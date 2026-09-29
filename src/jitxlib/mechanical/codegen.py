"""Generate user-editable JITX Python from mechanical import data."""

from __future__ import annotations

import keyword
import math
import re

from .geometry import geometry_bounding_box
from .models import (
    ArcPathSegment,
    CircleGeometry,
    ClosedPath,
    Geometry,
    LinePathSegment,
    MechanicalAnnotation,
    MechanicalComponent,
    MechanicalImport,
    MechanicalRegion,
    Point,
)

_KEEPOUT_ROLES = frozenset({"route_keepout", "via_keepout"})
_DEFAULT_ANNOTATION_HEIGHT_MM = 1.0
_CUSTOM_NAME_BY_ROLE = {
    "place_keepout": "PlaceKeepout",
    "place_outline": "PlaceOutline",
    "route_outline": "RouteOutline",
    "other_outline": "OtherOutline",
}

DEFAULT_PRECISION = 4
DEFAULT_ANNULAR_PAD_MARGIN_MM = 0.4
"""Copper extends past the hole edge by this margin on each side
when no annular ring data is in the source. Doubled when computing
the pad diameter (`hole_diameter + 2 * margin`)."""


def generate_board_module(
    imported: MechanicalImport,
    *,
    class_name: str = "ImportedBoard",
    module_name: str | None = None,
    recenter: bool = True,
    precision: int = DEFAULT_PRECISION,
) -> str:
    """Return Python source for a Board with attached cutouts and drawing features.

    ``imported`` must contain an outline in millimeters. ``class_name`` is
    converted to a valid Python identifier; ``module_name`` identifies the source
    in the module's description. Recentering moves the outline's bounding-box
    center to the origin and applies the same offset to every attached feature.
    ``precision`` controls decimal places and must be between 0 and 12.
    """

    if imported.board_outline is None:
        raise ValueError(
            "No board outline detected; select an outline layer before generating code"
        )
    _validate_precision(precision)
    clean_class = sanitize_identifier(class_name)
    offset = _recenter_offset(imported.board_outline, recenter)
    imports = _imports(imported)
    lines: list[str] = []

    source = module_name or imported.source_path
    lines.append(repr(f"Board geometry imported from {source}."))
    lines.append("")
    lines.extend(imports)
    lines.append("")
    lines.append("")
    lines.append(f"class {clean_class}(Board):")
    lines.append(
        "    shape = "
        + geometry_to_code(imported.board_outline, offset=offset, indent=1, precision=precision)
    )

    init_lines = _emit_board_init_features(imported, offset=offset, precision=precision)
    if init_lines:
        lines.append("")
        lines.append("    def __init__(self):")
        lines.append("        super().__init__()")
        lines.extend(init_lines)

    lines.append("")
    lines.append("")
    if imported.mechanical_components:
        lines.append("# Mechanical/plated holes are emitted as connectable JITX Component")
        lines.append("# classes in the companion `_components.py` module — instantiate")
        lines.append("# `MechanicalComponentsCircuit` there and net its ports into your design.")
    else:
        lines.append("MECHANICAL_COMPONENTS = []")

    if imported.messages:
        lines.append("")
        lines.append("IMPORT_MESSAGES = [")
        for message in imported.messages:
            lines.append(
                "    "
                + repr(
                    {
                        "severity": message.severity.value,
                        "message": message.text,
                        "source": message.source,
                        "hint": message.hint,
                    }
                )
                + ","
            )
        lines.append("]")

    lines.append("")
    return "\n".join(lines)


def geometry_to_code(
    geometry: Geometry,
    *,
    offset: Point | None = None,
    indent: int = 0,
    precision: int = DEFAULT_PRECISION,
) -> str:
    if offset is None:
        offset = Point(0.0, 0.0)
    if isinstance(geometry, CircleGeometry):
        return _circle_to_code(geometry, offset=offset, precision=precision)
    if _is_axis_aligned_rectangle(geometry):
        return _rectangle_as_polygon(geometry, offset=offset, precision=precision)
    if any(isinstance(seg, ArcPathSegment) for seg in geometry.segments):
        return _arc_polygon_to_code(geometry, offset=offset, indent=indent, precision=precision)
    return _polygon_to_code(geometry, offset=offset, indent=indent, precision=precision)


