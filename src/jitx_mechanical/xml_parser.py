"""XML parsing for JITX board design files."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .models import (
    ArcSegment,
    BoardData,
    CirclePad,
    CopperArc,
    CopperLine,
    CopperPolygon,
    CopperShape,
    DesignatorText,
    Instance,
    LineSegment,
    LineShape,
    Package,
    Point,
    PolygonPad,
    PolygonShape,
    Pose,
    RectanglePad,
    TextShape,
    Via,
)


def _attr(elem: ET.Element, name: str, default: str = "0") -> str:
    """Get an XML attribute as a non-None string."""
    val = elem.get(name)
    return val if val is not None else default


def parse_point(elem: ET.Element) -> Point:
    return Point(x=float(_attr(elem, "X")), y=float(_attr(elem, "Y")))


def parse_pose(elem: ET.Element) -> Pose:
    return Pose(
        x=float(_attr(elem, "X")),
        y=float(_attr(elem, "Y")),
        angle=float(_attr(elem, "ANGLE")),
        flip_x=_attr(elem, "FLIPX", "false").lower() == "true",
    )


def parse_board_boundary(
    board: ET.Element,
) -> tuple[list[LineSegment], list[ArcSegment]]:
    lines: list[LineSegment] = []
    arcs: list[ArcSegment] = []
    for bb in board.findall("BOARD-BOUNDARY"):
        line_elem = bb.find("LINE")
        arc_elem = bb.find("ARC")
        if line_elem is not None:
            points = [parse_point(p) for p in line_elem.findall("POINT")]
            width = float(line_elem.get("WIDTH", "0.0"))
            if len(points) == 2:
                lines.append(
                    LineSegment(p1=points[0], p2=points[1], width=width)
                )
        elif arc_elem is not None:
            arcs.append(
                ArcSegment(
                    center=Point(
                        x=float(_attr(arc_elem, "X")),
                        y=float(_attr(arc_elem, "Y")),
                    ),
                    radius=float(_attr(arc_elem, "RADIUS")),
                    start_angle=float(_attr(arc_elem, "START_ANGLE")),
                    end_angle=float(_attr(arc_elem, "END_ANGLE")),
                    width=float(arc_elem.get("WIDTH", "0.0")),
                )
            )
    return lines, arcs


def _parse_hole_radius(pad_elem: ET.Element) -> float:
    """Extract drill hole radius from a PAD's HOLE child, if present."""
    hole_elem = pad_elem.find("HOLE")
    if hole_elem is not None:
        hole_circle = hole_elem.find("CIRCLE")
        if hole_circle is not None:
            return float(hole_circle.get("RADIUS", "0.0"))
    return 0.0


def parse_package(pkg_elem: ET.Element) -> Package:
    name = _attr(pkg_elem, "NAME")
    pads: list[CirclePad] = []
    rectangle_pads: list[RectanglePad] = []
    polygon_pads: list[PolygonPad] = []
    polygons: list[PolygonShape] = []
    lines: list[LineShape] = []

    for pad_elem in pkg_elem.findall("PAD"):
        pose_elem = pad_elem.find("POSE")
        if pose_elem is None:
            continue
        pose = parse_pose(pose_elem)
        side = pad_elem.get("SIDE", "Top")
        hole_radius = _parse_hole_radius(pad_elem)
        circle = pad_elem.find("CIRCLE")
        rectangle = pad_elem.find("RECTANGLE")
        polygon = pad_elem.find("POLYGON")
        if circle is not None:
            pads.append(
                CirclePad(
                    name=_attr(pad_elem, "NAME"),
                    center=Point(x=pose.x, y=pose.y),
                    radius=float(_attr(circle, "RADIUS")),
                    side=side,
                    hole_radius=hole_radius,
                )
            )
        elif rectangle is not None:
            rect_pose_elem = rectangle.find("POSE")
            rect_pose = parse_pose(rect_pose_elem) if rect_pose_elem is not None else Pose(0.0, 0.0, 0.0, False)
            rectangle_pads.append(
                RectanglePad(
                    name=_attr(pad_elem, "NAME"),
                    width=float(_attr(rectangle, "WIDTH")),
                    height=float(_attr(rectangle, "HEIGHT")),
                    rect_pose=rect_pose,
                    pad_pose=pose,
                    side=side,
                    hole_radius=hole_radius,
                )
            )
        elif polygon is not None:
            points = [parse_point(p) for p in polygon.findall("POINT")]
            polygon_pads.append(
                PolygonPad(
                    name=_attr(pad_elem, "NAME"),
                    points=points,
                    pose=pose,
                    side=side,
                    hole_radius=hole_radius,
                )
            )

    for shape_elem in pkg_elem.findall("SHAPE"):
        layer_spec = shape_elem.find("LAYER-SPECIFIER")
        if layer_spec is None:
            continue
        layer_name = _attr(layer_spec, "NAME")
        side = layer_spec.get("SIDE", "Top")

        polygon_elem = shape_elem.find("POLYGON")
        if polygon_elem is not None:
            points = [parse_point(p) for p in polygon_elem.findall("POINT")]
            polygons.append(
                PolygonShape(points=points, layer_name=layer_name, side=side)
            )

        line_elem = shape_elem.find("LINE")
        if line_elem is not None:
            pts = [parse_point(p) for p in line_elem.findall("POINT")]
            width = float(line_elem.get("WIDTH", "0.0"))
            if len(pts) == 2:
                lines.append(
                    LineShape(
                        line=LineSegment(p1=pts[0], p2=pts[1], width=width),
                        layer_name=layer_name,
                        side=side,
                    )
                )

    return Package(name=name, pads=pads, rectangle_pads=rectangle_pads, polygon_pads=polygon_pads, polygons=polygons, lines=lines)


