"""Geometry helpers shared by mechanical importers."""

from __future__ import annotations

import math
from collections import defaultdict

from .models import (
    ArcPathSegment,
    CircleGeometry,
    ClosedPath,
    LinePathSegment,
    PathSegment,
    Point,
)

type _PointKey = tuple[int, int]

_GRID_INV = 1000


def point_key(point: Point, grid_inv: int = _GRID_INV) -> _PointKey:
    """Hash a point to a tolerance grid cell."""

    return (round(point.x * grid_inv), round(point.y * grid_inv))


def segment_endpoints(seg: PathSegment) -> tuple[Point, Point]:
    if isinstance(seg, LinePathSegment):
        return seg.start, seg.end
    if isinstance(seg, ArcPathSegment):
        return seg.start_point, seg.end_point
    msg = f"Unknown segment type: {type(seg)}"
    raise TypeError(msg)


def assemble_closed_paths(
    lines: list[tuple[Point, Point]],
    arcs: list[ArcPathSegment],
    tolerance: float = 0.001,
    source_layer: str = "",
    source_section: str = "",
) -> list[ClosedPath]:
    """Assemble disconnected LINE/ARC segments into closed paths."""

    grid_inv = round(1.0 / tolerance)
    segments: list[PathSegment] = [LinePathSegment(start=start, end=end) for start, end in lines]
    segments.extend(arcs)

    if not segments:
        return []

    adjacency: dict[_PointKey, list[tuple[int, bool]]] = defaultdict(list)
    for i, seg in enumerate(segments):
        start, end = segment_endpoints(seg)
        adjacency[point_key(start, grid_inv)].append((i, True))
        adjacency[point_key(end, grid_inv)].append((i, False))

    used = [False] * len(segments)
    paths: list[ClosedPath] = []

    for start_idx in range(len(segments)):
        if used[start_idx]:
            continue
        loop = _walk_loop(segments, adjacency, used, start_idx, grid_inv)
        if loop is not None:
            paths.append(
                ClosedPath(
                    segments=tuple(loop),
                    source_layer=source_layer,
                    source_section=source_section,
                )
            )

    return paths


def _walk_loop(
    segments: list[PathSegment],
    adjacency: dict[_PointKey, list[tuple[int, bool]]],
    used: list[bool],
    start_idx: int,
    grid_inv: int,
) -> list[PathSegment] | None:
    seg = segments[start_idx]
    start_pt, end_pt = segment_endpoints(seg)
    loop_start_key = point_key(start_pt, grid_inv)

    chain: list[PathSegment] = [seg]
    chain_indices: list[int] = [start_idx]
    used[start_idx] = True
    current_key = point_key(end_pt, grid_inv)

    for _ in range(len(segments)):
        if current_key == loop_start_key and (
            len(chain) > 1
            or isinstance(seg, ArcPathSegment)
            and abs(seg.end_angle - seg.start_angle) >= 360
        ):
            return chain

        next_seg = _find_next(adjacency, used, current_key)
        if next_seg is None:
            for idx in chain_indices:
                used[idx] = False
            return None

        seg_idx, entering_at_start = next_seg
        used[seg_idx] = True
        seg = segments[seg_idx]
        if not entering_at_start:
            seg = flip_segment(seg)

        chain.append(seg)
        chain_indices.append(seg_idx)
        _, end_pt = segment_endpoints(seg)
        current_key = point_key(end_pt, grid_inv)

    for idx in chain_indices:
        used[idx] = False
    return None


def _find_next(
    adjacency: dict[_PointKey, list[tuple[int, bool]]],
    used: list[bool],
    key: _PointKey,
) -> tuple[int, bool] | None:
    for seg_idx, is_start in adjacency.get(key, []):
        if not used[seg_idx]:
            return (seg_idx, is_start)
    return None


def flip_segment(seg: PathSegment) -> PathSegment:
    if isinstance(seg, LinePathSegment):
        return LinePathSegment(start=seg.end, end=seg.start)
    if isinstance(seg, ArcPathSegment):
        return ArcPathSegment(
            center=seg.center,
            radius=seg.radius,
            start_angle=seg.end_angle,
            end_angle=seg.start_angle,
            start_point=seg.end_point,
            end_point=seg.start_point,
        )
    msg = f"Unknown segment type: {type(seg)}"
    raise TypeError(msg)