def _imports(imported: MechanicalImport) -> list[str]:
    geometries: list[Geometry] = []
    if imported.board_outline is not None:
        geometries.append(imported.board_outline)
    geometries.extend(imported.board_cutouts)
    geometries.extend(region.geometry for region in imported.regions)

    needs_arc_polygon = any(
        isinstance(geometry, ClosedPath)
        and any(isinstance(seg, ArcPathSegment) for seg in geometry.segments)
        for geometry in geometries
    )
    needs_circle = any(isinstance(geometry, CircleGeometry) for geometry in geometries)
    needs_polygon = any(
        isinstance(geometry, ClosedPath)
        and not any(isinstance(seg, ArcPathSegment) for seg in geometry.segments)
        for geometry in geometries
    )
    needs_text = bool(imported.annotations)

    keepout_regions = [r for r in imported.regions if r.role in _KEEPOUT_ROLES]
    custom_regions = [r for r in imported.regions if r.role not in _KEEPOUT_ROLES]
    needs_keepout = bool(keepout_regions)
    needs_custom = bool(custom_regions) or needs_text
    needs_layerset = needs_keepout

    shape_imports: list[str] = []
    if needs_arc_polygon:
        shape_imports.extend(["Arc", "ArcPolygon"])
    if needs_circle:
        shape_imports.append("Circle")
    if needs_polygon:
        shape_imports.append("Polygon")
    if needs_text:
        shape_imports.append("Text")

    feature_imports: list[str] = []
    if imported.board_cutouts:
        feature_imports.append("Cutout")
    if needs_keepout:
        feature_imports.append("KeepOut")
    if needs_custom:
        feature_imports.append("Custom")

    lines = ["from jitx.board import Board"]
    if feature_imports:
        lines.append(f"from jitx.feature import {', '.join(feature_imports)}")
    if needs_layerset:
        lines.append("from jitx.layerindex import LayerSet")
    if shape_imports:
        lines.append(f"from jitx.shapes.primitive import {', '.join(shape_imports)}")
    return lines


def _emit_board_init_features(
    imported: MechanicalImport,
    *,
    offset: Point,
    precision: int,
) -> list[str]:
    """Generate cutouts, regions, and annotations attached to the Board."""
    regions = imported.regions
    annotations = imported.annotations
    body: list[str] = []
    for idx, cutout in enumerate(imported.board_cutouts):
        shape = geometry_to_code(cutout, offset=offset, indent=2, precision=precision)
        body.append(f"        self.cutout_{idx} = Cutout({shape})")
    for idx, region in enumerate(regions):
        attr = "region_" + sanitize_identifier(f"{region.role}_{idx}")
        body.extend(_region_feature_lines(region, attr, offset=offset, precision=precision))

    note_idx = 0
    for annotation in annotations:
        attr = f"note_{note_idx}"
        note_idx += 1
        body.extend(_annotation_feature_lines(annotation, attr, offset=offset, precision=precision))

    return body


def _region_feature_lines(
    region: MechanicalRegion,
    attr: str,
    *,
    offset: Point,
    precision: int,
) -> list[str]:
    shape_code = geometry_to_code(region.geometry, offset=offset, indent=2, precision=precision)
    if region.role in _KEEPOUT_ROLES:
        kind_kwarg = "route=True" if region.role == "route_keepout" else "via=True"
        layer_code = _layer_string_to_layerset(region.layers)
        return [
            f"        self.{attr} = KeepOut(",
            f"            {shape_code},",
            f"            layers={layer_code},",
            f"            {kind_kwarg},",
            "        )",
        ]
    name = _CUSTOM_NAME_BY_ROLE.get(region.role, _to_pascal(region.role))
    return [
        f"        self.{attr} = Custom({shape_code}, name={name!r})",
    ]


def _annotation_feature_lines(
    annotation: MechanicalAnnotation,
    attr: str,
    *,
    offset: Point,
    precision: int,
) -> list[str]:
    text = repr(annotation.text)
    height = annotation.height if annotation.height > 0 else _DEFAULT_ANNOTATION_HEIGHT_MM
    height_code = _fmt(height, precision=precision)
    x = _fmt(annotation.position.x + offset.x, precision=precision)
    y = _fmt(annotation.position.y + offset.y, precision=precision)
    rotation = _fmt(annotation.rotation, precision=precision)
    return [
        f"        self.{attr} = Custom("
        f'Text({text}, {height_code}).at({x}, {y}, rotate={rotation}), name="Note")'
    ]


def _layer_string_to_layerset(layers: str) -> str:
    token = (layers or "").strip().upper()
    if token == "TOP":
        return "LayerSet(0)"
    if token == "BOTTOM":
        return "LayerSet(-1)"
    return "LayerSet.all()"


def _to_pascal(snake: str) -> str:
    return "".join(part.capitalize() for part in snake.split("_") if part) or "Custom"


