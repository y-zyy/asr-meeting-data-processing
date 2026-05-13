"""Path C: Extract text from PDF pages via OpenDataLoader API.

OpenDataLoader performs direct PDF text extraction (e.g. embedded font-based parsing,
layout-aware extraction), which is more accurate than image-based OCR for text-embedded
PDFs.  Results are used as the primary text source in the VLM prompt, taking precedence
over OCR output.

API contract (HTTP POST to OPENDATALOADER_API_URL):
  Request JSON:
    {
      "pdf_base64": "<base64-encoded PDF bytes>",
      "page_num": 1,          # 1-indexed
      "model": "opendataloader"
    }
  Response JSON:
    {
      "text": "<extracted text for the page>",
      "page_num": 1
    }
  OR OpenAI-compatible:
    {
      "choices": [{ "message": { "content": "<extracted text>" } }]
    }
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import APIConfig, Config
from .models import OpenDataLoaderResult
from .utils import content_sha256, load_cache, retry_with_backoff, save_cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request builder
# ---------------------------------------------------------------------------

def _read_pdf_base64(pdf_path: Path) -> str:
    """Read a PDF file and return its base64-encoded content."""
    with open(pdf_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _build_payload(pdf_base64: str, page_num: int, cfg: Config) -> Dict[str, Any]:
    return {
        "model": cfg.opendataloader_model,
        "pdf_base64": pdf_base64,
        "page_num": page_num,
        "max_tokens": cfg.opendataloader_max_tokens,
    }


# ---------------------------------------------------------------------------
# Response parser
# ---------------------------------------------------------------------------

def _parse_response(raw: Dict[str, Any], page_num: int) -> OpenDataLoaderResult:
    """Extract text from the OpenDataLoader API response.

    Supports both a custom {text: ...} format and the OpenAI-compatible
    choices[0].message.content format.
    """
    # Custom format
    if "text" in raw:
        text = raw["text"] or ""
        return OpenDataLoaderResult(
            slide_num=page_num,
            text=str(text).strip(),
            confidence=1.0,
            raw_response=raw,
        )

    # OpenAI-compatible format
    choices = raw.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict)
            )
        return OpenDataLoaderResult(
            slide_num=page_num,
            text=str(content).strip(),
            confidence=1.0,
            raw_response=raw,
        )

    logger.warning("Page %d: OpenDataLoader response has no recognised text field", page_num)
    return OpenDataLoaderResult(slide_num=page_num, text="", confidence=0.0, raw_response=raw)


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

def run_opendataloader(pdf_path: Path, page_num: int, cfg: Config) -> OpenDataLoaderResult:
    """Call the OpenDataLoader API for one PDF page.

    Results are cached in cfg.cache_dir keyed by (pdf content hash, page number)
    so repeated runs on the same file skip the API call.
    """
    pdf_b64 = _read_pdf_base64(pdf_path)
    cache_key = f"odl_{content_sha256(pdf_b64)[:16]}_page{page_num}"

    cached = load_cache(cfg.cache_dir, cache_key)
    if cached:
        logger.debug("OpenDataLoader cache hit for page %d", page_num)
        return _parse_response(cached, page_num)

    logger.info("Running OpenDataLoader for page %d …", page_num)
    payload = _build_payload(pdf_b64, page_num, cfg)

    def _call() -> Dict[str, Any]:
        return _post_json(cfg.opendataloader_api.url, payload, cfg.opendataloader_api)

    raw = retry_with_backoff(_call, max_retries=cfg.opendataloader_api.max_retries)
    save_cache(cfg.cache_dir, cache_key, raw)
    return _parse_response(raw, page_num)


def run_opendataloader_batch(
    pdf_path: Path,
    num_pages: int,
    cfg: Config,
) -> List[Optional[OpenDataLoaderResult]]:
    """Run OpenDataLoader for every page in the PDF sequentially."""
    results: List[Optional[OpenDataLoaderResult]] = []
    for page_num in range(1, num_pages + 1):
        try:
            results.append(run_opendataloader(pdf_path, page_num, cfg))
        except Exception as exc:
            logger.error("OpenDataLoader failed for page %d: %s", page_num, exc)
            results.append(None)
    return results
