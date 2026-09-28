from __future__ import annotations

import pytest

from jitxlib.mechanical.geometry import (
    assemble_closed_paths,
    lwpolyline_to_closed_path,
    path_area,
    point_in_path,
)
from jitxlib.mechanical.models import ArcPathSegment, LinePathSegment, Point


def test_assemble_rectangle_path():
    lines = [
        (Point(0, 0), Point(10, 0)),
        (Point(10, 0), Point(10, 5)),
        (Point(10, 5), Point(0, 5)),
        (Point(0, 5), Point(0, 0)),
    ]
    paths = assemble_closed_paths(lines, [])
    assert len(paths) == 1
    assert len(paths[0].segments) == 4
    assert path_area(paths[0]) == pytest.approx(50.0)
    assert point_in_path(Point(5, 2), paths[0])


def test_lwpolyline_bulge_creates_arc():
    path = lwpolyline_to_closed_path(
        [(0, 0), (10, 0), (10, 10), (0, 10)],
        [0.5, 0.0, 0.0, 0.0],
        "OUTLINE",
    )
    assert isinstance(path.segments[0], ArcPathSegment)
    assert isinstance(path.segments[1], LinePathSegment)


def test_lwpolyline_length_mismatch_raises():
    with pytest.raises(ValueError, match="len\\(bulges\\)"):
        lwpolyline_to_closed_path([(0, 0), (1, 0)], [0.0], "bad")


@pytest.mark.parametrize("bulge", [-2.0, -0.5, 0.5, 2.0])
def test_bulge_preserves_signed_major_sweep(bulge):
    import math

    from jitxlib.mechanical.geometry import bulge_to_arc

    arc = bulge_to_arc(Point(0, 0), Point(10, 0), bulge)
    assert arc.end_angle - arc.start_angle == pytest.approx(math.degrees(4 * math.atan(bulge)))
    assert arc.radius == pytest.approx(math.dist((arc.center.x, arc.center.y), (0, 0)))


@pytest.mark.parametrize("sweep", [-270, -180, -90, 90, 180, 270])
def test_arc_area_and_bounds(sweep):
    import math

    from jitxlib.mechanical.geometry import arc_from_chord, geometry_bounding_box
    from jitxlib.mechanical.models import ClosedPath

    arc = arc_from_chord(Point(-1, 0), Point(1, 0), sweep)
    assert arc is not None
    path = ClosedPath((arc, LinePathSegment(arc.end_point, arc.start_point)))
    theta = math.radians(sweep)
    assert path_area(path) == pytest.approx(arc.radius**2 * (theta - math.sin(theta)) / 2)
    low, high = geometry_bounding_box(path)
    for i in range(101):
        angle = math.radians(arc.start_angle + sweep * i / 100)
        x = arc.center.x + arc.radius * math.cos(angle)
        y = arc.center.y + arc.radius * math.sin(angle)
        assert low.x - 1e-9 <= x <= high.x + 1e-9
        assert low.y - 1e-9 <= y <= high.y + 1e-9
