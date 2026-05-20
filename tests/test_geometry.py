from __future__ import annotations

import pytest

from jitx_mechanical.geometry import (
    assemble_closed_paths,
    lwpolyline_to_closed_path,
    path_area,
    point_in_path,
)
from jitx_mechanical.models import ArcPathSegment, LinePathSegment, Point


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
