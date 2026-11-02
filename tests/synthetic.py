"""Synthetic IDF and DXF inputs for importer tests."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

import ezdxf

Rect = tuple[float, float, float, float]


def _loop(rect: Rect, loop: int = 0) -> str:
    x0, y0, x1, y1 = rect
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
    return "\n".join(f"{loop} {x} {y} 0" for x, y in corners)


def route_keepout(layers: str, rect: Rect) -> str:
    return f".ROUTE_KEEPOUT ECAD\n{layers}\n{_loop(rect)}\n.END_ROUTE_KEEPOUT"


def via_keepout(rect: Rect) -> str:
    return f".VIA_KEEPOUT ECAD\n{_loop(rect)}\n.END_VIA_KEEPOUT"


def place_keepout(side: str, rect: Rect, height: float = 0.0) -> str:
    return f".PLACE_KEEPOUT ECAD\n{side} {height}\n{_loop(rect)}\n.END_PLACE_KEEPOUT"


def write_emn(
    path: Path,
    *,
    outline: Rect = (0, 0, 50, 30),
    cutouts: Sequence[Rect] = (),
    holes: Iterable[tuple[float, float, float, str]] = (),
    sections: Iterable[str] = (),
    notes: Iterable[tuple[float, float, float, str]] = (),
) -> Path:
    """Write an IDF 3.0 board file in millimeters.

    Args:
        path: Destination file.
        outline: Board outline rectangle as (x0, y0, x1, y1).
        cutouts: Rectangles added to the board outline as interior loops.
        holes: Drilled holes as (diameter, x, y, plating).
        sections: Additional section text, such as from `route_keepout`.
        notes: Notes as (x, y, text height, text).

    Returns:
        The written path.
    """
    loops = [_loop(outline, 0)] + [_loop(rect, i + 1) for i, rect in enumerate(cutouts)]
    parts = [
        '.HEADER\nBOARD_FILE 3.0 "synthetic" 2026/01/01.00:00:00 1\n"Synthetic" MM\n.END_HEADER',
        ".BOARD_OUTLINE ECAD\n1.6\n" + "\n".join(loops) + "\n.END_BOARD_OUTLINE",
    ]
    hole_lines = [f"{d} {x} {y} {plating} BOARD MTG ECAD" for d, x, y, plating in holes]
    if hole_lines:
        parts.append(".DRILLED_HOLES\n" + "\n".join(hole_lines) + "\n.END_DRILLED_HOLES")
    parts.extend(sections)
    note_lines = [f'{x} {y} {h} 20 "{text}"' for x, y, h, text in notes]
    if note_lines:
        parts.append(".NOTES\n" + "\n".join(note_lines) + "\n.END_NOTES")
    path.write_text("\n".join(parts) + "\n")
    return path


def write_dxf(
    path: Path,
    *,
    version: str = "R2010",
    outline: Rect = (0, 0, 40, 20),
    outline_layer: str = "OUTLINE",
    outline_as_lines: bool = False,
    slots: Sequence[Rect] = (),
    slot_layer: str = "SLOTS",
    circles: Iterable[tuple[float, float, float]] = (),
    circle_layer: str = "HOLES",
) -> Path:
    """Write a millimeter DXF board drawing.

    Args:
        path: Destination file.
        version: DXF version passed to `ezdxf.new`; "R12" writes no units.
        outline: Board outline rectangle as (x0, y0, x1, y1).
        outline_layer: Layer for the outline.
        outline_as_lines: Draw the outline as LINE entities instead of a polyline.
        slots: Closed rectangular paths inside the outline.
        slot_layer: Layer for the slots.
        circles: Circles as (x, y, radius).
        circle_layer: Layer for the circles.

    Returns:
        The written path.
    """
    doc = ezdxf.new(version)
    if version != "R12":
        doc.units = ezdxf.units.MM
    msp = doc.modelspace()

    def rect_points(rect: Rect) -> list[tuple[float, float]]:
        x0, y0, x1, y1 = rect
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]

    corners = rect_points(outline)
    if outline_as_lines:
        for start, end in zip(corners, corners[1:] + corners[:1], strict=True):
            msp.add_line(start, end, dxfattribs={"layer": outline_layer})
    else:
        msp.add_lwpolyline(corners, close=True, dxfattribs={"layer": outline_layer})
    for rect in slots:
        points = rect_points(rect)
        for start, end in zip(points, points[1:] + points[:1], strict=True):
            msp.add_line(start, end, dxfattribs={"layer": slot_layer})
    for x, y, radius in circles:
        msp.add_circle((x, y), radius, dxfattribs={"layer": circle_layer})
    doc.saveas(path)
    return path


def write_headerless_dxf(path: Path, outline: Rect = (0, 0, 40, 20)) -> Path:
    """Write a DXF with only an ENTITIES section, as minimal exporters do.

    Without a HEADER section the file cannot declare units, and ezdxf reads it as R12.

    Args:
        path: Destination file.
        outline: Board outline rectangle as (x0, y0, x1, y1), drawn with LINE entities.

    Returns:
        The written path.
    """
    x0, y0, x1, y1 = outline
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    lines = ["0", "SECTION", "2", "ENTITIES"]
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1], strict=True):
        lines += ["0", "LINE", "8", "OUTLINE", "10", str(ax), "20", str(ay)]
        lines += ["11", str(bx), "21", str(by)]
    lines += ["0", "ENDSEC", "0", "EOF"]
    path.write_text("\n".join(lines) + "\n")
    return path
