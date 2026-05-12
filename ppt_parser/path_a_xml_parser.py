"""Path A: Parse PPTX XML to extract structural information about shapes and relationships."""
from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from .models import (
    BoundingBox,
    Relationship,
    RelationshipType,
    ShapeInfo,
    ShapeType,
    SlideXMLStructure,
)

logger = logging.getLogger(__name__)

# Well-known PPTX namespace URIs
_KNOWN_NS: Dict[str, str] = {
    "a":   "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p":   "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r":   "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "mc":  "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "c":   "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "dgm": "http://schemas.openxmlformats.org/drawingml/2006/diagram",
}

# Reverse map: URI → prefix
_URI_TO_PREFIX: Dict[str, str] = {v: k for k, v in _KNOWN_NS.items()}

# Slide dimensions default (Wide 16:9): 12192000 × 6858000 EMU
_DEFAULT_SLIDE_WIDTH = 12192000
_DEFAULT_SLIDE_HEIGHT = 6858000


def _detect_namespaces(xml_text: str) -> Dict[str, str]:
    """Return {prefix: uri} map by scanning xmlns declarations in the XML text."""
    ns: Dict[str, str] = dict(_KNOWN_NS)
    for prefix, uri in re.findall(r'xmlns:(\w+)=["\']([^"\']+)["\']', xml_text):
        ns.setdefault(prefix, uri)
    return ns


def _q(ns: Dict[str, str], prefix: str, localname: str) -> str:
    """Build an ElementTree {uri}localname tag."""
    uri = ns.get(prefix, "")
    return f"{{{uri}}}{localname}" if uri else localname


def _text_of(element: Optional[ET.Element], ns: Dict[str, str]) -> Tuple[str, List[str]]:
    """Extract all text runs from a shape element; return (full_text, paragraphs)."""
    if element is None:
        return "", []

    paragraphs: List[str] = []
    txBody = element.find(f".//{_q(ns,'p','txBody')}")
    if txBody is None:
        txBody = element.find(f".//{_q(ns,'a','txBody')}")
    if txBody is None:
        return "", []

    for para in txBody.iter(_q(ns, "a", "p")):
        parts: List[str] = []
        for run in para.iter(_q(ns, "a", "t")):
            if run.text:
                parts.append(run.text)
        if parts:
            paragraphs.append("".join(parts))

    return "\n".join(paragraphs), paragraphs


def _bbox_from_xfrm(xfrm: Optional[ET.Element], ns: Dict[str, str]) -> Optional[BoundingBox]:
    """Parse <a:xfrm> element → BoundingBox in EMU."""
    if xfrm is None:
        return None
    off = xfrm.find(_q(ns, "a", "off"))
    ext = xfrm.find(_q(ns, "a", "ext"))
    if off is None or ext is None:
        return None
    try:
        return BoundingBox(
            x=float(off.get("x", 0)),
            y=float(off.get("y", 0)),
            width=float(ext.get("cx", 0)),
            height=float(ext.get("cy", 0)),
        )
    except (TypeError, ValueError):
        return None


def _shape_preset(spPr: Optional[ET.Element], ns: Dict[str, str]) -> Optional[str]:
    if spPr is None:
        return None
    prstGeom = spPr.find(_q(ns, "a", "prstGeom"))
    if prstGeom is not None:
        return prstGeom.get("prst")
    return None


def _color_hex(solidFill: Optional[ET.Element], ns: Dict[str, str]) -> Optional[str]:
    if solidFill is None:
        return None
    for tag in ("srgbClr", "sysClr"):
        el = solidFill.find(f".//{_q(ns,'a',tag)}")
        if el is not None:
            return el.get("val") or el.get("lastClr")
    return None