def _circle_to_code(
    circle: CircleGeometry,
    *,
    offset: Point,
    precision: int,
) -> str:
    radius = _fmt(circle.radius, precision=precision)
    cx = circle.center.x + offset.x
    cy = circle.center.y + offset.y
    if abs(cx) > 1e-10 or abs(cy) > 1e-10:
        return f"Circle(radius={radius}).at({_fmt(cx, precision=precision)}, {_fmt(cy, precision=precision)})"
    return f"Circle(radius={radius})"


def _polygon_to_code(
    path: ClosedPath,
    *,
    offset: Point,
    indent: int,
    precision: int,
) -> str:
    points = []
    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            points.append(_point_to_code(seg.start, offset=offset, precision=precision))
    if len(points) <= 6:
        return f"Polygon([{', '.join(points)}])"
    pad = "    " * (indent + 1)
    inner = f",\n{pad}".join(points)
    return f"Polygon([\n{pad}{inner},\n{'    ' * indent}])"


def _arc_polygon_to_code(
    path: ClosedPath,
    *,
    offset: Point,
    indent: int,
    precision: int,
) -> str:
    pad = "    " * (indent + 1)
    elements: list[str] = []

    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            elements.append(_point_to_code(seg.start, offset=offset, precision=precision))
        elif isinstance(seg, ArcPathSegment):
            elements.append(_point_to_code(seg.start_point, offset=offset, precision=precision))
            center = _point_to_code(seg.center, offset=offset, precision=precision)
            start = _fmt(_wrap_angle(seg.start_angle), precision=precision)
            sweep = _fmt(seg.end_angle - seg.start_angle, precision=precision)
            radius = _fmt(seg.radius, precision=precision)
            elements.append(f"Arc({center}, {radius}, {start}, {sweep})")

    inner = f",\n{pad}".join(elements)
    return f"ArcPolygon([\n{pad}{inner},\n{'    ' * indent}])"


def _point_to_code(point: Point, *, offset: Point, precision: int) -> str:
    return f"({_fmt(point.x + offset.x, precision=precision)}, {_fmt(point.y + offset.y, precision=precision)})"


def _is_axis_aligned_rectangle(path: ClosedPath) -> bool:
    if len(path.segments) != 4:
        return False
    if not all(isinstance(seg, LinePathSegment) for seg in path.segments):
        return False
    for seg in path.segments:
        assert isinstance(seg, LinePathSegment)
        dx = abs(seg.end.x - seg.start.x)
        dy = abs(seg.end.y - seg.start.y)
        if dx > 1e-6 and dy > 1e-6:
            return False
    return True


def _rectangle_as_polygon(
    path: ClosedPath,
    *,
    offset: Point,
    precision: int,
) -> str:
    return _polygon_to_code(path, offset=offset, indent=0, precision=precision)


def _recenter_offset(geometry: Geometry | None, recenter: bool) -> Point:
    if not recenter or geometry is None:
        return Point(0.0, 0.0)
    bb_min, bb_max = geometry_bounding_box(geometry)
    return Point(
        -(bb_min.x + bb_max.x) / 2.0,
        -(bb_min.y + bb_max.y) / 2.0,
    )


def _fmt(value: float, *, precision: int = DEFAULT_PRECISION) -> str:
    if not math.isfinite(value):
        raise ValueError("Generated geometry requires finite coordinates and dimensions")
    if abs(value) < 10 ** (-(precision + 1)):
        return "0.0"
    rounded = round(value, precision)
    if rounded == int(rounded):
        return f"{int(rounded)}.0"
    return f"{rounded:.{precision}f}".rstrip("0").rstrip(".")


def _wrap_angle(angle: float) -> float:
    wrapped = angle - 360.0 * math.floor(angle / 360.0)
    if wrapped >= 360.0 - 1e-6:
        return 0.0
    return wrapped


def sanitize_identifier(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_]", "_", name) or "ImportedBoard"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    if keyword.iskeyword(cleaned):
        cleaned += "_"
    return cleaned


def _validate_precision(precision: int) -> None:
    if not 0 <= precision <= 12:
        raise ValueError("precision must be between 0 and 12 decimal places")