def lwpolyline_to_closed_path(
    points: list[tuple[float, float]],
    bulges: list[float],
    layer: str,
    *,
    source_section: str = "",
) -> ClosedPath:
    """Convert a closed DXF LWPOLYLINE to a ClosedPath."""

    if len(bulges) != len(points):
        msg = (
            f"lwpolyline_to_closed_path: len(bulges)={len(bulges)} does not "
            f"match len(points)={len(points)} (layer={layer!r})"
        )
        raise ValueError(msg)

    segments: list[PathSegment] = []
    for i, point in enumerate(points):
        p1 = Point(point[0], point[1])
        p2 = Point(points[(i + 1) % len(points)][0], points[(i + 1) % len(points)][1])
        bulge = bulges[i]
        if abs(bulge) < 1e-10:
            segments.append(LinePathSegment(start=p1, end=p2))
        else:
            segments.append(bulge_to_arc(p1, p2, bulge))

    return ClosedPath(
        segments=tuple(segments),
        source_layer=layer,
        source_section=source_section,
    )


def bulge_to_arc(p1: Point, p2: Point, bulge: float) -> ArcPathSegment:
    """Convert a DXF bulge value between two points to an arc segment."""

    dx = p2.x - p1.x
    dy = p2.y - p1.y
    chord = math.hypot(dx, dy)

    if chord < 1e-12:
        return ArcPathSegment(
            center=p1,
            radius=0.0,
            start_angle=0.0,
            end_angle=0.0,
            start_point=p1,
            end_point=p2,
        )

    sagitta = bulge * chord / 2.0
    radius = abs((chord**2 / 4.0 + sagitta**2) / (2.0 * sagitta))
    midpoint = Point((p1.x + p2.x) / 2.0, (p1.y + p2.y) / 2.0)
    nx = -dy / chord
    ny = dx / chord
    d = radius - abs(sagitta)
    if bulge > 0:
        center = Point(midpoint.x + d * nx, midpoint.y + d * ny)
    else:
        center = Point(midpoint.x - d * nx, midpoint.y - d * ny)

    return ArcPathSegment(
        center=center,
        radius=radius,
        start_angle=math.degrees(math.atan2(p1.y - center.y, p1.x - center.x)),
        end_angle=(
            math.degrees(math.atan2(p1.y - center.y, p1.x - center.x))
            + math.degrees(4 * math.atan(bulge))
        ),
        start_point=p1,
        end_point=p2,
    )


def arc_from_chord(
    start: Point,
    end: Point,
    sweep_angle: float,
    *,
    loop_num: int = 0,
) -> ArcPathSegment | None:
    """Build an arc segment from an IDF chord endpoint and sweep angle."""

    chord = math.hypot(end.x - start.x, end.y - start.y)
    if chord < 1e-10:
        return None
    sin_half = math.sin(math.radians(sweep_angle / 2.0))
    if abs(sin_half) < 1e-10:
        return None

    half_chord = chord / 2.0
    radius = abs(half_chord / sin_half)
    radius_sq_minus_h2 = radius**2 - half_chord**2
    if radius_sq_minus_h2 < -1e-6:
        return None

    chord_dx = (end.x - start.x) / chord
    chord_dy = (end.y - start.y) / chord
    midpoint_to_center = math.sqrt(max(0.0, radius_sq_minus_h2))
    major_arc_sign = -1.0 if abs(sweep_angle) > 180.0 else 1.0
    direction_sign = -1.0 if sweep_angle < 0 else 1.0
    offset = midpoint_to_center * major_arc_sign * direction_sign

    mid = Point((start.x + end.x) / 2.0, (start.y + end.y) / 2.0)
    center = Point(mid.x - chord_dy * offset, mid.y + chord_dx * offset)
    start_angle = _wrap_angle(math.degrees(math.atan2(start.y - center.y, start.x - center.x)))
    end_angle = start_angle + sweep_angle

    return ArcPathSegment(
        center=center,
        radius=radius,
        start_angle=start_angle,
        end_angle=end_angle,
        start_point=start,
        end_point=end,
    )


def full_circle_from_diameter(
    start: Point, end: Point, *, layer: str = "", section: str = ""
) -> CircleGeometry | None:
    """Interpret an IDF +/-360-degree loop point as a circle with a diameter chord."""

    diameter = math.hypot(start.x - end.x, start.y - end.y)
    if diameter <= 0.0:
        return None
    return CircleGeometry(
        center=Point((start.x + end.x) / 2.0, (start.y + end.y) / 2.0),
        radius=diameter / 2.0,
        source_layer=layer,
        source_section=section,
    )