def _parse_sp(el: ET.Element, ns: Dict[str, str], z_order: int) -> Optional[ShapeInfo]:
    """Parse a <p:sp> (normal shape / text box / placeholder)."""
    nvSpPr = el.find(_q(ns, "p", "nvSpPr"))
    cNvPr = nvSpPr.find(_q(ns, "p", "cNvPr")) if nvSpPr is not None else None
    shape_id = cNvPr.get("id", str(z_order)) if cNvPr is not None else str(z_order)
    shape_name = cNvPr.get("name", "") if cNvPr is not None else ""

    # Determine placeholder type
    nvPr = nvSpPr.find(_q(ns, "p", "nvPr")) if nvSpPr is not None else None
    ph = nvPr.find(_q(ns, "p", "ph")) if nvPr is not None else None
    ph_type = ph.get("type", "") if ph is not None else ""

    spPr = el.find(_q(ns, "p", "spPr"))
    xfrm = spPr.find(_q(ns, "a", "xfrm")) if spPr is not None else None
    bbox = _bbox_from_xfrm(xfrm, ns)
    if bbox is None:
        return None

    text, paragraphs = _text_of(el, ns)

    # Shape type
    if ph is not None:
        stype = ShapeType.PLACEHOLDER
    else:
        preset = _shape_preset(spPr, ns)
        stype = ShapeType.TEXT_BOX if preset in (None, "rect") and text else ShapeType.SHAPE

    # Fill / line color
    fill = _color_hex(
        spPr.find(f".//{_q(ns,'a','solidFill')}") if spPr else None, ns
    )

    return ShapeInfo(
        shape_id=shape_id,
        shape_name=shape_name or ph_type,
        shape_type=stype,
        bbox=bbox,
        text=text,
        text_paragraphs=paragraphs,
        fill_color=fill,
        shape_preset=_shape_preset(spPr, ns),
        z_order=z_order,
        raw_xml=ET.tostring(el, encoding="unicode"),
    )


def _parse_cxnSp(el: ET.Element, ns: Dict[str, str], z_order: int) -> Optional[ShapeInfo]:
    """Parse a <p:cxnSp> (connector / arrow)."""
    nvCxnSpPr = el.find(_q(ns, "p", "nvCxnSpPr"))
    cNvPr = nvCxnSpPr.find(_q(ns, "p", "cNvPr")) if nvCxnSpPr is not None else None
    shape_id = cNvPr.get("id", str(z_order)) if cNvPr is not None else str(z_order)
    shape_name = cNvPr.get("name", "") if cNvPr is not None else ""

    # Connection endpoints
    cNvCxnSpPr = nvCxnSpPr.find(_q(ns, "p", "cNvCxnSpPr")) if nvCxnSpPr is not None else None
    connects_from = connects_to = None
    if cNvCxnSpPr is not None:
        stCxn = cNvCxnSpPr.find(_q(ns, "a", "stCxn"))
        endCxn = cNvCxnSpPr.find(_q(ns, "a", "endCxn"))
        if stCxn is not None:
            connects_from = stCxn.get("id")
        if endCxn is not None:
            connects_to = endCxn.get("id")

    spPr = el.find(_q(ns, "p", "spPr"))
    xfrm = spPr.find(_q(ns, "a", "xfrm")) if spPr is not None else None
    bbox = _bbox_from_xfrm(xfrm, ns)
    if bbox is None:
        return None

    # Arrow heads: look in spPr/a:ln
    has_head = has_tail = False
    if spPr is not None:
        ln = spPr.find(_q(ns, "a", "ln"))
        if ln is not None:
            headEnd = ln.find(_q(ns, "a", "headEnd"))
            tailEnd = ln.find(_q(ns, "a", "tailEnd"))
            has_tail = headEnd is not None and headEnd.get("type", "none") not in ("none", "")
            has_head = tailEnd is not None and tailEnd.get("type", "none") not in ("none", "")
    # If no explicit decoration, treat as default arrow (tail = none, head = arrow)
    if not has_head and not has_tail:
        has_head = True

    return ShapeInfo(
        shape_id=shape_id,
        shape_name=shape_name,
        shape_type=ShapeType.CONNECTOR,
        bbox=bbox,
        connects_from=connects_from,
        connects_to=connects_to,
        has_arrow_head=has_head,
        has_arrow_tail=has_tail,
        shape_preset=_shape_preset(spPr, ns),
        z_order=z_order,
        raw_xml=ET.tostring(el, encoding="unicode"),
    )


def _parse_pic(el: ET.Element, ns: Dict[str, str], z_order: int) -> Optional[ShapeInfo]:
    """Parse a <p:pic> (embedded image)."""
    nvPicPr = el.find(_q(ns, "p", "nvPicPr"))
    cNvPr = nvPicPr.find(_q(ns, "p", "cNvPr")) if nvPicPr is not None else None
    shape_id = cNvPr.get("id", str(z_order)) if cNvPr is not None else str(z_order)
    shape_name = cNvPr.get("name", "") if cNvPr is not None else ""

    spPr = el.find(_q(ns, "p", "spPr"))
    xfrm = spPr.find(_q(ns, "a", "xfrm")) if spPr is not None else None
    bbox = _bbox_from_xfrm(xfrm, ns)
    if bbox is None:
        return None

    return ShapeInfo(
        shape_id=shape_id,
        shape_name=shape_name,
        shape_type=ShapeType.IMAGE,
        bbox=bbox,
        z_order=z_order,
    )