def parse_instance(inst_elem: ET.Element) -> Instance:
    pose_elem = inst_elem.find("POSE")
    assert pose_elem is not None, f"INST missing POSE: {inst_elem.get('DESIGNATOR')}"
    pose = parse_pose(pose_elem)
    des_text = None
    dt_elem = inst_elem.find("DESIGNATOR-TEXT")
    if dt_elem is not None:
        text_elem = dt_elem.find("TEXT")
        if text_elem is not None:
            text_pose_elem = text_elem.find("POSE")
            assert text_pose_elem is not None
            des_text = DesignatorText(
                string=_attr(text_elem, "STRING", ""),
                size=float(text_elem.get("SIZE", "1.0")),
                anchor=_attr(text_elem, "ANCHOR", "C"),
                pose=parse_pose(text_pose_elem),
            )
    # Parse instance-level SHAPE children (value labels, custom geometry)
    inst_texts: list[TextShape] = []
    inst_polygons: list[PolygonShape] = []
    inst_lines: list[LineShape] = []
    for shape_elem in inst_elem.findall("SHAPE"):
        layer_spec = shape_elem.find("LAYER-SPECIFIER")
        if layer_spec is None:
            continue
        layer_name = layer_spec.get("NAME", "")
        side = layer_spec.get("SIDE", "Top")
        text_elem = shape_elem.find("TEXT")
        if text_elem is not None:
            text_pose_elem = text_elem.find("POSE")
            if text_pose_elem is not None:
                inst_texts.append(TextShape(
                    string=text_elem.get("STRING", ""),
                    size=float(text_elem.get("SIZE", "1.0")),
                    pose=parse_pose(text_pose_elem),
                    layer_name=layer_name,
                    side=side,
                ))
        polygon_elem = shape_elem.find("POLYGON")
        if polygon_elem is not None:
            points = [parse_point(p) for p in polygon_elem.findall("POINT")]
            inst_polygons.append(
                PolygonShape(points=points, layer_name=layer_name, side=side)
            )
        line_elem = shape_elem.find("LINE")
        if line_elem is not None:
            pts = [parse_point(p) for p in line_elem.findall("POINT")]
            width = float(line_elem.get("WIDTH", "0.0"))
            if len(pts) == 2:
                inst_lines.append(
                    LineShape(
                        line=LineSegment(p1=pts[0], p2=pts[1], width=width),
                        layer_name=layer_name,
                        side=side,
                    )
                )

    return Instance(
        designator=_attr(inst_elem, "DESIGNATOR", ""),
        package_name=_attr(inst_elem, "PACKAGE", ""),
        side=inst_elem.get("SIDE", "Top"),
        pose=pose,
        designator_text=des_text,
        shapes_text=inst_texts,
        shapes_polygon=inst_polygons,
        shapes_line=inst_lines,
    )


def parse_board_shapes(board: ET.Element) -> tuple[list[PolygonShape], list[LineShape]]:
    polygons: list[PolygonShape] = []
    lines: list[LineShape] = []
    for shape_elem in board.findall("SHAPE"):
        layer_spec = shape_elem.find("LAYER-SPECIFIER")
        if layer_spec is None:
            continue
        layer_name = layer_spec.get("NAME", "")
        side = layer_spec.get("SIDE", "Top")
        polygon_elem = shape_elem.find("POLYGON")
        if polygon_elem is not None:
            points = [parse_point(p) for p in polygon_elem.findall("POINT")]
            polygons.append(
                PolygonShape(points=points, layer_name=layer_name, side=side)
            )
        line_elem = shape_elem.find("LINE")
        if line_elem is not None:
            pts = [parse_point(p) for p in line_elem.findall("POINT")]
            width = float(line_elem.get("WIDTH", "0.0"))
            if len(pts) == 2:
                lines.append(
                    LineShape(
                        line=LineSegment(p1=pts[0], p2=pts[1], width=width),
                        layer_name=layer_name,
                        side=side,
                    )
                )
    return polygons, lines


def _parse_layer_index(shape_elem: ET.Element) -> int:
    """Extract the layer index from a SHAPE's LAYER-INDEX child."""
    layer_idx = shape_elem.find("LAYER-INDEX")
    if layer_idx is not None:
        return int(layer_idx.get("INDEX", "0"))
    return 0


