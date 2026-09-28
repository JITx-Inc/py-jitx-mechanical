"""Placed DXF geometry in millimeters, independent of the JITX runtime."""

from dataclasses import dataclass, field
from typing import Literal

type Point = tuple[float, float]
type Category = Literal[
    "board_outline", "components", "pads", "drill", "vias", "copper", "annotations"
]


@dataclass(frozen=True)
class Marker:
    position: Point


@dataclass(frozen=True)
class Circle:
    center: Point
    radius: float


@dataclass(frozen=True)
class Path:
    """Polyline vertices with a signed bulge for the segment following each vertex."""

    vertices: tuple[tuple[float, float, float], ...]
    closed: bool = False
    width: float = 0.0


@dataclass(frozen=True)
class Text:
    position: Point
    string: str
    height: float
    rotation: float = 0.0
    mirrored: bool = False
    anchor: str = "C"


type Geometry = Marker | Circle | Path | Text


@dataclass(frozen=True)
class Entity:
    layer: str
    category: Category
    geometry: Geometry


@dataclass(frozen=True)
class DxfExportConfig:
    """Select DXF layers and independent categories of geometry.

    ``layers=None`` selects all layer names; an empty set selects none.
    A geometry category must also be enabled by its ``include_`` switch.
    ``layer_colors`` maps exact layer names to AutoCAD color indexes.
    """

    layers: set[str] | None = None
    include_board_outline: bool = True
    include_components: bool = True
    include_pads: bool = True
    include_drill: bool = True
    include_vias: bool = True
    include_copper: bool = True
    include_annotations: bool = True
    layer_colors: dict[str, int] = field(default_factory=dict)

    def includes(self, category: Category, layer: str) -> bool:
        return bool(getattr(self, f"include_{category}")) and (
            self.layers is None or layer in self.layers
        )