def generate_components_module(
    imported: MechanicalImport,
    *,
    module_name: str | None = None,
    recenter: bool = True,
    precision: int = DEFAULT_PRECISION,
    annular_pad_margin: float = DEFAULT_ANNULAR_PAD_MARGIN_MM,
) -> str:
    """Return Python source for the import's connectable holes and their Circuit.

    An import with no mechanical components produces an empty string.
    ``module_name`` identifies the source in the module's description.
    ``recenter`` and ``precision`` must match the corresponding Board generation
    call so both modules share an origin and coordinate precision.
    ``annular_pad_margin`` is the positive radial copper and soldermask extension
    beyond each hole, in millimeters; it is not a fabrication rule from the source.
    """
    _validate_precision(precision)
    if not math.isfinite(annular_pad_margin) or annular_pad_margin <= 0:
        raise ValueError("annular_pad_margin must be a positive finite length")
    components = imported.mechanical_components
    if not components:
        return ""

    offset = _recenter_offset(imported.board_outline, recenter)
    source = module_name or imported.source_path

    header = [
        repr(f"Mechanical components imported from {source}."),
        "",
        f"# Copper and soldermask extend {annular_pad_margin} mm beyond each hole edge.",
        "# Adjust the pad shapes to meet your fabrication requirements.",
        "",
        "from jitx.circuit import Circuit",
        "from jitx.component import Component",
        "from jitx.feature import Cutout, Soldermask",
        "from jitx.landpattern import Landpattern, Pad",
        "from jitx.net import Port",
        "from jitx.shapes.primitive import Circle",
        "from jitx.symbol import Pin, Symbol",
        "",
        "",
        "class _SinglePinSymbol(Symbol):",
        '    """Shared single-pin symbol used by every mechanical-hole Component."""',
        "    p1 = Pin(at=(0, 0))",
        "",
    ]

    body: list[str] = []
    class_names: list[tuple[MechanicalComponent, str]] = []
    seen: dict[str, int] = {}
    for component in components:
        name = _component_class_name(component, precision=precision)
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 1
        class_names.append((component, name))
        body.extend(_component_class_lines(component, name, precision, annular_pad_margin))
        body.append("")

    body.append("class MechanicalComponentsCircuit(Circuit):")
    body.append('    """Imported mechanical holes placed in the same frame as the generated Board.')
    body.append("")
    body.append("    Net the exposed ports into your design — for example, connect every")
    body.append("    PLATED hole's `.p1` to a chassis-ground net at the Design level.")
    body.append('    """')
    body.append("")
    body.append("    def __init__(self):")
    body.append("        super().__init__()")
    for component, class_name in class_names:
        attr = _placement_attr_name(class_name)
        instances = [
            f"{class_name}().at({_fmt(p.x + offset.x, precision=precision)}, "
            f"{_fmt(p.y + offset.y, precision=precision)})"
            for p in component.placements
        ]
        if len(instances) <= 1:
            body.append(f"        self.{attr} = [{', '.join(instances)}]")
        else:
            body.append(f"        self.{attr} = [")
            for instance in instances:
                body.append(f"            {instance},")
            body.append("        ]")
    body.append("")
    return "\n".join(header + body)


def _component_class_name(component: MechanicalComponent, *, precision: int) -> str:
    if not math.isfinite(component.geometry.radius) or component.geometry.radius <= 0:
        raise ValueError("Mechanical hole radius must be finite and positive")
    diameter_mm = _fmt(component.geometry.radius * 2.0, precision=precision)
    plating = component.plating.value.upper() if component.plating else "UNKNOWN"
    safe_dia = diameter_mm.replace(".", "p")
    return f"MechanicalHole_{safe_dia}mm_{plating}"


def _placement_attr_name(class_name: str) -> str:
    return "instances_" + class_name.lower().replace("mechanicalhole_", "").lstrip("_")


def _component_class_lines(
    component: MechanicalComponent,
    class_name: str,
    precision: int,
    annular_pad_margin: float,
) -> list[str]:
    hole_dia = component.geometry.radius * 2.0
    pad_dia = hole_dia + 2.0 * annular_pad_margin
    hole_str = _fmt(hole_dia, precision=precision)
    pad_str = _fmt(pad_dia, precision=precision)
    plating_label = component.plating.value if component.plating else "unknown"
    pad_cls = f"_Pad_{class_name}"
    lp_cls = f"_LP_{class_name}"
    lines = [
        f"class {pad_cls}(Pad):",
        f"    shape = Circle(diameter={pad_str})",
        "    def __init__(self):",
        f"        self.cutout = Cutout(Circle(diameter={hole_str}))",
        "        self.soldermask = Soldermask(self.shape)",
        "",
        f"class {lp_cls}(Landpattern):",
        "    def __init__(self):",
        f"        self.p1 = {pad_cls}().at(0, 0)",
        "",
        f"class {class_name}(Component):",
        f'    """Single-pin through-hole Component (hole diameter {hole_str} mm, plating: {plating_label})."""',
        '    reference_designator_prefix = "MH"',
        "    p1 = Port()",
        f"    landpattern = {lp_cls}()",
        "    symbol = _SinglePinSymbol()",
    ]
    return lines