def parse_tracks(board: ET.Element) -> list[CopperShape]:
    tracks: list[CopperShape] = []
    for track_elem in board.findall("TRACK"):
        net = track_elem.get("NET", "")
        for shape_elem in track_elem.findall("SHAPE"):
            idx = _parse_layer_index(shape_elem)
            polygon_elem = shape_elem.find("POLYGON")
            line_elem = shape_elem.find("LINE")
            arc_elem = shape_elem.find("ARC")
            if polygon_elem is not None:
                points = [parse_point(p) for p in polygon_elem.findall("POINT")]
                tracks.append(CopperPolygon(layer_index=idx, net=net, points=points))
            elif line_elem is not None:
                pts = [parse_point(p) for p in line_elem.findall("POINT")]
                width = float(line_elem.get("WIDTH", "0.0"))
                if len(pts) == 2:
                    tracks.append(CopperLine(
                        layer_index=idx, net=net,
                        p1=pts[0], p2=pts[1], width=width,
                    ))
            elif arc_elem is not None:
                tracks.append(CopperArc(
                    layer_index=idx, net=net,
                    center=Point(
                        x=float(_attr(arc_elem, "X")),
                        y=float(_attr(arc_elem, "Y")),
                    ),
                    radius=float(_attr(arc_elem, "RADIUS")),
                    start_angle=float(_attr(arc_elem, "START_ANGLE")),
                    end_angle=float(_attr(arc_elem, "END_ANGLE")),
                    width=float(arc_elem.get("WIDTH", "0.0")),
                ))
    return tracks


def parse_fills(board: ET.Element) -> list[CopperPolygon]:
    fills: list[CopperPolygon] = []
    for fill_elem in board.findall("FILL"):
        net = fill_elem.get("NET", "")
        for shape_elem in fill_elem.findall("SHAPE"):
            idx = _parse_layer_index(shape_elem)
            polygon_elem = shape_elem.find("POLYGON")
            if polygon_elem is not None:
                points = [parse_point(p) for p in polygon_elem.findall("POINT")]
                fills.append(CopperPolygon(layer_index=idx, net=net, points=points))
    return fills


def parse_stackup_names(board: ET.Element) -> dict[int, str]:
    """Parse stackup to build a mapping of conductor layer index -> name."""
    names: dict[int, str] = {}
    stackup = board.find("STACKUP")
    if stackup is None:
        return names
    conductor_idx = 0
    for layer in stackup.findall("STACKUP-LAYER"):
        if layer.get("MATERIAL-TYPE") == "CONDUCTOR":
            names[conductor_idx] = layer.get("NAME", f"L{conductor_idx}")
            conductor_idx += 1
    return names


def parse_vias(board: ET.Element) -> list[Via]:
    vias: list[Via] = []
    for via_elem in board.findall("VIA"):
        point_elem = via_elem.find("POINT")
        if point_elem is None:
            continue
        start_layer = via_elem.find("START-LAYER")
        end_layer = via_elem.find("END-LAYER")
        start_side = "Top"
        end_side = "Bottom"
        if start_layer is not None:
            li = start_layer.find("LAYER-INDEX")
            if li is not None:
                start_side = li.get("SIDE", "Top")
        if end_layer is not None:
            li = end_layer.find("LAYER-INDEX")
            if li is not None:
                end_side = li.get("SIDE", "Bottom")
        vias.append(
            Via(
                center=parse_point(point_elem),
                diameter=float(via_elem.get("DIAMETER", "0.0")),
                hole_diameter=float(via_elem.get("HOLE-DIAMETER", "0.0")),
                net=via_elem.get("NET", ""),
                start_side=start_side,
                end_side=end_side,
            )
        )
    return vias


def parse_xml(xml_path: str) -> BoardData:
    tree = ET.parse(xml_path)
    root = tree.getroot()
    board = root.find("BOARD")
    if board is None:
        raise ValueError(f"No <BOARD> element found in {xml_path}")

    boundary_lines, boundary_arcs = parse_board_boundary(board)

    packages: dict[str, Package] = {}
    for pkg_elem in board.findall("PACKAGE"):
        pkg = parse_package(pkg_elem)
        packages[pkg.name] = pkg

    instances = [parse_instance(inst) for inst in board.findall("INST")]
    board_polygon_shapes, board_line_shapes = parse_board_shapes(board)
    tracks = parse_tracks(board)
    fills = parse_fills(board)
    vias = parse_vias(board)
    layer_names = parse_stackup_names(board)

    return BoardData(
        boundary_lines=boundary_lines,
        boundary_arcs=boundary_arcs,
        packages=packages,
        instances=instances,
        board_shapes=board_polygon_shapes,
        board_line_shapes=board_line_shapes,
        tracks=tracks,
        fills=fills,
        vias=vias,
        layer_names=layer_names,
    )
