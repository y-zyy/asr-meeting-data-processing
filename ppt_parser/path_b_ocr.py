"""Path B: Send slide JPEG to LightOnOCR (vllm, OpenAI-compatible) and retrieve text."""
from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import APIConfig, Config
from .models import OCRResult
from .utils import content_sha256, encode_image_base64, load_cache, retry_with_backoff, save_cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------

def _build_ocr_payload(image_b64: str, cfg: Config) -> Dict[str, Any]:
    """
    Build an OpenAI-compatible chat/completions request for LightOnOCR.

    LightOnOCR is served via vllm and accepts the standard vision message format:
      POST /v1/chat/completions
      {
        "model": "lightonai/LightOnOCR-2-1B",
        "messages": [{ "role": "user", "content": [{ "type": "image_url", ... }] }],
        ...
      }
    The model returns the OCR'd text directly as the assistant message content.
    No system prompt or additional instructions are needed.
    """
    return {
        "model": cfg.ocr_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_b64}",
                        },
                    }
                ],
            }
        ],
        "max_tokens": cfg.ocr_max_tokens,
        "temperature": cfg.ocr_temperature,
        "top_p": cfg.ocr_top_p,
    }


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(raw: Dict[str, Any], slide_num: int) -> OCRResult:
    """
    Extract OCR text from an OpenAI-compatible response.

    Response shape:
      { "choices": [{ "message": { "content": "<ocr text>" } }] }
    """
    choices = raw.get("choices", [])
    if not choices:
        logger.warning("Slide %d: OCR response has no choices field", slide_num)
        return OCRResult(slide_num=slide_num, text="", confidence=0.0, raw_response=raw)

    content = choices[0].get("message", {}).get("content", "")
    if isinstance(content, list):
        # Multi-part content — join text blocks
        content = "\n".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )

    # LightOnOCR does not return per-token logprobs by default; treat as full confidence.
    return OCRResult(
        slide_num=slide_num,
        text=content.strip(),
        confidence=1.0,
        raw_response=raw,
    )


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _post_json(url: str, payload: Dict[str, Any], api_cfg: APIConfig) -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    headers: Dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_cfg.api_key:
        headers["Authorization"] = f"Bearer {api_cfg.api_key}"
    headers.update(api_cfg.extra_headers)

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=api_cfg.timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_ocr(image_path: Path, slide_num: int, cfg: Config) -> OCRResult:
    """
    Call LightOnOCR for one slide image.

    Results are cached in cfg.cache_dir (keyed by image content hash)
    so repeated runs on the same file skip the API call.
    """
    image_b64 = encode_image_base64(image_path)
    cache_key = f"ocr_{content_sha256(image_b64)[:16]}_slide{slide_num}"

    cached = load_cache(cfg.cache_dir, cache_key)
    if cached:
        logger.debug("OCR cache hit for slide %d", slide_num)
        return _parse_response(cached, slide_num)

    logger.info("Running OCR for slide %d …", slide_num)
    payload = _build_ocr_payload(image_b64, cfg)

    def _call() -> Dict[str, Any]:
        return _post_json(cfg.ocr_api.url, payload, cfg.ocr_api)

    raw = retry_with_backoff(_call, max_retries=cfg.ocr_api.max_retries)
    save_cache(cfg.cache_dir, cache_key, raw)
    return _parse_response(raw, slide_num)


def run_ocr_batch(image_paths: List[Path], cfg: Config) -> List[Optional[OCRResult]]:
    """Run OCR for every slide image sequentially (with per-slide caching)."""
    results: List[Optional[OCRResult]] = []
    for idx, img_path in enumerate(image_paths, start=1):
        try:
            results.append(run_ocr(img_path, idx, cfg))
        except Exception as exc:
            logger.error("OCR failed for slide %d: %s", idx, exc)
            results.append(None)
    return results
