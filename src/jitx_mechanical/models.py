"""Shared data models for mechanical import and export.

The importers in this package parse source-specific files into these simple
Python data structures first.  JITX code generation is a separate final step,
which keeps parsing testable and avoids coupling file readers to a particular
JITX runtime version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    angle: float
    flip_x: bool = False


@dataclass(frozen=True)
class LineSegment:
    p1: Point
    p2: Point
    width: float = 0.0


@dataclass(frozen=True)
class ArcSegment:
    center: Point
    radius: float
    start_angle: float
    end_angle: float
    width: float = 0.0


@dataclass(frozen=True)
class PathSegment:
    """Base class for a segment of a closed path."""


@dataclass(frozen=True)
class LinePathSegment(PathSegment):
    start: Point = field(default_factory=lambda: Point(0.0, 0.0))
    end: Point = field(default_factory=lambda: Point(0.0, 0.0))


@dataclass(frozen=True)
class ArcPathSegment(PathSegment):
    center: Point = field(default_factory=lambda: Point(0.0, 0.0))
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0
    start_point: Point = field(default_factory=lambda: Point(0.0, 0.0))
    end_point: Point = field(default_factory=lambda: Point(0.0, 0.0))


@dataclass(frozen=True)
class ClosedPath:
    """A closed region boundary assembled from line and arc segments."""

    segments: tuple[PathSegment, ...] = ()
    source_layer: str = ""
    source_section: str = ""


@dataclass(frozen=True)
class CircleGeometry:
    center: Point = field(default_factory=lambda: Point(0.0, 0.0))
    radius: float = 0.0
    source_layer: str = ""
    source_section: str = ""


Geometry = ClosedPath | CircleGeometry


@dataclass(frozen=True)
class TextGeometry:
    content: str = ""
    position: Point = field(default_factory=lambda: Point(0.0, 0.0))
    height: float = 0.0
    rotation: float = 0.0
    source_layer: str = ""
    source_section: str = ""


class MessageSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class ImportMessage:
    severity: MessageSeverity
    text: str
    source: str = ""
    hint: str = ""


class HolePlating(StrEnum):
    PLATED = "plated"
    UNPLATED = "unplated"
    UNKNOWN = "unknown"


class HolePolicy(StrEnum):
    CUTOUT = "cutout"
    COMPONENT = "component"


@dataclass(frozen=True)
class MechanicalHole:
    geometry: CircleGeometry
    plating: HolePlating = HolePlating.UNKNOWN
    associated_with: str = ""
    hole_type: str = ""
    owner: str = ""
    imported_as: Literal["cutout", "component"] = "cutout"


@dataclass(frozen=True)
class MechanicalRegion:
    role: str
    geometry: Geometry
    source_name: str = ""
    layers: str = ""
    owner: str = ""
    thickness: float = 0.0
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MechanicalAnnotation:
    role: str
    text: str
    position: Point = field(default_factory=lambda: Point(0.0, 0.0))
    height: float = 0.0
    rotation: float = 0.0
    source_name: str = ""


@dataclass(frozen=True)
class MechanicalPlacement:
    refdes: str
    package: str = ""
    part_number: str = ""
    pose: Pose = field(default_factory=lambda: Pose(0.0, 0.0, 0.0))
    side: str = ""
    status: str = ""


@dataclass(frozen=True)
class MechanicalComponent:
    name: str
    geometry: CircleGeometry
    placements: tuple[Point, ...]
    plating: HolePlating = HolePlating.UNKNOWN
    source: str = ""


@dataclass(frozen=True)
class SourceObject:
    role: str
    source_name: str
    detail: str = ""
    count: int = 1


@dataclass
class MechanicalImport:
    source_path: str
    source_format: str
    source_units: str | None = None
    unit_scale: float = 1.0
    board_outline: Geometry | None = None
    board_cutouts: list[Geometry] = field(default_factory=list)
    holes: list[MechanicalHole] = field(default_factory=list)
    regions: list[MechanicalRegion] = field(default_factory=list)
    annotations: list[MechanicalAnnotation] = field(default_factory=list)
    placements: list[MechanicalPlacement] = field(default_factory=list)
    mechanical_components: list[MechanicalComponent] = field(default_factory=list)
    unclassified: list[SourceObject] = field(default_factory=list)
    messages: list[ImportMessage] = field(default_factory=list)

    def warn(self, text: str, *, source: str = "", hint: str = "") -> None:
        self.messages.append(
            ImportMessage(MessageSeverity.WARNING, text=text, source=source, hint=hint)
        )

    def info(self, text: str, *, source: str = "", hint: str = "") -> None:
        self.messages.append(
            ImportMessage(MessageSeverity.INFO, text=text, source=source, hint=hint)
        )


# --- JITX XML export models retained from py-jitx-dxf -----------------------


@dataclass
class CirclePad:
    name: str
    center: Point
    radius: float
    side: str
    hole_radius: float = 0.0


@dataclass
class RectanglePad:
    name: str
    width: float
    height: float
    rect_pose: Pose
    pad_pose: Pose
    side: str
    hole_radius: float = 0.0


@dataclass
class PolygonPad:
    name: str
    points: list[Point]
    pose: Pose
    side: str
    hole_radius: float = 0.0


@dataclass
class PolygonShape:
    points: list[Point]
    layer_name: str
    side: str


@dataclass
class LineShape:
    line: LineSegment
    layer_name: str
    side: str


@dataclass
class TextShape:
    string: str
    size: float
    pose: Pose
    layer_name: str
    side: str


@dataclass
class Package:
    name: str
    pads: list[CirclePad]
    rectangle_pads: list[RectanglePad]
    polygon_pads: list[PolygonPad]
    polygons: list[PolygonShape]
    lines: list[LineShape]


@dataclass
class DesignatorText:
    string: str
    size: float
    anchor: str
    pose: Pose


@dataclass
class Instance:
    designator: str
    package_name: str
    side: str
    pose: Pose
    designator_text: DesignatorText | None
    shapes_text: list[TextShape] = field(default_factory=list)
    shapes_polygon: list[PolygonShape] = field(default_factory=list)
    shapes_line: list[LineShape] = field(default_factory=list)


@dataclass
class CopperShape:
    layer_index: int
    net: str


@dataclass
class CopperLine(CopperShape):
    p1: Point = field(default_factory=lambda: Point(0.0, 0.0))
    p2: Point = field(default_factory=lambda: Point(0.0, 0.0))
    width: float = 0.0


@dataclass
class CopperArc(CopperShape):
    center: Point = field(default_factory=lambda: Point(0.0, 0.0))
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0
    width: float = 0.0


@dataclass
class CopperPolygon(CopperShape):
    points: list[Point] = field(default_factory=list)


@dataclass
class Via:
    center: Point
    diameter: float
    hole_diameter: float
    net: str
    start_side: str
    end_side: str


@dataclass
class BoardData:
    boundary_lines: list[LineSegment]
    boundary_arcs: list[ArcSegment]
    packages: dict[str, Package]
    instances: list[Instance]
    board_shapes: list[PolygonShape]
    board_line_shapes: list[LineShape]
    tracks: list[CopperShape]
    fills: list[CopperPolygon]
    vias: list[Via]
    layer_names: dict[int, str]
