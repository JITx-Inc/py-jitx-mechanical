"""Generate user-editable JITX Python from mechanical import data."""

from __future__ import annotations

import math
import re

from .geometry import geometry_bounding_box
from .models import (
    ArcPathSegment,
    CircleGeometry,
    ClosedPath,
    Geometry,
    HolePlating,
    LinePathSegment,
    MechanicalComponent,
    MechanicalImport,
    Point,
)

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
    """Generate a Board-focused Python module from mechanical import data."""

    clean_class = sanitize_identifier(class_name)
    offset = _recenter_offset(imported.board_outline, recenter)
    imports = _imports(imported)
    lines: list[str] = []

    source = module_name or imported.source_path
    lines.append(f'"""Board geometry imported from {source}."""')
    lines.append("")
    lines.extend(imports)
    lines.append("")
    lines.append("")
    lines.append(f"class {clean_class}(Board):")
    if imported.board_outline is None:
        lines.append("    shape = None  # No board outline was detected; fill this in manually.")
    else:
        lines.append(
            "    shape = "
            + geometry_to_code(imported.board_outline, offset=offset, indent=1, precision=precision)
        )

    lines.append("")
    lines.append("")
    lines.append("# Interior board cutout geometry imported from the source file.")
    lines.append("# Attach these shapes to your board or circuit according to your JITX workflow.")
    lines.append("BOARD_CUTOUTS = [")
    for cutout in imported.board_cutouts:
        lines.append(
            "    "
            + geometry_to_code(cutout, offset=offset, indent=1, precision=precision)
            + ","
        )
    lines.append("]")

    lines.append("")
    lines.append("MECHANICAL_ANNOTATIONS = [")
    for annotation in imported.annotations:
        x = _fmt(annotation.position.x + offset.x, precision=precision)
        y = _fmt(annotation.position.y + offset.y, precision=precision)
        lines.append(
            "    "
            + "{"
            + f'"role": "{_escape(annotation.role)}", '
            + f'"text": "{_escape(annotation.text)}", '
            + f'"at": ({x}, {y}), '
            + f'"height": {_fmt(annotation.height, precision=precision)}, '
            + f'"source": "{_escape(annotation.source_name)}"'
            + "},"
        )
    lines.append("]")

    lines.append("")
    if imported.mechanical_components:
        lines.append(
            "# Mechanical/plated holes are emitted as connectable JITX Component"
        )
        lines.append(
            "# classes in the companion `_components.py` module — instantiate"
        )
        lines.append(
            "# `MechanicalComponentsCircuit` there and net its ports into your design."
        )
    else:
        lines.append("MECHANICAL_COMPONENTS = []")

    if imported.messages:
        lines.append("")
        lines.append("IMPORT_MESSAGES = [")
        for message in imported.messages:
            lines.append(
                "    "
                + "{"
                + f'"severity": "{message.severity.value}", '
                + f'"message": "{_escape(message.text)}", '
                + f'"source": "{_escape(message.source)}", '
                + f'"hint": "{_escape(message.hint)}"'
                + "},"
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

    needs_arc_polygon = any(
        isinstance(geometry, ClosedPath)
        and any(isinstance(seg, ArcPathSegment) for seg in geometry.segments)
        for geometry in geometries
    )
    needs_circle = any(isinstance(geometry, CircleGeometry) for geometry in geometries)
    needs_polygon = any(isinstance(geometry, ClosedPath) for geometry in geometries)

    shape_imports: list[str] = []
    if needs_arc_polygon:
        shape_imports.extend(["Arc", "ArcPolygon"])
    if needs_circle:
        shape_imports.append("Circle")
    if needs_polygon:
        shape_imports.append("Polygon")

    lines = ["from jitx.board import Board"]
    if shape_imports:
        lines.append(f"from jitx.shapes.primitive import {', '.join(shape_imports)}")
    return lines


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


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def sanitize_identifier(name: str) -> str:
    if name and (name[0].isalpha() or name[0] == "_"):
        return re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return "_" + re.sub(r"[^a-zA-Z0-9_]", "_", name)


def generate_components_module(
    imported: MechanicalImport,
    *,
    module_name: str | None = None,
    recenter: bool = True,
    precision: int = DEFAULT_PRECISION,
    annular_pad_margin: float = DEFAULT_ANNULAR_PAD_MARGIN_MM,
) -> str:
    """Generate a JITX Component + Circuit module for plated/mounting holes.

    Returns the empty string when the import has no mechanical components,
    so callers can guard with `if code:` to decide whether to write the file.
    """
    components = imported.mechanical_components
    if not components:
        return ""

    offset = _recenter_offset(imported.board_outline, recenter)
    source = module_name or imported.source_path

    header = [
        f'"""Mechanical components (plated and mounting holes) imported from {source}.',
        "",
        "Each unique hole geometry becomes a single-pin Component class with a",
        "through-hole pad. `MechanicalComponentsCircuit` instantiates them at the",
        "placements detected in the source file.",
        "",
        f"NOTE: the source file only carries hole diameters. Annular ring and",
        f"soldermask diameters are assumed at `hole_diameter + 2 * {annular_pad_margin} mm`.",
        "Edit the Pad subclasses below if your fab requires different copper.",
        '"""',
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
    body.append('    """All imported mechanical/plated holes placed at their source coordinates.')
    body.append("")
    body.append("    Net the exposed ports into your design — for example, connect every")
    body.append("    PLATED hole's `.p1` to a chassis-ground net at the Design level.")
    body.append('    """')
    body.append("")
    body.append("    def __init__(self):")
    body.append("        super().__init__()")
    if not class_names:
        body.append("        pass")
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
        f"        self.p1 = {pad_cls}()",
        "",
        f"class {class_name}(Component):",
        f'    """Single-pin through-hole Component (hole diameter {hole_str} mm, plating: {plating_label})."""',
        '    reference_designator_prefix = "MH"',
        "    p1 = Port()",
        f"    landpattern = {lp_cls}()",
        "    symbol = _SinglePinSymbol()",
    ]
    return lines