def path_bounding_box(path: ClosedPath) -> tuple[Point, Point]:
    xs: list[float] = []
    ys: list[float] = []

    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            xs.extend([seg.start.x, seg.end.x])
            ys.extend([seg.start.y, seg.end.y])
        elif isinstance(seg, ArcPathSegment):
            xs.extend([seg.start_point.x, seg.end_point.x])
            ys.extend([seg.start_point.y, seg.end_point.y])
            _arc_bbox_extend(seg, xs, ys)

    if not xs:
        return (Point(0.0, 0.0), Point(0.0, 0.0))
    return (Point(min(xs), min(ys)), Point(max(xs), max(ys)))


def geometry_bounding_box(geometry) -> tuple[Point, Point]:
    if isinstance(geometry, CircleGeometry):
        return (
            Point(geometry.center.x - geometry.radius, geometry.center.y - geometry.radius),
            Point(geometry.center.x + geometry.radius, geometry.center.y + geometry.radius),
        )
    return path_bounding_box(geometry)


def _arc_bbox_extend(arc: ArcPathSegment, xs: list[float], ys: list[float]) -> None:
    for angle in [0.0, 90.0, 180.0, 270.0]:
        if angle_in_arc(angle, arc.start_angle, arc.end_angle):
            rad = math.radians(angle)
            xs.append(arc.center.x + arc.radius * math.cos(rad))
            ys.append(arc.center.y + arc.radius * math.sin(rad))


def angle_in_arc(angle: float, start: float, end: float) -> bool:
    sweep = end - start
    if abs(sweep) >= 360:
        return True
    distance = (angle - start) % 360 if sweep >= 0 else (start - angle) % 360
    return distance <= abs(sweep) + 1e-10


def path_area(path: ClosedPath) -> float:
    area = 0.0
    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            area += seg.start.x * seg.end.y - seg.end.x * seg.start.y
        elif isinstance(seg, ArcPathSegment):
            area += seg.start_point.x * seg.end_point.y - seg.end_point.x * seg.start_point.y
            area += 2 * arc_segment_area(seg)
    return area / 2.0


def arc_segment_area(arc: ArcPathSegment) -> float:
    if arc.radius < 1e-12:
        return 0.0
    sweep = arc.end_angle - arc.start_angle
    sweep_rad = math.radians(sweep)
    return arc.radius**2 * (sweep_rad - math.sin(sweep_rad)) / 2.0


def geometry_area(geometry) -> float:
    if isinstance(geometry, CircleGeometry):
        return math.pi * geometry.radius**2
    return path_area(geometry)


def point_in_geometry(point: Point, geometry) -> bool:
    if isinstance(geometry, CircleGeometry):
        return (
            math.hypot(point.x - geometry.center.x, point.y - geometry.center.y) <= geometry.radius
        )
    return point_in_path(point, geometry)


def point_in_path(point: Point, path: ClosedPath) -> bool:
    crossings = 0
    px, py = point.x, point.y

    for seg in path.segments:
        if isinstance(seg, LinePathSegment):
            crossings += _ray_crosses_line(px, py, seg)
        elif isinstance(seg, ArcPathSegment):
            crossings += _ray_crosses_arc(px, py, seg)
    return crossings % 2 == 1


def _ray_crosses_line(px: float, py: float, seg: LinePathSegment) -> int:
    y1, y2 = seg.start.y, seg.end.y
    x1, x2 = seg.start.x, seg.end.x
    if (y1 <= py < y2) or (y2 <= py < y1):
        t = (py - y1) / (y2 - y1)
        x_intersect = x1 + t * (x2 - x1)
        if x_intersect > px:
            return 1
    return 0


def _ray_crosses_arc(px: float, py: float, arc: ArcPathSegment) -> int:
    steps = max(8, int(abs(arc.end_angle - arc.start_angle) / 5.0))
    crossings = 0
    sweep = arc.end_angle - arc.start_angle
    points = []
    for i in range(steps + 1):
        angle = math.radians(arc.start_angle + (i / steps) * sweep)
        points.append(
            Point(
                arc.center.x + arc.radius * math.cos(angle),
                arc.center.y + arc.radius * math.sin(angle),
            )
        )
    for i in range(len(points) - 1):
        crossings += _ray_crosses_line(px, py, LinePathSegment(points[i], points[i + 1]))
    return crossings


def geometry_center(geometry) -> Point:
    if isinstance(geometry, CircleGeometry):
        return geometry.center
    bb_min, bb_max = path_bounding_box(geometry)
    return Point((bb_min.x + bb_max.x) / 2.0, (bb_min.y + bb_max.y) / 2.0)


def _wrap_angle(angle: float) -> float:
    wrapped = angle - 360.0 * math.floor(angle / 360.0)
    if wrapped >= 360.0 - 1e-6:
        return 0.0
    return wrapped
