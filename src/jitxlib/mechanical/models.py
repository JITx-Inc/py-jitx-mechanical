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
