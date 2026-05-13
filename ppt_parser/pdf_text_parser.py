"""PDF text extraction — produces SlideXMLStructure-compatible objects per page.

Uses pypdf for pure-Python extraction.  Falls back gracefully when the library
is not installed (returns empty structures so OCR/VLM still run).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List, Optional

from .models import BoundingBox, ShapeInfo, ShapeType, SlideXMLStructure

logger = logging.getLogger(__name__)

# A4 / standard PDF page in points is variable; we use a fixed EMU stand-in so
# that geometry analysis (step1) can still reference normalised coordinates.
_DEFAULT_PAGE_WIDTH_EMU = 12192000   # same as PPTX 16:9 wide
_DEFAULT_PAGE_HEIGHT_EMU = 6858000


def _try_import_pypdf():
    try:
        import pypdf  # noqa: F401
        return pypdf
    except ImportError:
        return None


def _points_to_emu(points: float) -> float:
    """Convert PDF points (1/72 inch) to EMU (914400 per inch)."""
    return points * 914400 / 72


def _parse_page_pypdf(page, page_num: int, page_width_emu: float, page_height_emu: float) -> SlideXMLStructure:
    """Extract text from a single pypdf page object."""
    text = page.extract_text() or ""
    paragraphs = [line.strip() for line in text.splitlines() if line.strip()]
    full_text = "\n".join(paragraphs)

    shapes: List[ShapeInfo] = []
    if full_text:
        bbox = BoundingBox(x=0, y=0, width=page_width_emu, height=page_height_emu)
        shapes.append(
            ShapeInfo(
                shape_id="page_text",
                shape_name="page_text",
                shape_type=ShapeType.TEXT_BOX,
                bbox=bbox,
                text=full_text,
                text_paragraphs=paragraphs,
                z_order=0,
            )
        )

    title = paragraphs[0] if paragraphs else ""

    return SlideXMLStructure(
        slide_num=page_num,
        slide_width=page_width_emu,
        slide_height=page_height_emu,
        title=title,
        shapes=shapes,
        relationships=[],
        notes="",
    )


def parse_all_pages(pdf_path: Path) -> List[SlideXMLStructure]:
    """Extract text from every page in the PDF.

    Returns a list of SlideXMLStructure (one per page).
    Returns an empty list if pypdf is unavailable or extraction fails.
    """
    pypdf = _try_import_pypdf()
    if pypdf is None:
        logger.warning(
            "pypdf not installed — PDF text extraction skipped. "
            "Install with: pip install pypdf>=4.0"
        )
        return []

    try:
        reader = pypdf.PdfReader(str(pdf_path))
    except Exception as exc:
        logger.warning("Could not open PDF for text extraction: %s", exc)
        return []

    results: List[SlideXMLStructure] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            # Attempt to get real page dimensions (in points)
            media_box = page.mediabox
            width_emu = _points_to_emu(float(media_box.width))
            height_emu = _points_to_emu(float(media_box.height))
        except Exception:
            width_emu = _DEFAULT_PAGE_WIDTH_EMU
            height_emu = _DEFAULT_PAGE_HEIGHT_EMU

        try:
            structure = _parse_page_pypdf(page, i, width_emu, height_emu)
        except Exception as exc:
            logger.warning("Failed to extract text from page %d: %s", i, exc)
            structure = SlideXMLStructure(
                slide_num=i,
                slide_width=width_emu,
                slide_height=height_emu,
            )
        results.append(structure)

    logger.info("PDF text extraction: %d pages", len(results))
    return results
