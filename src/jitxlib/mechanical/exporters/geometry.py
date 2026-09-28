"""Convert JITX primitives to placed DXF geometry without tessellating arcs."""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence

from jitx.shapes import Shape
from jitx.shapes import primitive as p
from jitx.transform import IDENTITY, Transform

from . import model


class _Placement:
    def __init__(self, transform: Transform):
        self.transform = transform
        origin = transform * (0.0, 0.0)
        px, py = transform * (1.0, 0.0), transform * (0.0, 1.0)
        x = (px[0] - origin[0], px[1] - origin[1])
        y = (py[0] - origin[0], py[1] - origin[1])
        sx, sy = math.hypot(*x), math.hypot(*y)
        if not all(math.isfinite(v) for v in (*origin, *x, *y)):
            raise ValueError("DXF geometry has a non-finite transform")
        if (
            sx == 0
            or not math.isclose(sx, sy, rel_tol=1e-9)
            or not math.isclose(x[0] * y[0] + x[1] * y[1], 0.0, abs_tol=1e-9 * sx * sy)
        ):
            raise ValueError("DXF geometry requires a uniform, nonzero scale without shear")
        self.scale = sx
        self.mirrored = x[0] * y[1] - x[1] * y[0] < 0
        self.rotation = math.degrees(math.atan2(x[1], x[0])) % 360

    def point(self, point: model.Point) -> model.Point:
        placed = self.transform * point
        if not all(math.isfinite(v) for v in placed):
            raise ValueError("DXF geometry contains a non-finite coordinate")
        return placed


def _arc_point(arc: p.Arc, angle: float) -> model.Point:
    radians = math.radians(angle)
    return (
        arc.center[0] + arc.radius * math.cos(radians),
        arc.center[1] + arc.radius * math.sin(radians),
    )


def _path(
    elements: Sequence[p.Arc | model.Point],
    placement: _Placement,
    *,
    closed: bool,
    width: float = 0,
) -> model.Path:
    vertices: list[tuple[float, float, float]] = []

    def vertex(point: model.Point, bulge: float = 0) -> None:
        x, y = placement.point(point)
        if vertices and math.dist(vertices[-1][:2], (x, y)) < 1e-9:
            vertices[-1] = (x, y, bulge)
        else:
            vertices.append((x, y, bulge))

    for element in elements:
        if isinstance(element, p.Arc):
            if not math.isfinite(element.arc) or abs(element.arc) > 360 or element.radius < 0:
                raise ValueError(
                    "DXF arc requires a finite sweep within one revolution and nonnegative radius"
                )
            if element.radius == 0 or element.arc == 0:
                vertex(_arc_point(element, element.start))
                continue
            segments = 2 if abs(element.arc) >= 360 - 1e-9 else 1
            sweep = element.arc / segments
            bulge = math.tan(math.radians(sweep) / 4) * (-1 if placement.mirrored else 1)
            for i in range(segments):
                vertex(_arc_point(element, element.start + i * sweep), bulge)
                vertex(_arc_point(element, element.start + (i + 1) * sweep))
        else:
            vertex(element)
    if closed and len(vertices) > 1 and math.dist(vertices[0][:2], vertices[-1][:2]) < 1e-9:
        vertices.pop()
    if len(vertices) < 2:
        raise ValueError("DXF path contains fewer than two distinct vertices")
    if not math.isfinite(width) or width < 0:
        raise ValueError("DXF path width must be finite and nonnegative")
    return model.Path(tuple(vertices), closed, width * placement.scale)


def shape_geometry(
    shape: Shape,
    transform: Transform = IDENTITY,
) -> Iterator[tuple[model.Geometry, bool]]:
    """Yield geometry and whether it is an interior polygon boundary."""
    primitive = shape.to_primitive()
    placement = _Placement(transform * primitive.transform)
    geometry = primitive.geometry
    match geometry:
        case p.Empty():
            return
        case p.Circle():
            if not math.isfinite(geometry.radius) or geometry.radius <= 0:
                raise ValueError("DXF circle radius must be finite and positive")
            yield (
                model.Circle(placement.point((0.0, 0.0)), geometry.radius * placement.scale),
                False,
            )
        case p.Polygon():
            yield _path(geometry.elements, placement, closed=True), False
            for hole in geometry.holes:
                yield _path(hole, placement, closed=True), True
        case p.PolygonSet():
            for polygon in geometry.polygons:
                yield _path(polygon.elements, placement, closed=True), False
                for hole in polygon.holes:
                    yield _path(hole, placement, closed=True), True
        case p.ArcPolygon():
            yield _path(geometry.elements, placement, closed=True), False
        case p.Polyline() | p.ArcPolyline():
            yield _path(geometry.elements, placement, closed=False, width=geometry.width), False
        case p.Text():
            if not math.isfinite(geometry.size) or geometry.size <= 0:
                raise ValueError("DXF text height must be finite and positive")
            yield (
                model.Text(
                    placement.point((0.0, 0.0)),
                    geometry.string,
                    geometry.size * placement.scale,
                    placement.rotation,
                    placement.mirrored,
                    geometry.anchor.value,
                ),
                False,
            )
        case _:
            raise ValueError(f"Unsupported DXF shape: {type(geometry).__name__}")
