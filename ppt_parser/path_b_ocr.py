"""Path B: Send slide JPEG to OCR API and retrieve extracted text."""
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
# LightOnOCR response parser
# ---------------------------------------------------------------------------

def _parse_lighton_response(data: Dict[str, Any], slide_num: int) -> OCRResult:
    """
    Parse LightOnOCR JSON response.

    Expected schema (flexible – handles both flat and nested formats):
      { "text": "...", "confidence": 0.95, "words": [{...}], ... }
    or
      { "outputs": [{"text": "...", "confidence": 0.95}] }
    """
    # Try outputs array first (batch format)
    if "outputs" in data and isinstance(data["outputs"], list) and data["outputs"]:
        item = data["outputs"][0]
    else:
        item = data

    text = item.get("text") or item.get("recognized_text") or item.get("result") or ""
    confidence = float(item.get("confidence", item.get("score", 1.0)))
    words: List[Dict[str, Any]] = item.get("words", item.get("word_boxes", []))

    return OCRResult(
        slide_num=slide_num,
        text=str(text),
        confidence=confidence,
        word_boxes=words,
        raw_response=data,
    )


def _parse_generic_response(data: Dict[str, Any], slide_num: int) -> OCRResult:
    """Fallback parser for plain-text OCR responses."""
    if isinstance(data, str):
        return OCRResult(slide_num=slide_num, text=data, confidence=1.0)
    text = (
        data.get("text")
        or data.get("result")
        or data.get("recognized_text")
        or json.dumps(data)
    )
    confidence = float(data.get("confidence", data.get("score", 1.0)))
    return OCRResult(
        slide_num=slide_num,
        text=str(text),
        confidence=confidence,
        raw_response=data,
    )


# ---------------------------------------------------------------------------
# HTTP helper
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_ocr(image_path: Path, slide_num: int, cfg: Config) -> OCRResult:
    """
    Call OCR API for the given slide image.

    Caches results in cfg.cache_dir to avoid redundant API calls.
    """
    img_b64 = encode_image_base64(image_path)
    cache_key = f"ocr_{content_sha256(img_b64)[:16]}_slide{slide_num}"

    cached = load_cache(cfg.cache_dir, cache_key)
    if cached:
        logger.debug("OCR cache hit for slide %d", slide_num)
        parser = (
            _parse_lighton_response
            if cfg.ocr_response_format == "lighton"
            else _parse_generic_response
        )
        return parser(cached, slide_num)

    logger.info("Running OCR for slide %d …", slide_num)
    payload = _build_ocr_payload(img_b64, image_path)

    def _call() -> Dict[str, Any]:
        return _post_json(cfg.ocr_api.url, payload, cfg.ocr_api)

    raw = retry_with_backoff(_call, max_retries=cfg.ocr_api.max_retries)
    save_cache(cfg.cache_dir, cache_key, raw)

    if cfg.ocr_response_format == "lighton":
        return _parse_lighton_response(raw, slide_num)
    return _parse_generic_response(raw, slide_num)


def _build_ocr_payload(img_b64: str, image_path: Path) -> Dict[str, Any]:
    """
    Build the request payload for LightOnOCR.

    LightOnOCR typically accepts:
      POST /ocr
      { "image": "<base64>", "lang": "auto" }
    Adjust here if the user's endpoint differs.
    """
    return {
        "image": img_b64,
        "lang": "auto",
        "filename": image_path.name,
    }


def run_ocr_batch(
    image_paths: List[Path], cfg: Config
) -> List[Optional[OCRResult]]:
    """Run OCR for a list of slide images (sequentially, with caching)."""
    results: List[Optional[OCRResult]] = []
    for idx, img_path in enumerate(image_paths, start=1):
        try:
            results.append(run_ocr(img_path, idx, cfg))
        except Exception as exc:
            logger.error("OCR failed for slide %d: %s", idx, exc)
            results.append(None)
    return results
