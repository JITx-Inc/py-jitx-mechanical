"""Data models for mechanical imports.

`import_dxf` and `import_idf` return a `MechanicalImport` built from these types;
code generation and the report writers read it. Lengths are in millimeters and
angles in degrees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


@dataclass(frozen=True)
class Point:
    """A 2D point."""

    x: float
    y: float


@dataclass(frozen=True)
class Pose:
    """A position and counterclockwise rotation."""

    x: float
    y: float
    angle: float
    flip_x: bool = False


@dataclass(frozen=True)
class PathSegment:
    """Base class for a segment of a closed path."""


@dataclass(frozen=True)
class LinePathSegment(PathSegment):
    """A straight segment from ``start`` to ``end``."""

    start: Point = field(default_factory=lambda: Point(0.0, 0.0))
    end: Point = field(default_factory=lambda: Point(0.0, 0.0))


@dataclass(frozen=True)
class ArcPathSegment(PathSegment):
    """A circular arc from ``start_angle`` to ``end_angle`` around ``center``.

    The arc runs counterclockwise when ``end_angle`` is the larger angle and
    clockwise otherwise. ``start_point`` and ``end_point`` are its ends.
    """

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
    """A full circle, with the source layer or section it came from."""

    center: Point = field(default_factory=lambda: Point(0.0, 0.0))
    radius: float = 0.0
    source_layer: str = ""
    source_section: str = ""


Geometry = ClosedPath | CircleGeometry


@dataclass(frozen=True)
class TextGeometry:
    """Text read from a DXF drawing, before it becomes an annotation."""

    content: str = ""
    position: Point = field(default_factory=lambda: Point(0.0, 0.0))
    height: float = 0.0
    rotation: float = 0.0
    source_layer: str = ""
    source_section: str = ""


class MessageSeverity(StrEnum):
    """Severity of an import message."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class ImportMessage:
    """A note about the conversion, with the source it concerns and a suggested remedy."""

    severity: MessageSeverity
    text: str
    source: str = ""
    hint: str = ""


class HolePlating(StrEnum):
    """Plating of a drilled hole, as the source records it."""

    PLATED = "plated"
    UNPLATED = "unplated"
    UNKNOWN = "unknown"


class HolePolicy(StrEnum):
    """How plated holes and holes of unknown plating are imported.

    ``CUTOUT`` makes them Board cutouts. ``COMPONENT`` makes them single-pin
    components that can be connected to a net. Unplated holes are always cutouts.
    """

    CUTOUT = "cutout"
    COMPONENT = "component"


@dataclass(frozen=True)
class MechanicalHole:
    """A drilled hole and how it was imported.

    Attributes:
        geometry: The hole's circle.
        plating: Plating recorded by the source.
        associated_with: IDF association: BOARD, PANEL, NOREFDES, or a reference
            designator. Empty for DXF imports.
        hole_type: IDF hole type, such as PIN, VIA, MTG, or TOOL. Empty for DXF
            imports.
        owner: IDF owner: ECAD, MCAD, or UNOWNED. Empty for DXF imports.
        imported_as: Whether the hole became a cutout or a component.
    """

    geometry: CircleGeometry
    plating: HolePlating = HolePlating.UNKNOWN
    associated_with: str = ""
    hole_type: str = ""
    owner: str = ""
    imported_as: Literal["cutout", "component"] = "cutout"


@dataclass(frozen=True)
class MechanicalRegion:
    """An area such as a keepout or an outline, imported as a Board feature.

    Attributes:
        role: What the area is. IDF imports use route_keepout, via_keepout,
            place_keepout, route_outline, place_outline, and other_outline. DXF
            imports use keepout, soldermask, bend, height, annotation_region,
            hatch, and hatch_solid.
        geometry: The area's boundary.
        source_name: Source section or layer.
        layers: IDF routing layers (TOP, BOTTOM, BOTH, INNER, or ALL) or board
            side (TOP, BOTTOM, or BOTH); empty when the source gives none.
        owner: IDF owner: ECAD, MCAD, or UNOWNED.
        thickness: IDF height or thickness.
        notes: Free-form notes. The importers leave this empty.
    """

    role: str
    geometry: Geometry
    source_name: str = ""
    layers: str = ""
    owner: str = ""
    thickness: float = 0.0
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class MechanicalAnnotation:
    """Text placed on the board drawing: an IDF note or DXF text."""

    role: str
    text: str
    position: Point = field(default_factory=lambda: Point(0.0, 0.0))
    height: float = 0.0
    rotation: float = 0.0
    source_name: str = ""


@dataclass(frozen=True)
class MechanicalPlacement:
    """A component placement read from an IDF file. Code generation ignores it."""

    refdes: str
    package: str = ""
    part_number: str = ""
    pose: Pose = field(default_factory=lambda: Pose(0.0, 0.0, 0.0))
    side: str = ""
    status: str = ""


@dataclass(frozen=True)
class MechanicalComponent:
    """Holes of one size and plating, imported as connectable components.

    Attributes:
        name: Name for the generated component class.
        geometry: The hole's circle, centered on the origin.
        placements: Hole centers.
        plating: Plating recorded by the source.
        source: Source section or layer.
    """

    name: str
    geometry: CircleGeometry
    placements: tuple[Point, ...]
    plating: HolePlating = HolePlating.UNKNOWN
    source: str = ""


@dataclass(frozen=True)
class SourceObject:
    """A source object that was not converted, with the reason."""

    role: str
    source_name: str
    detail: str = ""
    count: int = 1


@dataclass
class MechanicalImport:
    """Board geometry and metadata read from a mechanical drawing.

    Attributes:
        source_path: The imported file.
        source_format: Format read: dxf, emn, idf, idx, or bdf.
        source_units: Units declared by the source or given as an override; None
            when neither is known.
        unit_scale: Factor that converted source coordinates to millimeters.
        board_outline: The board outline, or None when none was found.
        board_cutouts: Cutouts in the board, including holes imported as cutouts.
        holes: IDF drilled holes and DXF circles classified as holes, however
            each was imported.
        regions: Keepouts, outlines, and other areas.
        annotations: Text notes.
        placements: Component placements from IDF files.
        mechanical_components: Holes imported as components, grouped by size and
            plating.
        unclassified: Source objects that were not converted.
        messages: Warnings and other notes about the conversion.
    """

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
        """Record a warning about the conversion.

        Args:
            text: What happened.
            source: The source section or layer concerned.
            hint: A suggested remedy.
        """
        self.messages.append(
            ImportMessage(MessageSeverity.WARNING, text=text, source=source, hint=hint)
        )

    def info(self, text: str, *, source: str = "", hint: str = "") -> None:
        """Record an informational note about the conversion.

        Args:
            text: What happened.
            source: The source section or layer concerned.
            hint: A suggested remedy.
        """
        self.messages.append(
            ImportMessage(MessageSeverity.INFO, text=text, source=source, hint=hint)
        )
