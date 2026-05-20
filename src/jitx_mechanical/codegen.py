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
    LinePathSegment,
    MechanicalImport,
    Point,
)

DEFAULT_PRECISION = 4


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
    lines.append("MECHANICAL_COMPONENTS = [")
    for component in imported.mechanical_components:
        placements = ", ".join(
            f"({_fmt(p.x + offset.x, precision=precision)}, {_fmt(p.y + offset.y, precision=precision)})"
            for p in component.placements
        )
        lines.append(
            "    "
            + "{"
            + f'"name": "{_escape(component.name)}", '
            + f'"hole_radius": {_fmt(component.geometry.radius, precision=precision)}, '
            + f'"plating": "{component.plating.value}", '
            + f'"placements": [{placements}]'
            + "},"
        )
    lines.append("]")

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
