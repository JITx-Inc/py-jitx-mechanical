"""DXF export from JITX XML board data."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field

from ezdxf import units as ezdxf_units
from ezdxf.document import Drawing
from ezdxf.filemanagement import new as ezdxf_new
from ezdxf.layouts.layout import Modelspace

from jitx_mechanical.models import (
    ArcShape,
    BoardData,
    CirclePad,
    CircleShape,
    CopperArc,
    CopperLine,
    CopperPolygon,
    CopperShape,
    Instance,
    LineShape,
    Package,
    Point,
    PolygonPad,
    PolygonShape,
    Pose,
    RectanglePad,
    RectangleShape,
    TextShape,
    Via,
)
from jitx_mechanical.transforms import transform_angle, transform_point
from jitx_mechanical.xml_parser import parse_xml


@dataclass(frozen=True)
class DxfExportConfig:
    """Configuration for DXF export.

    `layers=None` exports every generated layer.  Otherwise, only the named DXF
    layers are emitted.  The remaining switches provide a stable public API for
    callers that need partial mechanical exports.
    """

    layers: set[str] | None = None
    units: str = "mm"
    include_board_outline: bool = True
    include_components: bool = True
    include_pads: bool = True
    include_drill: bool = True
    include_vias: bool = True
    include_copper: bool = True
    include_annotations: bool = True
    layer_colors: dict[str, int] = field(default_factory=dict)

# ─── DXF Layer Setup ─────────────────────────────────────────────────────

LAYER_DEFS: dict[str, dict[str, int]] = {
    "BoardOutline": {"color": 7},
    "Pads_Top": {"color": 1},
    "Pads_Bottom": {"color": 5},
    "Vias": {"color": 8},
    "Drill": {"color": 8},
    "Silkscreen_Top": {"color": 3},
    "Silkscreen_Bottom": {"color": 4},
    "Courtyard_Top": {"color": 6},
    "Courtyard_Bottom": {"color": 2},
    "Soldermask_Top": {"color": 94},
    "Soldermask_Bottom": {"color": 96},
    "Paste_Top": {"color": 9},
    "Paste_Bottom": {"color": 9},
    "Finish_Top": {"color": 40},
    "Finish_Bottom": {"color": 40},
    "Components": {"color": 7},
}

# Colors for copper layers by index (cycling if more than available)
COPPER_COLORS = [30, 140, 170, 200, 50, 110]


def copper_layer_name(layer_index: int, layer_names: dict[int, str], prefix: str = "Copper") -> str:
    """Generate a DXF layer name for a copper layer index."""
    stackup_name = layer_names.get(layer_index, f"L{layer_index}")
    return f"{prefix}_{stackup_name}"


def get_dxf_layer(layer_name: str, side: str) -> str:
    """Map an XML LAYER-SPECIFIER NAME + SIDE to a DXF layer name.

    Well-known PCB layer names are normalized to Title_Case for consistency.
    Unknown names are passed through with the side suffix.
    """
    if _is_drill_layer(layer_name):
        return "Drill"
    normalized = _LAYER_NAME_MAP.get(layer_name)
    if normalized is not None:
        return f"{normalized}_{side}"
    return f"{layer_name}_{side}"


# Normalized display names for well-known XML LAYER-SPECIFIER NAME values.
_LAYER_NAME_MAP: dict[str, str] = {
    "SILKSCREEN": "Silkscreen",
    "COURTYARD": "Courtyard",
    "SOLDERMASK": "Soldermask",
    "PASTE": "Paste",
    "FINISH": "Finish",
}


def _flip_side(side: str) -> str:
    """Return the opposite board side."""
    return "Bottom" if side == "Top" else "Top"


def _is_drill_layer(layer_name: str) -> bool:
    return layer_name.upper() in {"DRILL", "HOLE"}


def resolve_side(shape_side: str, inst_side: str) -> str:
    """Resolve a shape's layer side for a given instance placement.

    Package shapes are defined relative to a Top-side placement.  When the
    instance is on the Bottom, every Top shape must flip to Bottom and vice
    versa.
    """
    if inst_side == "Bottom":
        return _flip_side(shape_side)
    return shape_side


_DYNAMIC_COLORS = [10, 20, 30, 50, 70, 90, 110, 130, 150, 170, 190, 210]


def _collect_layers(data: BoardData) -> set[str]:
    """Pre-scan board data to determine all DXF layers that will be used."""
    layers: set[str] = set()

    if data.boundary_lines or data.boundary_arcs:
        layers.add("BoardOutline")
    if data.vias:
        layers.update(["Vias", "Drill"])
    if data.instances:
        layers.add("Components")

    for _idx, name in data.layer_names.items():
        layers.add(f"Copper_{name}")
    for track in data.tracks:
        layers.add(copper_layer_name(track.layer_index, data.layer_names, "Copper"))
    for fill in data.fills:
        layers.add(copper_layer_name(fill.layer_index, data.layer_names, "Copper"))

    for inst in data.instances:
        pkg = data.packages.get(inst.package_name)
        if pkg is None:
            continue
        pad_layer = f"Pads_{inst.side}"
        if pkg.pads or pkg.rectangle_pads or pkg.polygon_pads:
            layers.add(pad_layer)
        if any(p.hole_radius > 0 for p in pkg.pads) or \
           any(p.hole_radius > 0 for p in pkg.rectangle_pads) or \
           any(p.hole_radius > 0 for p in pkg.polygon_pads):
            layers.add("Drill")
        for poly in pkg.polygons:
            resolved = resolve_side(poly.side, inst.side)
            layers.add(get_dxf_layer(poly.layer_name, resolved))
        for ls in pkg.lines:
            resolved = resolve_side(ls.side, inst.side)
            layers.add(get_dxf_layer(ls.layer_name, resolved))
        for circle in pkg.circles:
            resolved = resolve_side(circle.side, inst.side)
            layers.add(get_dxf_layer(circle.layer_name, resolved))
        for arc in pkg.arcs:
            resolved = resolve_side(arc.side, inst.side)
            layers.add(get_dxf_layer(arc.layer_name, resolved))
        for rect in pkg.rectangles:
            resolved = resolve_side(rect.side, inst.side)
            layers.add(get_dxf_layer(rect.layer_name, resolved))
        for text in pkg.texts:
            resolved = resolve_side(text.side, inst.side)
            layers.add(get_dxf_layer(text.layer_name, resolved))
        for ts in inst.shapes_text:
            layers.add(get_dxf_layer(ts.layer_name, ts.side))
        for poly in inst.shapes_polygon:
            layers.add(get_dxf_layer(poly.layer_name, poly.side))
        for ls in inst.shapes_line:
            layers.add(get_dxf_layer(ls.layer_name, ls.side))
        for circle in inst.shapes_circle:
            layers.add(get_dxf_layer(circle.layer_name, circle.side))
        for arc in inst.shapes_arc:
            layers.add(get_dxf_layer(arc.layer_name, arc.side))
        for rect in inst.shapes_rectangle:
            layers.add(get_dxf_layer(rect.layer_name, rect.side))

    for shape in data.board_shapes:
        layers.add(get_dxf_layer(shape.layer_name, shape.side))
    for ls in data.board_line_shapes:
        layers.add(get_dxf_layer(ls.layer_name, ls.side))
    for circle in data.board_circle_shapes:
        layers.add(get_dxf_layer(circle.layer_name, circle.side))
    for arc in data.board_arc_shapes:
        layers.add(get_dxf_layer(arc.layer_name, arc.side))
    for rect in data.board_rectangle_shapes:
        layers.add(get_dxf_layer(rect.layer_name, rect.side))
    for text in data.board_text_shapes:
        layers.add(get_dxf_layer(text.layer_name, text.side))

    return layers


def setup_layers(doc: Drawing, data: BoardData, config: DxfExportConfig | None = None) -> None:
    """Create all DXF layers that will be used, with appropriate colors."""
    config = config or DxfExportConfig()
    needed = _collect_layers(data)
    dynamic_idx = 0

    for layer_name in sorted(needed):
        if layer_name in config.layer_colors:
            doc.layers.add(layer_name, color=config.layer_colors[layer_name])
        elif layer_name in LAYER_DEFS:
            doc.layers.add(layer_name, color=LAYER_DEFS[layer_name]["color"])
        elif layer_name.startswith("Copper_"):
            # Derive color from the copper layer index
            idx = next(
                (i for i, n in data.layer_names.items()
                 if f"Copper_{n}" == layer_name),
                dynamic_idx,
            )
            color = COPPER_COLORS[idx % len(COPPER_COLORS)]
            doc.layers.add(layer_name, color=color)
        else:
            color = _DYNAMIC_COLORS[dynamic_idx % len(_DYNAMIC_COLORS)]
            doc.layers.add(layer_name, color=color)
            dynamic_idx += 1


def _effective_layer_filter(data: BoardData, config: DxfExportConfig) -> set[str] | None:
    """Apply inclusion switches to a DXF layer filter."""

    if all(
        [
            config.include_board_outline,
            config.include_components,
            config.include_pads,
            config.include_drill,
            config.include_vias,
            config.include_copper,
            config.include_annotations,
        ]
    ):
        return config.layers

    layers = set(config.layers) if config.layers is not None else _collect_layers(data)
    if not config.include_board_outline:
        layers.discard("BoardOutline")
    if not config.include_components:
        layers.discard("Components")
    if not config.include_pads:
        layers = {layer for layer in layers if not layer.startswith("Pads_")}
    if not config.include_drill:
        layers.discard("Drill")
    if not config.include_vias:
        layers.discard("Vias")
    if not config.include_copper:
        layers = {layer for layer in layers if not layer.startswith("Copper_")}
    if not config.include_annotations:
        layers = {
            layer
            for layer in layers
            if not (
                layer.startswith("Silkscreen_")
                or layer.startswith("Courtyard_")
                or layer.startswith("Soldermask_")
                or layer.startswith("Paste_")
                or layer.startswith("Finish_")
            )
        }
    return layers


# ─── DXF Emission Helpers ─────────────────────────────────────────────────


def _add_wide_line(
    msp: Modelspace,
    p1: tuple[float, float],
    p2: tuple[float, float],
    width: float,
    layer: str,
) -> None:
    """Emit a line with physical width as a 2-vertex LWPOLYLINE.

    Uses per-vertex start/end width (xyseb format) for maximum viewer
    compatibility — some DXF viewers ignore const_width.
    """
    # xyseb format: x, y, start_width, end_width, bulge
    msp.add_lwpolyline(
        [(p1[0], p1[1], width, width, 0.0), (p2[0], p2[1], width, width, 0.0)],
        dxfattribs={"layer": layer},
        format="xyseb",
    )


def _add_wide_arc(
    msp: Modelspace,
    center: tuple[float, float],
    radius: float,
    start_angle: float,
    end_angle: float,
    width: float,
    layer: str,
) -> None:
    """Emit an arc with physical width as a 2-vertex LWPOLYLINE with bulge."""
    sa_rad = math.radians(start_angle)
    ea_rad = math.radians(end_angle)
    p1x = center[0] + radius * math.cos(sa_rad)
    p1y = center[1] + radius * math.sin(sa_rad)
    p2x = center[0] + radius * math.cos(ea_rad)
    p2y = center[1] + radius * math.sin(ea_rad)
    # Included angle (CCW from start to end)
    included = end_angle - start_angle
    if included <= 0:
        included += 360.0
    # Bulge = tan(included_angle / 4), positive for CCW
    bulge = math.tan(math.radians(included) / 4.0)
    # xyseb format: x, y, start_width, end_width, bulge
    msp.add_lwpolyline(
        [(p1x, p1y, width, width, bulge), (p2x, p2y, width, width, 0.0)],
        dxfattribs={"layer": layer},
        format="xyseb",
    )


# ─── DXF Emission ────────────────────────────────────────────────────────


def emit_board_outline(msp: Modelspace, data: BoardData) -> None:
    for line in data.boundary_lines:
        msp.add_line(
            start=(line.p1.x, line.p1.y),
            end=(line.p2.x, line.p2.y),
            dxfattribs={"layer": "BoardOutline"},
        )
    for arc in data.boundary_arcs:
        msp.add_arc(
            center=(arc.center.x, arc.center.y),
            radius=arc.radius,
            start_angle=arc.start_angle,
            end_angle=arc.end_angle,
            dxfattribs={"layer": "BoardOutline"},
        )


def _emit_drill_hole(
    msp: Modelspace, center: Point, hole_radius: float, inst_pose: Pose
) -> None:
    """Emit a drill hole circle on the Drill layer if hole_radius > 0."""
    if hole_radius <= 0.0:
        return
    board_pt = transform_point(center, inst_pose)
    msp.add_circle(
        center=(board_pt.x, board_pt.y),
        radius=hole_radius,
        dxfattribs={"layer": "Drill"},
    )


def emit_pads(
    msp: Modelspace, pads: list[CirclePad], pose: Pose, side: str
) -> None:
    layer = f"Pads_{side}"
    for pad in pads:
        board_pt = transform_point(pad.center, pose)
        msp.add_circle(
            center=(board_pt.x, board_pt.y),
            radius=pad.radius,
            dxfattribs={"layer": layer},
        )


def emit_rectangle_pads(
    msp: Modelspace,
    rectangle_pads: list[RectanglePad],
    inst_pose: Pose,
    side: str,
) -> None:
    layer = f"Pads_{side}"
    for pad in rectangle_pads:
        # Build rectangle corners in rect-local coordinates
        hw = pad.width / 2.0
        hh = pad.height / 2.0
        corners = [
            Point(-hw, -hh),
            Point(hw, -hh),
            Point(hw, hh),
            Point(-hw, hh),
        ]
        # Transform: rect-local -> pad-local (via rect_pose) -> package-local (via pad_pose) -> board (via inst_pose)
        transformed = []
        for pt in corners:
            pad_pt = transform_point(pt, pad.rect_pose)
            pkg_pt = transform_point(pad_pt, pad.pad_pose)
            board_pt = transform_point(pkg_pt, inst_pose)
            transformed.append((board_pt.x, board_pt.y))
        msp.add_lwpolyline(transformed, close=True, dxfattribs={"layer": layer})


def emit_polygon_pads(
    msp: Modelspace,
    polygon_pads: list[PolygonPad],
    inst_pose: Pose,
    side: str,
) -> None:
    layer = f"Pads_{side}"
    for pad in polygon_pads:
        transformed = []
        for pt in pad.points:
            pkg_pt = transform_point(pt, pad.pose)
            board_pt = transform_point(pkg_pt, inst_pose)
            transformed.append((board_pt.x, board_pt.y))
        if len(transformed) < 2:
            continue
        msp.add_lwpolyline(
            transformed,
            close=True,
            dxfattribs={"layer": layer},
        )


def emit_pad_drill_holes(
    msp: Modelspace, pkg: Package, inst_pose: Pose
) -> None:
    """Emit drill holes on the Drill layer for all through-hole pads in a package."""
    for pad in pkg.pads:
        _emit_drill_hole(msp, pad.center, pad.hole_radius, inst_pose)
    for pad in pkg.rectangle_pads:
        _emit_drill_hole(msp, Point(pad.pad_pose.x, pad.pad_pose.y), pad.hole_radius, inst_pose)
    for pad in pkg.polygon_pads:
        _emit_drill_hole(msp, Point(pad.pose.x, pad.pose.y), pad.hole_radius, inst_pose)


def emit_polygon(
    msp: Modelspace,
    points: list[Point],
    layer: str,
    pose: Pose | None = None,
) -> None:
    transformed = []
    for pt in points:
        if pose is not None:
            bpt = transform_point(pt, pose)
            transformed.append((bpt.x, bpt.y))
        else:
            transformed.append((pt.x, pt.y))
    if len(transformed) < 2:
        return
    msp.add_lwpolyline(
        transformed,
        close=True,
        dxfattribs={"layer": layer},
    )


def emit_line_shape(
    msp: Modelspace, line_shape: LineShape, pose: Pose | None = None, layer: str | None = None,
) -> None:
    if layer is None:
        layer = get_dxf_layer(line_shape.layer_name, line_shape.side)
    p1 = line_shape.line.p1
    p2 = line_shape.line.p2
    if pose is not None:
        p1 = transform_point(p1, pose)
        p2 = transform_point(p2, pose)
    _add_wide_line(msp, (p1.x, p1.y), (p2.x, p2.y), line_shape.line.width, layer)


def emit_circle_shape(
    msp: Modelspace,
    circle_shape: CircleShape,
    pose: Pose | None = None,
    layer: str | None = None,
) -> None:
    if layer is None:
        layer = get_dxf_layer(circle_shape.layer_name, circle_shape.side)
    center = circle_shape.center
    if pose is not None:
        center = transform_point(center, pose)
    msp.add_circle(
        center=(center.x, center.y),
        radius=circle_shape.radius,
        dxfattribs={"layer": layer},
    )


def _transform_arc_angles(start_angle: float, end_angle: float, pose: Pose | None) -> tuple[float, float]:
    if pose is None:
        return start_angle, end_angle
    if pose.flip_x:
        return transform_angle(end_angle, pose), transform_angle(start_angle, pose)
    return transform_angle(start_angle, pose), transform_angle(end_angle, pose)


def emit_arc_shape(
    msp: Modelspace,
    arc_shape: ArcShape,
    pose: Pose | None = None,
    layer: str | None = None,
) -> None:
    if layer is None:
        layer = get_dxf_layer(arc_shape.layer_name, arc_shape.side)
    arc = arc_shape.arc
    center = arc.center
    if pose is not None:
        center = transform_point(center, pose)
    start_angle, end_angle = _transform_arc_angles(arc.start_angle, arc.end_angle, pose)
    if arc.width > 0.0:
        _add_wide_arc(
            msp,
            (center.x, center.y),
            arc.radius,
            start_angle,
            end_angle,
            arc.width,
            layer,
        )
    else:
        msp.add_arc(
            center=(center.x, center.y),
            radius=arc.radius,
            start_angle=start_angle,
            end_angle=end_angle,
            dxfattribs={"layer": layer},
        )


def emit_rectangle_shape(
    msp: Modelspace,
    rectangle_shape: RectangleShape,
    pose: Pose | None = None,
    layer: str | None = None,
) -> None:
    if layer is None:
        layer = get_dxf_layer(rectangle_shape.layer_name, rectangle_shape.side)
    hw = rectangle_shape.width / 2.0
    hh = rectangle_shape.height / 2.0
    corners = [
        Point(-hw, -hh),
        Point(hw, -hh),
        Point(hw, hh),
        Point(-hw, hh),
    ]
    transformed = []
    for pt in corners:
        rect_pt = transform_point(pt, rectangle_shape.pose)
        if pose is not None:
            rect_pt = transform_point(rect_pt, pose)
        transformed.append((rect_pt.x, rect_pt.y))
    msp.add_lwpolyline(transformed, close=True, dxfattribs={"layer": layer})


def emit_text_shape(
    msp: Modelspace,
    text_shape: TextShape,
    pose: Pose | None = None,
    layer: str | None = None,
) -> None:
    if layer is None:
        layer = get_dxf_layer(text_shape.layer_name, text_shape.side)
    text_pt = Point(x=text_shape.pose.x, y=text_shape.pose.y)
    rotation = text_shape.pose.angle
    if pose is not None:
        text_pt = transform_point(text_pt, pose)
        rotation = transform_angle(rotation, pose)
    msp.add_text(
        text_shape.string,
        height=text_shape.size,
        dxfattribs={
            "layer": layer,
            "rotation": rotation,
            "insert": (text_pt.x, text_pt.y),
        },
    )


def emit_instance(
    msp: Modelspace,
    inst: Instance,
    packages: dict[str, Package],
    layer_filter: set[str] | None,
) -> None:
    pkg = packages.get(inst.package_name)
    if pkg is None:
        print(
            f"Warning: Package '{inst.package_name}' not found for "
            f"instance '{inst.designator}'",
            file=sys.stderr,
        )
        return

    if layer_filter is None or "Components" in layer_filter:
        msp.add_point(
            location=(inst.pose.x, inst.pose.y),
            dxfattribs={"layer": "Components"},
        )

    pad_layer = f"Pads_{inst.side}"
    if layer_filter is None or pad_layer in layer_filter:
        emit_pads(msp, pkg.pads, inst.pose, inst.side)
        emit_rectangle_pads(msp, pkg.rectangle_pads, inst.pose, inst.side)
        emit_polygon_pads(msp, pkg.polygon_pads, inst.pose, inst.side)

    if layer_filter is None or "Drill" in layer_filter:
        emit_pad_drill_holes(msp, pkg, inst.pose)

    for poly in pkg.polygons:
        resolved_side = resolve_side(poly.side, inst.side)
        layer = get_dxf_layer(poly.layer_name, resolved_side)
        if layer_filter is None or layer in layer_filter:
            emit_polygon(msp, poly.points, layer, inst.pose)

    for line_shape in pkg.lines:
        resolved_side = resolve_side(line_shape.side, inst.side)
        layer = get_dxf_layer(line_shape.layer_name, resolved_side)
        if layer_filter is None or layer in layer_filter:
            emit_line_shape(msp, line_shape, inst.pose, layer)
    for circle_shape in pkg.circles:
        resolved_side = resolve_side(circle_shape.side, inst.side)
        layer = get_dxf_layer(circle_shape.layer_name, resolved_side)
        if layer_filter is None or layer in layer_filter:
            emit_circle_shape(msp, circle_shape, inst.pose, layer)
    for arc_shape in pkg.arcs:
        resolved_side = resolve_side(arc_shape.side, inst.side)
        layer = get_dxf_layer(arc_shape.layer_name, resolved_side)
        if layer_filter is None or layer in layer_filter:
            emit_arc_shape(msp, arc_shape, inst.pose, layer)
    for rectangle_shape in pkg.rectangles:
        resolved_side = resolve_side(rectangle_shape.side, inst.side)
        layer = get_dxf_layer(rectangle_shape.layer_name, resolved_side)
        if layer_filter is None or layer in layer_filter:
            emit_rectangle_shape(msp, rectangle_shape, inst.pose, layer)
    for text_shape in pkg.texts:
        resolved_side = resolve_side(text_shape.side, inst.side)
        layer = get_dxf_layer(text_shape.layer_name, resolved_side)
        if layer_filter is None or layer in layer_filter:
            emit_text_shape(msp, text_shape, inst.pose, layer)

    if layer_filter is None or "Components" in layer_filter:
        if inst.designator_text is not None:
            dt = inst.designator_text
            text_board_pt = transform_point(
                Point(x=dt.pose.x, y=dt.pose.y), inst.pose
            )
            text_rotation = transform_angle(dt.pose.angle, inst.pose)
            msp.add_text(
                dt.string,
                height=dt.size,
                dxfattribs={
                    "layer": "Components",
                    "rotation": text_rotation,
                    "insert": (text_board_pt.x, text_board_pt.y),
                },
            )

    # Instance-level shapes (value labels, custom geometry) — already in board coords
    for ts in inst.shapes_text:
        layer = get_dxf_layer(ts.layer_name, ts.side)
        if layer_filter is not None and layer not in layer_filter:
            continue
        emit_text_shape(msp, ts, layer=layer)
    for poly in inst.shapes_polygon:
        layer = get_dxf_layer(poly.layer_name, poly.side)
        if layer_filter is None or layer in layer_filter:
            emit_polygon(msp, poly.points, layer)
    for ls in inst.shapes_line:
        layer = get_dxf_layer(ls.layer_name, ls.side)
        if layer_filter is None or layer in layer_filter:
            _add_wide_line(
                msp, (ls.line.p1.x, ls.line.p1.y), (ls.line.p2.x, ls.line.p2.y),
                ls.line.width, layer,
            )
    for circle_shape in inst.shapes_circle:
        layer = get_dxf_layer(circle_shape.layer_name, circle_shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_circle_shape(msp, circle_shape, layer=layer)
    for arc_shape in inst.shapes_arc:
        layer = get_dxf_layer(arc_shape.layer_name, arc_shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_arc_shape(msp, arc_shape, layer=layer)
    for rectangle_shape in inst.shapes_rectangle:
        layer = get_dxf_layer(rectangle_shape.layer_name, rectangle_shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_rectangle_shape(msp, rectangle_shape, layer=layer)


def emit_tracks(
    msp: Modelspace,
    tracks: list[CopperShape],
    layer_names: dict[int, str],
    layer_filter: set[str] | None,
) -> None:
    for track in tracks:
        layer = copper_layer_name(track.layer_index, layer_names, "Copper")
        if layer_filter is not None and layer not in layer_filter:
            continue
        if isinstance(track, CopperPolygon):
            pts = [(p.x, p.y) for p in track.points]
            if len(pts) < 2:
                continue
            msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})
        elif isinstance(track, CopperLine):
            _add_wide_line(
                msp, (track.p1.x, track.p1.y), (track.p2.x, track.p2.y),
                track.width, layer,
            )
        elif isinstance(track, CopperArc):
            _add_wide_arc(
                msp, (track.center.x, track.center.y), track.radius,
                track.start_angle, track.end_angle, track.width, layer,
            )


def emit_fills(
    msp: Modelspace,
    fills: list[CopperPolygon],
    layer_names: dict[int, str],
    layer_filter: set[str] | None,
) -> None:
    for fill in fills:
        layer = copper_layer_name(fill.layer_index, layer_names, "Copper")
        if layer_filter is not None and layer not in layer_filter:
            continue
        pts = [(p.x, p.y) for p in fill.points]
        if len(pts) < 2:
            continue
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})


def emit_vias(
    msp: Modelspace,
    vias: list[Via],
    layer_filter: set[str] | None,
) -> None:
    for via in vias:
        # Via pad (annular ring) on Vias layer
        if layer_filter is None or "Vias" in layer_filter:
            msp.add_circle(
                center=(via.center.x, via.center.y),
                radius=via.diameter / 2.0,
                dxfattribs={"layer": "Vias"},
            )
        # Drill hole on Drill layer
        if layer_filter is None or "Drill" in layer_filter:
            msp.add_circle(
                center=(via.center.x, via.center.y),
                radius=via.hole_diameter / 2.0,
                dxfattribs={"layer": "Drill"},
            )


def emit_board_shapes(
    msp: Modelspace,
    shapes: list[PolygonShape],
    layer_filter: set[str] | None,
) -> None:
    for shape in shapes:
        layer = get_dxf_layer(shape.layer_name, shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_polygon(msp, shape.points, layer)


def emit_board_line_shapes(
    msp: Modelspace,
    line_shapes: list[LineShape],
    layer_filter: set[str] | None,
) -> None:
    for ls in line_shapes:
        layer = get_dxf_layer(ls.layer_name, ls.side)
        if layer_filter is None or layer in layer_filter:
            _add_wide_line(
                msp, (ls.line.p1.x, ls.line.p1.y), (ls.line.p2.x, ls.line.p2.y),
                ls.line.width, layer,
            )


def emit_board_circle_shapes(
    msp: Modelspace,
    circle_shapes: list[CircleShape],
    layer_filter: set[str] | None,
) -> None:
    for circle_shape in circle_shapes:
        layer = get_dxf_layer(circle_shape.layer_name, circle_shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_circle_shape(msp, circle_shape, layer=layer)


def emit_board_arc_shapes(
    msp: Modelspace,
    arc_shapes: list[ArcShape],
    layer_filter: set[str] | None,
) -> None:
    for arc_shape in arc_shapes:
        layer = get_dxf_layer(arc_shape.layer_name, arc_shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_arc_shape(msp, arc_shape, layer=layer)


def emit_board_rectangle_shapes(
    msp: Modelspace,
    rectangle_shapes: list[RectangleShape],
    layer_filter: set[str] | None,
) -> None:
    for rectangle_shape in rectangle_shapes:
        layer = get_dxf_layer(rectangle_shape.layer_name, rectangle_shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_rectangle_shape(msp, rectangle_shape, layer=layer)


def emit_board_text_shapes(
    msp: Modelspace,
    text_shapes: list[TextShape],
    layer_filter: set[str] | None,
) -> None:
    for text_shape in text_shapes:
        layer = get_dxf_layer(text_shape.layer_name, text_shape.side)
        if layer_filter is None or layer in layer_filter:
            emit_text_shape(msp, text_shape, layer=layer)


def emit_board_drill_shapes(msp: Modelspace, data: BoardData, layer_filter: set[str] | None) -> None:
    drill_lines = [ls for ls in data.board_line_shapes if _is_drill_layer(ls.layer_name)]
    drill_polygons = [shape for shape in data.board_shapes if _is_drill_layer(shape.layer_name)]
    drill_circles = [
        circle for circle in data.board_circle_shapes if _is_drill_layer(circle.layer_name)
    ]
    drill_arcs = [arc for arc in data.board_arc_shapes if _is_drill_layer(arc.layer_name)]
    drill_rectangles = [
        rect for rect in data.board_rectangle_shapes if _is_drill_layer(rect.layer_name)
    ]
    emit_board_shapes(msp, drill_polygons, layer_filter)
    emit_board_line_shapes(msp, drill_lines, layer_filter)
    emit_board_circle_shapes(msp, drill_circles, layer_filter)
    emit_board_arc_shapes(msp, drill_arcs, layer_filter)
    emit_board_rectangle_shapes(msp, drill_rectangles, layer_filter)


# ─── Main Conversion ─────────────────────────────────────────────────────


def export_dxf(
    xml_path: str,
    dxf_path: str,
    layers: set[str] | None = None,
    config: DxfExportConfig | None = None,
) -> None:
    if config is None:
        config = DxfExportConfig(layers=layers)
    elif layers is not None:
        config = DxfExportConfig(
            layers=layers,
            units=config.units,
            include_board_outline=config.include_board_outline,
            include_components=config.include_components,
            include_pads=config.include_pads,
            include_drill=config.include_drill,
            include_vias=config.include_vias,
            include_copper=config.include_copper,
            include_annotations=config.include_annotations,
            layer_colors=config.layer_colors,
        )
    data = parse_xml(xml_path)
    layers = _effective_layer_filter(data, config)

    n_track_lines = sum(1 for t in data.tracks if isinstance(t, CopperLine))
    n_track_arcs = sum(1 for t in data.tracks if isinstance(t, CopperArc))
    n_track_polys = sum(1 for t in data.tracks if isinstance(t, CopperPolygon))
    print(f"Parsed: {len(data.boundary_lines)} boundary lines, "
          f"{len(data.boundary_arcs)} boundary arcs, "
          f"{len(data.packages)} packages, "
          f"{len(data.instances)} instances, "
          f"{len(data.board_shapes)} board polygon shapes, "
          f"{len(data.board_line_shapes)} board line shapes, "
          f"{len(data.board_circle_shapes)} board circle shapes, "
          f"{len(data.board_arc_shapes)} board arc shapes, "
          f"{len(data.board_rectangle_shapes)} board rectangle shapes, "
          f"{len(data.board_text_shapes)} board text shapes, "
          f"{len(data.tracks)} tracks ({n_track_lines} line, {n_track_arcs} arc, {n_track_polys} polygon), "
          f"{len(data.fills)} fills, "
          f"{len(data.vias)} vias")

    for pkg_name, pkg in data.packages.items():
        print(f"  Package '{pkg_name}': {len(pkg.pads)} circle, "
              f"{len(pkg.rectangle_pads)} rect, "
              f"{len(pkg.polygon_pads)} polygon pads, "
              f"{len(pkg.polygons)} shapes, {len(pkg.lines)} lines, "
              f"{len(pkg.circles)} circles, {len(pkg.arcs)} arcs, "
              f"{len(pkg.rectangles)} rectangles, {len(pkg.texts)} texts")

    for inst in data.instances:
        print(f"  Instance '{inst.designator}': package='{inst.package_name}', "
              f"pose=({inst.pose.x}, {inst.pose.y}, {inst.pose.angle}deg)")

    if data.layer_names:
        print(f"  Stackup layers: {data.layer_names}")

    doc = ezdxf_new("R2010")
    doc.units = ezdxf_units.MM
    setup_layers(doc, data, config=config)
    msp = doc.modelspace()

    if config.include_board_outline and (layers is None or "BoardOutline" in layers):
        emit_board_outline(msp, data)

    if config.include_components:
        for inst in data.instances:
            emit_instance(msp, inst, data.packages, layers)

    if config.include_copper:
        emit_tracks(msp, data.tracks, data.layer_names, layers)
        emit_fills(msp, data.fills, data.layer_names, layers)
    if config.include_vias:
        emit_vias(msp, data.vias, layers)
    if config.include_annotations:
        emit_board_shapes(msp, data.board_shapes, layers)
        emit_board_line_shapes(msp, data.board_line_shapes, layers)
        emit_board_circle_shapes(msp, data.board_circle_shapes, layers)
        emit_board_arc_shapes(msp, data.board_arc_shapes, layers)
        emit_board_rectangle_shapes(msp, data.board_rectangle_shapes, layers)
        emit_board_text_shapes(msp, data.board_text_shapes, layers)
    elif config.include_drill:
        emit_board_drill_shapes(msp, data, layers)

    doc.saveas(dxf_path)
    print(f"Written: {dxf_path}")


def convert(xml_path: str, dxf_path: str, layers: set[str] | None = None) -> None:
    """Backward-compatible Python helper; prefer export_dxf for new code."""

    export_dxf(xml_path, dxf_path, layers=layers)
