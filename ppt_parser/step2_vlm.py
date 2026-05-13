"""Step 2: Send slide context to Gemma4 VLM and retrieve structured Markdown."""
from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from .config import APIConfig, Config
from .models import OCRResult, OpenDataLoaderResult, SlideXMLStructure, VLMResult
from .step1_geometry import slide_to_xml_summary
from .utils import content_sha256, encode_image_base64, load_cache, retry_with_backoff, save_cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert presentation analyst. Your task is to convert a slide into \
well-formatted Markdown. You will receive:
1. The slide image
2. OpenDataLoader-extracted text — direct PDF text extraction, highest accuracy
3. OCR-extracted text — image-based recognition, supplementary reference only
4. An XML summary of the slide's structural elements, positions, and relationships

**Text source priority (highest → lowest):**
1. OpenDataLoader text: Use this as the primary and most accurate text source.
   Prefer its wording, spelling, and content over all other sources.
2. OCR text: Use only to fill gaps not covered by OpenDataLoader, or to help
   infer spatial/visual layout that OpenDataLoader may miss.
3. Slide image: Use for visual structure, diagrams, charts, and any content
   not captured by text extraction.

Output ONLY the Markdown content for this single slide. Do not include \
surrounding commentary.

Rules:
- Use # for the slide title, ## for section headings
- Render tables as GitHub-flavoured Markdown tables
- Represent flowcharts/diagrams using ASCII art or descriptive flow notation: [Box] → [Box]
- For hierarchical structures use nested lists
- For arrow relationships shown in the XML <arrows> section, represent as: shape_text → shape_text
- Preserve all text content accurately — prioritise OpenDataLoader wording
- Do not invent content not present in the slide
"""

_USER_PROMPT_TEMPLATE = """\
## Slide {slide_num}

### XML Structure:
```xml
{xml_summary}
```

### OpenDataLoader Extracted Text (Primary — most accurate):
{opendataloader_text}

### OCR Extracted Text (Supplementary — use to fill gaps only):
{ocr_text}

Please generate the Markdown representation for this slide.
Prioritise the OpenDataLoader text for all textual content.
"""


def build_vlm_messages(
    slide_num: int,
    image_b64: str,
    xml_summary: str,
    ocr_text: str,
    opendataloader_text: str = "",
) -> list:
    """Build OpenAI-compatible chat messages with a vision payload."""
    user_text = _USER_PROMPT_TEMPLATE.format(
        slide_num=slide_num,
        xml_summary=xml_summary,
        opendataloader_text=opendataloader_text or "(no OpenDataLoader text available)",
        ocr_text=ocr_text or "(no OCR text available)",
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                },
                {"type": "text", "text": user_text},
            ],
        },
    ]


# ---------------------------------------------------------------------------
# HTTP helper (OpenAI-compatible endpoint)
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: Dict[str, Any], api_cfg: APIConfig) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_cfg.api_key:
        headers["Authorization"] = f"Bearer {api_cfg.api_key}"
    headers.update(api_cfg.extra_headers)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=api_cfg.timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_markdown(raw: Dict[str, Any]) -> str:
    """Extract the assistant message content from an OpenAI-compatible response."""
    # Standard: choices[0].message.content
    choices = raw.get("choices", [])
    if choices:
        msg = choices[0].get("message", {})
        content = msg.get("content", "")
        if isinstance(content, list):
            # Multi-part content – concatenate text parts
            return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
        return str(content)

    # Fallback: direct "text" or "response" field
    return str(raw.get("text") or raw.get("response") or raw.get("output") or "")


def _estimate_confidence(raw: Dict[str, Any]) -> float:
    """Heuristically estimate response confidence (0–1)."""
    choices = raw.get("choices", [])
    if choices:
        logprobs = choices[0].get("logprobs")
        if logprobs and isinstance(logprobs, dict):
            token_logprobs = logprobs.get("token_logprobs", [])
            if token_logprobs:
                import math
                avg_lp = sum(lp for lp in token_logprobs if lp is not None) / len(token_logprobs)
                return round(min(1.0, math.exp(avg_lp)), 3)
    return 1.0  # default: assume confident


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_vlm(
    image_path: Path,
    slide_num: int,
    xml_structure: Optional[SlideXMLStructure],
    ocr_result: Optional[OCRResult],
    cfg: Config,
    opendataloader_result: Optional[OpenDataLoaderResult] = None,
) -> VLMResult:
    """Call the VLM API for the given slide.

    Inputs are ensembled: OpenDataLoader text (primary) + OCR text (supplementary)
    + XML structure summary are all passed to the VLM together with the slide image.
    The VLM prompt instructs the model to prioritise OpenDataLoader text over OCR.

    Uses a cache keyed by (image hash, xml summary hash, odl text hash) to avoid
    redundant calls.
    """
    image_b64 = encode_image_base64(image_path)
    xml_summary = slide_to_xml_summary(xml_structure) if xml_structure else ""
    ocr_text = ocr_result.text if ocr_result else ""
    odl_text = opendataloader_result.text if opendataloader_result else ""

    cache_key = (
        f"vlm_{content_sha256(image_b64[:200], xml_summary[:200], odl_text[:200])[:16]}"
        f"_slide{slide_num}"
    )
    cached = load_cache(cfg.cache_dir, cache_key)
    if cached:
        logger.debug("VLM cache hit for slide %d", slide_num)
        md = _extract_markdown(cached)
        return VLMResult(
            slide_num=slide_num,
            markdown=md,
            confidence=_estimate_confidence(cached),
            raw_response=cached,
        )

    logger.info("Running VLM for slide %d …", slide_num)
    messages = build_vlm_messages(
        slide_num, image_b64, xml_summary, ocr_text, opendataloader_text=odl_text
    )
    payload = {
        "model": cfg.vlm_model,
        "messages": messages,
        "max_tokens": cfg.vlm_max_tokens,
        "temperature": cfg.vlm_temperature,
    }

    def _call() -> Dict[str, Any]:
        return _post_json(cfg.vlm_api.url, payload, cfg.vlm_api)

    raw = retry_with_backoff(_call, max_retries=cfg.vlm_api.max_retries)
    save_cache(cfg.cache_dir, cache_key, raw)

    md = _extract_markdown(raw)
    return VLMResult(
        slide_num=slide_num,
        markdown=md,
        confidence=_estimate_confidence(raw),
        raw_response=raw,
    )