def _parse_graphicFrame(el: ET.Element, ns: Dict[str, str], z_order: int) -> Optional[ShapeInfo]:
    """Parse <p:graphicFrame> (table, chart, SmartArt)."""
    nvGraphicFramePr = el.find(_q(ns, "p", "nvGraphicFramePr"))
    cNvPr = (
        nvGraphicFramePr.find(_q(ns, "p", "cNvPr"))
        if nvGraphicFramePr is not None
        else None
    )
    shape_id = cNvPr.get("id", str(z_order)) if cNvPr is not None else str(z_order)
    shape_name = cNvPr.get("name", "") if cNvPr is not None else ""

    xfrm = el.find(f".//{_q(ns,'a','xfrm')}")
    bbox = _bbox_from_xfrm(xfrm, ns)
    if bbox is None:
        return None

    # Determine sub-type
    graphic = el.find(f".//{_q(ns,'a','graphicData')}")
    uri = graphic.get("uri", "") if graphic is not None else ""
    if "chart" in uri:
        stype = ShapeType.CHART
    elif "diagram" in uri or "smartArt" in uri.lower():
        stype = ShapeType.SMART_ART
    else:
        # Try table
        if el.find(f".//{_q(ns,'a','tbl')}") is not None:
            stype = ShapeType.TABLE
        else:
            stype = ShapeType.CHART

    # Parse table data
    table_data: Optional[List[List[str]]] = None
    if stype == ShapeType.TABLE:
        table_data = []
        for tr in el.iter(_q(ns, "a", "tr")):
            row = []
            for tc in tr.iter(_q(ns, "a", "tc")):
                cell_text, _ = _text_of(tc, ns)
                row.append(cell_text)
            if row:
                table_data.append(row)

    return ShapeInfo(
        shape_id=shape_id,
        shape_name=shape_name,
        shape_type=stype,
        bbox=bbox,
        table_data=table_data,
        z_order=z_order,
    )


def _parse_grpSp(el: ET.Element, ns: Dict[str, str], z_order: int) -> List[ShapeInfo]:
    """Parse <p:grpSp> (group shape) → expand children."""
    nvGrpSpPr = el.find(_q(ns, "p", "nvGrpSpPr"))
    cNvPr = nvGrpSpPr.find(_q(ns, "p", "cNvPr")) if nvGrpSpPr is not None else None
    group_id = cNvPr.get("id", "") if cNvPr is not None else ""

    shapes: List[ShapeInfo] = []
    for child in el:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        child_shapes = _dispatch_shape(child, tag, ns, z_order)
        for s in child_shapes:
            s.group_id = group_id
        shapes.extend(child_shapes)
    return shapes


def _dispatch_shape(
    el: ET.Element, local_tag: str, ns: Dict[str, str], z_order: int
) -> List[ShapeInfo]:
    """Dispatch a shape element to its parser; returns list (groups expand)."""
    if local_tag == "sp":
        shape = _parse_sp(el, ns, z_order)
        return [shape] if shape else []
    if local_tag == "cxnSp":
        shape = _parse_cxnSp(el, ns, z_order)
        return [shape] if shape else []
    if local_tag == "pic":
        shape = _parse_pic(el, ns, z_order)
        return [shape] if shape else []
    if local_tag == "graphicFrame":
        shape = _parse_graphicFrame(el, ns, z_order)
        return [shape] if shape else []
    if local_tag == "grpSp":
        return _parse_grpSp(el, ns, z_order)
    return []


def _get_slide_dimensions(zf: zipfile.ZipFile) -> Tuple[float, float]:
    """Read slide dimensions from ppt/presentation.xml."""
    try:
        xml_bytes = zf.read("ppt/presentation.xml")
        xml_text = xml_bytes.decode("utf-8", errors="replace")
        ns = _detect_namespaces(xml_text)
        root = ET.fromstring(xml_text)
        sldSz = root.find(f".//{_q(ns,'p','sldSz')}")
        if sldSz is not None:
            return float(sldSz.get("cx", _DEFAULT_SLIDE_WIDTH)), float(
                sldSz.get("cy", _DEFAULT_SLIDE_HEIGHT)
            )
    except Exception as exc:
        logger.debug("Could not read slide dimensions: %s", exc)
    return _DEFAULT_SLIDE_WIDTH, _DEFAULT_SLIDE_HEIGHT


