"""Path C: Extract text from PDF pages using the opendataloader_pdf package.

opendataloader_pdf provides layout-aware, font-based PDF text extraction which
is more accurate than image-based OCR for text-embedded PDFs.  The extracted
text is used as the primary (highest-priority) text source in the VLM prompt.

Usage of the underlying library:
    from opendataloader_pdf import PDFConverter
    converter = PDFConverter()
    result = converter.convert(pdf_path, format='json')   # structured per-page
    result = converter.convert(pdf_path, format='md')     # whole-doc markdown
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from .models import OpenDataLoaderResult
from .utils import content_sha256, load_cache, save_cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Library import helper
# ---------------------------------------------------------------------------

def _try_import() -> Optional[object]:
    try:
        from opendataloader_pdf import PDFConverter  # type: ignore
        return PDFConverter
    except ImportError:
        return None


# ---------------------------------------------------------------------------
# Per-page text extraction
# ---------------------------------------------------------------------------

def _extract_pages_from_json(raw_json) -> Dict[int, str]:
    """Parse the JSON output of PDFConverter into a {page_num: text} dict.

    Tries common JSON structures that layout-aware converters produce.
    Falls back to treating the whole content as page 1 if unrecognised.
    """
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except json.JSONDecodeError:
            return {1: raw_json.strip()}

    # Structure: list of page objects
    if isinstance(raw_json, list):
        pages: Dict[int, str] = {}
        for item in raw_json:
            if isinstance(item, dict):
                num = item.get("page") or item.get("page_num") or item.get("page_number")
                text = item.get("text") or item.get("content") or item.get("markdown") or ""
                if num is not None:
                    pages[int(num)] = str(text).strip()
        if pages:
            return pages

    # Structure: dict with "pages" key
    if isinstance(raw_json, dict):
        page_list = raw_json.get("pages") or raw_json.get("content")
        if isinstance(page_list, list):
            pages = {}
            for item in page_list:
                if isinstance(item, dict):
                    num = item.get("page") or item.get("page_num") or item.get("page_number")
                    text = item.get("text") or item.get("content") or item.get("markdown") or ""
                    if num is not None:
                        pages[int(num)] = str(text).strip()
            if pages:
                return pages
        # Single-page or flat dict
        text = raw_json.get("text") or raw_json.get("content") or raw_json.get("markdown") or ""
        if text:
            return {1: str(text).strip()}

    return {}


def _split_markdown_by_page(md_text: str) -> Dict[int, str]:
    """Heuristically split a full-document markdown string into pages.

    Many converters embed page markers like '<!-- page N -->' or '---'.
    Falls back to returning the whole text as page 1.
    """
    import re

    # Try explicit page markers: <!-- page N --> or <<<Page N>>>
    marker_re = re.compile(
        r"<!--\s*[Pp]age\s*(\d+)\s*-->|<<<\s*[Pp]age\s*(\d+)\s*>>>", re.IGNORECASE
    )
    parts = marker_re.split(md_text)
    if len(parts) > 1:
        pages: Dict[int, str] = {}
        i = 0
        page_num = None
        while i < len(parts):
            chunk = parts[i]
            # marker_re produces 3 groups per match (full, group1, group2)
            if i % 3 == 0 and i > 0:
                text = parts[i].strip() if i < len(parts) else ""
                if page_num and text:
                    pages[page_num] = text
            elif i % 3 == 1 and parts[i] is not None:
                page_num = int(parts[i])
            elif i % 3 == 2 and parts[i] is not None:
                page_num = int(parts[i])
            i += 1
        if pages:
            return pages

    # No markers found — return whole document as page 1
    return {1: md_text.strip()}


def _convert_pdf(pdf_path: Path) -> Dict[int, str]:
    """Call PDFConverter and return a {page_num: text} mapping."""
    PDFConverter = _try_import()
    if PDFConverter is None:
        logger.warning(
            "opendataloader_pdf not installed — OpenDataLoader step skipped. "
            "Install with: pip install opendataloader_pdf"
        )
        return {}

    converter = PDFConverter()

    # Prefer JSON output (structured, per-page) over markdown
    try:
        raw = converter.convert(str(pdf_path), format="json")
        pages = _extract_pages_from_json(raw)
        if pages:
            logger.debug("OpenDataLoader: got %d pages via JSON format", len(pages))
            return pages
    except Exception as exc:
        logger.debug("OpenDataLoader JSON format failed (%s), trying md", exc)

    # Fall back to markdown output
    try:
        md = converter.convert(str(pdf_path), format="md")
        pages = _split_markdown_by_page(md if isinstance(md, str) else str(md))
        logger.debug("OpenDataLoader: got %d pages via markdown format", len(pages))
        return pages
    except Exception as exc:
        logger.warning("OpenDataLoader conversion failed for %s: %s", pdf_path, exc)
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_opendataloader_batch(
    pdf_path: Path,
    num_pages: int,
    cfg,  # Config — avoid circular import at type-check time
) -> List[Optional[OpenDataLoaderResult]]:
    """Extract text from every page of a PDF using opendataloader_pdf.

    Results are cached in cfg.cache_dir keyed by PDF content hash so that
    repeated runs on the same file skip re-conversion.
    """
    # Cache the full-document conversion result
    pdf_bytes = pdf_path.read_bytes()
    cache_key = f"odl_{content_sha256(pdf_bytes.hex()[:400])[:16]}_allpages"

    cached = load_cache(cfg.cache_dir, cache_key)
    if cached:
        logger.debug("OpenDataLoader cache hit for %s", pdf_path.name)
        pages: Dict[int, str] = {int(k): v for k, v in cached.items()}
    else:
        logger.info("Running OpenDataLoader on %s …", pdf_path.name)
        pages = _convert_pdf(pdf_path)
        if pages:
            save_cache(cfg.cache_dir, cache_key, {str(k): v for k, v in pages.items()})

    results: List[Optional[OpenDataLoaderResult]] = []
    for page_num in range(1, num_pages + 1):
        text = pages.get(page_num, "")
        if text:
            results.append(
                OpenDataLoaderResult(slide_num=page_num, text=text, confidence=1.0)
            )
        else:
            results.append(None)

    return results
