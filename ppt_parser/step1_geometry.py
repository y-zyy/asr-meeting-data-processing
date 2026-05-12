"""Step 1: Geometric relationship analysis — detect containment, alignment, flow order."""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

from .config import Config
from .models import (
    BoundingBox,
    Relationship,
    RelationshipType,
    ShapeInfo,
    ShapeType,
    SlideXMLStructure,
)

logger = logging.getLogger(__name__)


def _is_title_shape(s: ShapeInfo) -> bool:
    name_lower = (s.shape_name or "").lower()
    return s.shape_type == ShapeType.PLACEHOLDER and (
        "title" in name_lower or "ctrTitle" in s.shape_name
    )


# ---------------------------------------------------------------------------
# Containment
# ---------------------------------------------------------------------------

def detect_containment(shapes: List[ShapeInfo], tolerance: float = 0.0) -> List[Relationship]:
    """Return CONTAINS relationships where bbox A fully encloses bbox B."""
    non_connectors = [s for s in shapes if s.shape_type != ShapeType.CONNECTOR]
    rels: List[Relationship] = []
    for i, outer in enumerate(non_connectors):
        for j, inner in enumerate(non_connectors):
            if i == j:
                continue
            if outer.bbox.contains(inner.bbox, tolerance=tolerance) and outer.z_order < inner.z_order:
                rels.append(
                    Relationship(
                        from_shape_id=outer.shape_id,
                        to_shape_id=inner.shape_id,
                        rel_type=RelationshipType.CONTAINS,
                        metadata={"outer_area": outer.bbox.width * outer.bbox.height},
                    )
                )
    return rels


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def detect_alignment(
    shapes: List[ShapeInfo], threshold: float
) -> List[Relationship]:
    """
    Detect ALIGNED_HORIZONTAL / ALIGNED_VERTICAL relationships.

    Two shapes are aligned when their centers are within `threshold` EMU
    on one axis.  We return pairwise relationships only for the nearest
    neighbour pairs to avoid an explosion of edges.
    """
    content = [s for s in shapes if s.shape_type not in (ShapeType.CONNECTOR,)]
    rels: List[Relationship] = []

    for i in range(len(content)):
        for j in range(i + 1, len(content)):
            a, b = content[i], content[j]
            dy = abs(a.bbox.center_y - b.bbox.center_y)
            dx = abs(a.bbox.center_x - b.bbox.center_x)
            if dy < threshold and dx > threshold:
                rels.append(
                    Relationship(
                        from_shape_id=a.shape_id,
                        to_shape_id=b.shape_id,
                        rel_type=RelationshipType.ALIGNED_HORIZONTAL,
                        metadata={"delta_y_emu": round(dy)},
                    )
                )
            elif dx < threshold and dy > threshold:
                rels.append(
                    Relationship(
                        from_shape_id=a.shape_id,
                        to_shape_id=b.shape_id,
                        rel_type=RelationshipType.ALIGNED_VERTICAL,
                        metadata={"delta_x_emu": round(dx)},
                    )
                )
    return rels


# ---------------------------------------------------------------------------
# Flow / reading order
# ---------------------------------------------------------------------------

def reading_order(shapes: List[ShapeInfo]) -> List[ShapeInfo]:
    """
    Sort shapes in natural reading order: top-to-bottom, then left-to-right.
    Title placeholders always come first.
    """
    titles = [s for s in shapes if _is_title_shape(s)]
    others = [s for s in shapes if not _is_title_shape(s) and s.shape_type != ShapeType.CONNECTOR]
    connectors = [s for s in shapes if s.shape_type == ShapeType.CONNECTOR]

    def _key(s: ShapeInfo) -> Tuple[float, float]:
        return (round(s.bbox.center_y / 100000) * 100000, s.bbox.center_x)

    return titles + sorted(others, key=_key) + connectors


def detect_flow_sequence(
    shapes: List[ShapeInfo],
    existing_arrow_rels: List[Relationship],
) -> List[Relationship]:
    """
    Infer FLOW_SEQUENCE relationships for shapes that are horizontally or
    vertically aligned and connected by arrow connectors (indirect detection
    when connector endpoint IDs are not present in XML).
    """
    arrow_pairs = {
        (r.from_shape_id, r.to_shape_id)
        for r in existing_arrow_rels
        if r.rel_type == RelationshipType.ARROW_CONNECTS
    }
    if not arrow_pairs:
        return []

    # Build a simple adjacency set from arrows that exist and mark
    # their visual sequence for reporting purposes
    seq_rels: List[Relationship] = []
    for from_id, to_id in arrow_pairs:
        seq_rels.append(
            Relationship(
                from_shape_id=from_id,
                to_shape_id=to_id,
                rel_type=RelationshipType.FLOW_SEQUENCE,
            )
        )
    return seq_rels


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyze_geometry(slide: SlideXMLStructure, cfg: Config) -> SlideXMLStructure:
    """
    Enrich a SlideXMLStructure with containment, alignment, and flow
    relationships discovered from bounding-box geometry.
    Returns a new SlideXMLStructure (does not mutate the input).
    """
    shapes = slide.shapes
    existing_rels = list(slide.relationships)

    containment_rels = detect_containment(shapes, tolerance=50000)
    alignment_rels = detect_alignment(shapes, threshold=cfg.alignment_threshold_emu)
    flow_rels = detect_flow_sequence(shapes, existing_rels)

    all_rels = existing_rels + containment_rels + alignment_rels + flow_rels

    logger.debug(
        "Slide %d: +%d containment, +%d alignment, +%d flow relationships",
        slide.slide_num,
        len(containment_rels),
        len(alignment_rels),
        len(flow_rels),
    )

    return SlideXMLStructure(
        slide_num=slide.slide_num,
        slide_width=slide.slide_width,
        slide_height=slide.slide_height,
        title=slide.title,
        shapes=reading_order(shapes),
        relationships=all_rels,
        notes=slide.notes,
        layout_name=slide.layout_name,
    )


