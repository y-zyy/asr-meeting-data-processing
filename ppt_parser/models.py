from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ShapeType(Enum):
    TEXT_BOX = "text_box"
    SHAPE = "shape"
    CONNECTOR = "connector"
    IMAGE = "image"
    TABLE = "table"
    CHART = "chart"
    GROUP = "group"
    PLACEHOLDER = "placeholder"
    SMART_ART = "smart_art"


class RelationshipType(Enum):
    ARROW_CONNECTS = "arrow_connects"
    CONTAINS = "contains"
    ALIGNED_HORIZONTAL = "aligned_horizontal"
    ALIGNED_VERTICAL = "aligned_vertical"
    GROUPED = "grouped"
    FLOW_SEQUENCE = "flow_sequence"
    PROXIMITY = "proximity"


@dataclass
class BoundingBox:
    """Position and size in EMU (English Metric Units). 1 inch = 914400 EMU."""
    x: float
    y: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.x + self.width / 2

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height

    def contains(self, other: BoundingBox, tolerance: float = 0.0) -> bool:
        return (
            self.x - tolerance <= other.x
            and self.y - tolerance <= other.y
            and self.right + tolerance >= other.right
            and self.bottom + tolerance >= other.bottom
        )

    def overlaps(self, other: BoundingBox) -> bool:
        return not (
            self.right < other.x
            or other.right < self.x
            or self.bottom < other.y
            or other.bottom < self.y
        )

    def center_distance_to(self, other: BoundingBox) -> float:
        return math.hypot(self.center_x - other.center_x, self.center_y - other.center_y)

    def edge_distance_to(self, other: BoundingBox) -> float:
        """Minimum distance between edges (0 if overlapping)."""
        dx = max(0.0, max(self.x, other.x) - min(self.right, other.right))
        dy = max(0.0, max(self.y, other.y) - min(self.bottom, other.bottom))
        return math.hypot(dx, dy)

    def normalized(self, slide_width: float, slide_height: float) -> Dict[str, float]:
        """Return position as percentage (0-100) of slide dimensions."""
        return {
            "x_pct": round(self.x / slide_width * 100, 1),
            "y_pct": round(self.y / slide_height * 100, 1),
            "w_pct": round(self.width / slide_width * 100, 1),
            "h_pct": round(self.height / slide_height * 100, 1),
        }


@dataclass
class ShapeInfo:
    shape_id: str
    shape_name: str
    shape_type: ShapeType
    bbox: BoundingBox
    text: str = ""
    text_paragraphs: List[str] = field(default_factory=list)
    # Connector endpoints (references to shape IDs)
    connects_from: Optional[str] = None
    connects_to: Optional[str] = None
    has_arrow_head: bool = False   # arrow at end
    has_arrow_tail: bool = False   # arrow at start
    # Visual properties
    fill_color: Optional[str] = None
    line_color: Optional[str] = None
    shape_preset: Optional[str] = None  # e.g. "roundRect", "diamond", "chevron"
    # Grouping
    z_order: int = 0
    group_id: Optional[str] = None
    # Table data (for TABLE type)
    table_data: Optional[List[List[str]]] = None
    # Raw XML for debugging
    raw_xml: str = ""


@dataclass
class Relationship:
    from_shape_id: str
    to_shape_id: str
    rel_type: RelationshipType
    connector_shape_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        arrow = "→" if self.rel_type == RelationshipType.ARROW_CONNECTS else "—"
        return f"{self.from_shape_id} {arrow} {self.to_shape_id} [{self.rel_type.value}]"


@dataclass
class SlideXMLStructure:
    slide_num: int
    slide_width: float   # EMU
    slide_height: float  # EMU
    title: str = ""
    shapes: List[ShapeInfo] = field(default_factory=list)
    relationships: List[Relationship] = field(default_factory=list)
    notes: str = ""
    layout_name: str = ""

    def get_shape_by_id(self, shape_id: str) -> Optional[ShapeInfo]:
        for s in self.shapes:
            if s.shape_id == shape_id:
                return s
        return None

    def get_connectors(self) -> List[ShapeInfo]:
        return [s for s in self.shapes if s.shape_type == ShapeType.CONNECTOR]

    def get_non_connectors(self) -> List[ShapeInfo]:
        return [s for s in self.shapes if s.shape_type != ShapeType.CONNECTOR]


@dataclass
class OCRResult:
    slide_num: int
    text: str
    confidence: float
    word_boxes: List[Dict[str, Any]] = field(default_factory=list)
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VLMResult:
    slide_num: int
    markdown: str
    confidence: float
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SlideResult:
    slide_num: int
    image_path: str = ""
    xml_structure: Optional[SlideXMLStructure] = None
    ocr_result: Optional[OCRResult] = None
    vlm_result: Optional[VLMResult] = None
    final_markdown: str = ""
    error: Optional[str] = None