def _get_notes(zf: zipfile.ZipFile, slide_num: int) -> str:
    """Try to read speaker notes for a slide."""
    notes_path = f"ppt/notesSlides/notesSlide{slide_num}.xml"
    try:
        xml_bytes = zf.read(notes_path)
        xml_text = xml_bytes.decode("utf-8", errors="replace")
        ns = _detect_namespaces(xml_text)
        root = ET.fromstring(xml_text)
        parts: List[str] = []
        for t in root.iter(_q(ns, "a", "t")):
            if t.text:
                parts.append(t.text)
        return " ".join(parts).strip()
    except KeyError:
        return ""
    except Exception as exc:
        logger.debug("Could not read notes for slide %d: %s", slide_num, exc)
        return ""


def parse_slide(
    pptx_path: Path, slide_num: int  # 1-based
) -> SlideXMLStructure:
    """Parse a single slide and return its structural representation."""
    with zipfile.ZipFile(pptx_path, "r") as zf:
        slide_width, slide_height = _get_slide_dimensions(zf)

        # Slides are named slide1.xml, slide2.xml, …
        slide_path = f"ppt/slides/slide{slide_num}.xml"
        try:
            xml_bytes = zf.read(slide_path)
        except KeyError:
            raise ValueError(f"Slide {slide_num} not found in {pptx_path}")

        xml_text = xml_bytes.decode("utf-8", errors="replace")
        ns = _detect_namespaces(xml_text)
        root = ET.fromstring(xml_text)

        shapes: List[ShapeInfo] = []
        spTree = root.find(f".//{_q(ns,'p','spTree')}")
        if spTree is None:
            spTree = root  # fallback

        for z_order, child in enumerate(spTree):
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            shapes.extend(_dispatch_shape(child, tag, ns, z_order))

        # Deduplicate IDs (can happen with groups)
        seen: Dict[str, int] = {}
        for s in shapes:
            if s.shape_id in seen:
                seen[s.shape_id] += 1
                s.shape_id = f"{s.shape_id}_{seen[s.shape_id]}"
            else:
                seen[s.shape_id] = 0

        # Extract title from placeholder
        title = ""
        for s in shapes:
            if s.shape_name in ("title", "ctrTitle") or (
                s.shape_type == ShapeType.PLACEHOLDER and "title" in s.shape_name.lower()
            ):
                title = s.text
                break

        notes = _get_notes(zf, slide_num)

    # Build arrow-based relationships from connector shapes
    relationships = _build_connector_relationships(shapes)

    return SlideXMLStructure(
        slide_num=slide_num,
        slide_width=slide_width,
        slide_height=slide_height,
        title=title,
        shapes=shapes,
        relationships=relationships,
        notes=notes,
    )


def _build_connector_relationships(shapes: List[ShapeInfo]) -> List[Relationship]:
    """Create ARROW_CONNECTS relationships from connector shapes."""
    id_set = {s.shape_id for s in shapes}
    rels: List[Relationship] = []
    for s in shapes:
        if s.shape_type != ShapeType.CONNECTOR:
            continue
        if s.connects_from and s.connects_to:
            from_id = s.connects_from if s.connects_from in id_set else None
            to_id = s.connects_to if s.connects_to in id_set else None
            if from_id and to_id:
                rels.append(
                    Relationship(
                        from_shape_id=from_id,
                        to_shape_id=to_id,
                        rel_type=RelationshipType.ARROW_CONNECTS,
                        connector_shape_id=s.shape_id,
                        metadata={
                            "has_arrow_head": s.has_arrow_head,
                            "has_arrow_tail": s.has_arrow_tail,
                        },
                    )
                )
    return rels


def count_slides(pptx_path: Path) -> int:
    """Return the total number of slides in the PPTX."""
    with zipfile.ZipFile(pptx_path, "r") as zf:
        names = zf.namelist()
    return sum(1 for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n))


def parse_all_slides(pptx_path: Path) -> List[SlideXMLStructure]:
    """Parse every slide in the PPTX."""
    n = count_slides(pptx_path)
    logger.info("Parsing XML for %d slides …", n)
    results: List[SlideXMLStructure] = []
    for i in range(1, n + 1):
        try:
            results.append(parse_slide(pptx_path, i))
        except Exception as exc:
            logger.warning("Failed to parse slide %d: %s", i, exc)
    return results