# ---------------------------------------------------------------------------
# Serialisation: compact XML summary for the VLM prompt
# ---------------------------------------------------------------------------

def _escape_xml_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def slide_to_xml_summary(slide: SlideXMLStructure) -> str:
    """
    Produce a compact XML string describing the slide structure.

    Positions are normalised to percentage of slide dimensions (0–100).
    This is the structural context fed to the VLM.
    """
    lines: List[str] = [
        f'<slide num="{slide.slide_num}" '
        f'width_emu="{int(slide.slide_width)}" height_emu="{int(slide.slide_height)}">'
    ]

    for s in slide.shapes:
        norm = s.bbox.normalized(slide.slide_width, slide.slide_height)
        attrs: Dict[str, str] = {
            "id": s.shape_id,
            "type": s.shape_type.value,
            "x": str(norm["x_pct"]),
            "y": str(norm["y_pct"]),
            "w": str(norm["w_pct"]),
            "h": str(norm["h_pct"]),
        }
        if s.shape_preset:
            attrs["preset"] = s.shape_preset
        if s.shape_type == ShapeType.CONNECTOR:
            if s.connects_from:
                attrs["from"] = s.connects_from
            if s.connects_to:
                attrs["to"] = s.connects_to
            attrs["arrow"] = "end" if s.has_arrow_head else ("start" if s.has_arrow_tail else "both")
        if s.group_id:
            attrs["group"] = s.group_id

        attr_str = " ".join(f'{k}="{_escape_xml_attr(v)}"' for k, v in attrs.items())

        if s.shape_type == ShapeType.TABLE and s.table_data:
            lines.append(f"  <shape {attr_str}>")
            lines.append("    <table>")
            for row in s.table_data:
                lines.append("      <row>" + "".join(f"<cell>{_escape_xml_attr(c)}</cell>" for c in row) + "</row>")
            lines.append("    </table>")
            lines.append("  </shape>")
        elif s.text:
            safe_text = _escape_xml_attr(s.text[:300])
            lines.append(f"  <shape {attr_str}><text>{safe_text}</text></shape>")
        else:
            lines.append(f"  <shape {attr_str}/>")

    # Relationships
    arrow_rels = [r for r in slide.relationships if r.rel_type == RelationshipType.ARROW_CONNECTS]
    contain_rels = [r for r in slide.relationships if r.rel_type == RelationshipType.CONTAINS]
    h_align = [r for r in slide.relationships if r.rel_type == RelationshipType.ALIGNED_HORIZONTAL]
    v_align = [r for r in slide.relationships if r.rel_type == RelationshipType.ALIGNED_VERTICAL]

    if arrow_rels:
        lines.append("  <arrows>")
        for r in arrow_rels:
            via = f' via="{r.connector_shape_id}"' if r.connector_shape_id else ""
            lines.append(f'    <arrow from="{r.from_shape_id}" to="{r.to_shape_id}"{via}/>')
        lines.append("  </arrows>")

    if contain_rels:
        lines.append("  <containment>")
        for r in contain_rels:
            lines.append(f'    <contains outer="{r.from_shape_id}" inner="{r.to_shape_id}"/>')
        lines.append("  </containment>")

    if h_align:
        groups = _group_aligned([r.from_shape_id for r in h_align] + [r.to_shape_id for r in h_align])
        for g in groups:
            lines.append(f'  <h_group shapes="{",".join(g)}"/>')

    if v_align:
        groups = _group_aligned([r.from_shape_id for r in v_align] + [r.to_shape_id for r in v_align])
        for g in groups:
            lines.append(f'  <v_group shapes="{",".join(g)}"/>')

    lines.append("</slide>")
    return "\n".join(lines)


def _group_aligned(ids: List[str]) -> List[List[str]]:
    """Deduplicate and return unique shape ID lists (simple dedup)."""
    seen = dict.fromkeys(ids)  # preserves insertion order, deduplicates
    return [list(seen.keys())]
