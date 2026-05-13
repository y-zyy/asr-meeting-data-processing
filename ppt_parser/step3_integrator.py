"""Step 3: Combine per-slide results → output.md + analysis_report.json."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import OCRResult, OpenDataLoaderResult, ShapeType, SlideResult, SlideXMLStructure, VLMResult
from .step1_geometry import slide_to_xml_summary

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fallback Markdown generator (used when VLM is unavailable)
# ---------------------------------------------------------------------------

def _table_to_md(table_data: List[List[str]]) -> str:
    if not table_data:
        return ""
    header = table_data[0]
    sep = ["---"] * len(header)
    rows = [header, sep] + table_data[1:]
    return "\n".join("| " + " | ".join(str(c) for c in row) + " |" for row in rows)


def _xml_structure_to_fallback_md(slide: SlideXMLStructure) -> str:
    """Generate basic Markdown from XML structure without VLM."""
    lines: List[str] = []

    if slide.title:
        lines.append(f"# {slide.title}\n")

    from .models import Relationship, RelationshipType

    arrow_map: Dict[str, List[str]] = {}
    for r in slide.relationships:
        if r.rel_type == RelationshipType.ARROW_CONNECTS:
            from_shape = slide.get_shape_by_id(r.from_shape_id)
            to_shape = slide.get_shape_by_id(r.to_shape_id)
            if from_shape and to_shape and from_shape.text and to_shape.text:
                arrow_map.setdefault(r.from_shape_id, []).append(r.to_shape_id)

    # Render shapes in reading order
    rendered: set = set()
    for s in slide.shapes:
        if s.shape_type == ShapeType.CONNECTOR:
            continue
        if s.shape_id in rendered:
            continue

        if s.shape_type == ShapeType.TABLE and s.table_data:
            lines.append(_table_to_md(s.table_data))
            lines.append("")
        elif s.text:
            # Check if this starts a flow chain
            if s.shape_id in arrow_map:
                chain = _build_chain(s.shape_id, arrow_map, slide)
                lines.append(" → ".join(chain))
                lines.append("")
                for node_id in _flatten_chain(s.shape_id, arrow_map):
                    rendered.add(node_id)
            else:
                # Regular text – use paragraph structure
                if s.text == slide.title:
                    continue  # already written as #
                paras = s.text_paragraphs or [s.text]
                for p in paras:
                    if p.strip():
                        lines.append(p.strip())
                lines.append("")
        elif s.shape_type == ShapeType.IMAGE:
            lines.append(f"*[Image: {s.shape_name or 'embedded image'}]*\n")
        elif s.shape_type in (ShapeType.CHART, ShapeType.SMART_ART):
            lines.append(f"*[{s.shape_type.value.replace('_', ' ').title()}: {s.shape_name or ''}]*\n")

    if slide.notes:
        lines.append("\n> **Speaker notes:** " + slide.notes)

    return "\n".join(lines).strip()


def _build_chain(start_id: str, arrow_map: Dict[str, List[str]], slide: SlideXMLStructure) -> List[str]:
    """Build a linear flow chain starting from start_id."""
    chain = []
    current = start_id
    visited = set()
    while current and current not in visited:
        shape = slide.get_shape_by_id(current)
        chain.append(shape.text if shape and shape.text else current)
        visited.add(current)
        next_ids = arrow_map.get(current, [])
        current = next_ids[0] if next_ids else None
    return chain


def _flatten_chain(start_id: str, arrow_map: Dict[str, List[str]]) -> List[str]:
    ids = []
    current = start_id
    visited = set()
    while current and current not in visited:
        ids.append(current)
        visited.add(current)
        next_ids = arrow_map.get(current, [])
        current = next_ids[0] if next_ids else None
    return ids


# ---------------------------------------------------------------------------
# Per-slide Markdown selection
# ---------------------------------------------------------------------------

def select_markdown(result: SlideResult) -> str:
    """Choose the best available Markdown for a slide.

    Priority: VLM > XML fallback > OpenDataLoader plain text > OCR plain text > empty.
    VLM already incorporates OpenDataLoader + OCR + image, so it is preferred when
    available.  When VLM is unavailable the OpenDataLoader text (more accurate than OCR)
    is preferred over raw OCR output.
    """
    if result.vlm_result and result.vlm_result.markdown.strip():
        return result.vlm_result.markdown.strip()
    if result.xml_structure:
        logger.debug("Slide %d: VLM unavailable, using XML fallback", result.slide_num)
        return _xml_structure_to_fallback_md(result.xml_structure)
    if result.opendataloader_result and result.opendataloader_result.text.strip():
        logger.debug("Slide %d: Using OpenDataLoader text", result.slide_num)
        return result.opendataloader_result.text.strip()
    if result.ocr_result and result.ocr_result.text.strip():
        logger.debug("Slide %d: Using raw OCR text", result.slide_num)
        return result.ocr_result.text.strip()
    return f"*[Slide {result.slide_num}: no content extracted]*"


# ---------------------------------------------------------------------------
# Report serialisation helpers
# ---------------------------------------------------------------------------

def _shape_to_dict(s) -> Dict[str, Any]:
    return {
        "id": s.shape_id,
        "name": s.shape_name,
        "type": s.shape_type.value,
        "text": s.text,
        "bbox": {
            "x": s.bbox.x, "y": s.bbox.y,
            "w": s.bbox.width, "h": s.bbox.height,
        },
        "connects_from": s.connects_from,
        "connects_to": s.connects_to,
        "has_arrow_head": s.has_arrow_head,
        "has_arrow_tail": s.has_arrow_tail,
        "fill_color": s.fill_color,
        "shape_preset": s.shape_preset,
        "group_id": s.group_id,
    }


def _rel_to_dict(r) -> Dict[str, Any]:
    return {
        "from": r.from_shape_id,
        "to": r.to_shape_id,
        "type": r.rel_type.value,
        "connector_id": r.connector_shape_id,
        "metadata": r.metadata,
    }


def _slide_result_to_dict(result: SlideResult) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "slide_num": result.slide_num,
        "image_path": result.image_path,
        "error": result.error,
    }
    if result.xml_structure:
        xs = result.xml_structure
        d["xml_structure"] = {
            "title": xs.title,
            "slide_width_emu": xs.slide_width,
            "slide_height_emu": xs.slide_height,
            "shape_count": len(xs.shapes),
            "shapes": [_shape_to_dict(s) for s in xs.shapes],
            "relationships": [_rel_to_dict(r) for r in xs.relationships],
            "notes": xs.notes,
            "xml_summary": slide_to_xml_summary(xs),
        }
    if result.opendataloader_result:
        d["opendataloader"] = {
            "text": result.opendataloader_result.text,
            "confidence": result.opendataloader_result.confidence,
        }
    if result.ocr_result:
        d["ocr"] = {
            "text": result.ocr_result.text,
            "confidence": result.ocr_result.confidence,
            "word_boxes": result.ocr_result.word_boxes,
        }
    if result.vlm_result:
        d["vlm"] = {
            "markdown": result.vlm_result.markdown,
            "confidence": result.vlm_result.confidence,
        }
    d["final_markdown"] = result.final_markdown
    return d


# ---------------------------------------------------------------------------
# Main integration step
# ---------------------------------------------------------------------------

def integrate_results(
    results: List[SlideResult],
    output_dir: Path,
    pptx_name: str,
) -> None:
    """
    Combine all SlideResults into:
    - output.md
    - analysis_report.json
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Select final Markdown per slide
    for result in results:
        result.final_markdown = select_markdown(result)

    # --- output.md ---
    md_lines: List[str] = [
        f"# {pptx_name}\n",
        f"*Generated from `{pptx_name}` — {len(results)} slides*\n",
        "---\n",
    ]
    for result in results:
        md_lines.append(f"\n---\n\n<!-- Slide {result.slide_num} -->\n")
        md_lines.append(result.final_markdown)
        md_lines.append("")

    md_path = output_dir / "output.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    logger.info("Markdown saved: %s", md_path)

    # --- analysis_report.json ---
    report: Dict[str, Any] = {
        "source_file": pptx_name,
        "total_slides": len(results),
        "slides": [_slide_result_to_dict(r) for r in results],
    }
    report_path = output_dir / "analysis_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Report saved: %s", report_path)
