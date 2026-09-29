"""DXF mechanical importer."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

from ezdxf import units
from ezdxf.document import Drawing
from ezdxf.enums import InsertUnits
from ezdxf.filemanagement import readfile

from jitxlib.mechanical.geometry import (
    assemble_closed_paths,
    geometry_area,
    geometry_center,
    lwpolyline_to_closed_path,
    point_in_geometry,
)
from jitxlib.mechanical.models import (
    ArcPathSegment,
    CircleGeometry,
    ClosedPath,
    Geometry,
    HolePlating,
    HolePolicy,
    ImportMessage,
    MechanicalAnnotation,
    MechanicalComponent,
    MechanicalHole,
    MechanicalImport,
    MechanicalRegion,
    MessageSeverity,
    Point,
    SourceObject,
    TextGeometry,
)

_logger = logging.getLogger(__name__)

_UNIT_TO_MM: dict[str, float] = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
    "in": 25.4,
    "ft": 304.8,
    "mil": 0.0254,
    "uin": 0.0000254,
    "um": 0.001,
    "yd": 914.4,
}

_LAYER_PATTERNS: dict[str, list[str]] = {
    "outline": ["outline", "board", "boundary", "profile", "edge", "border"],
    "cutout": ["cutout", "route", "rout", "slot", "interior_profile"],
    "hole": ["hole", "drill", "mount"],
    "keepout": ["keepout", "keep-out", "keep_out", "restrict"],
    "soldermask": ["mask", "soldermask", "solder"],
    "bend": ["bend", "flex"],
    "height": ["height", "component_area", "place"],
    "annotation": ["dim", "dimension", "note", "text", "anno"],
}


@dataclass
class DxfInventory:
    filepath: str = ""
    dxf_version: str = ""
    units: str | None = None
    layers: dict[str, int] = field(default_factory=dict)
    entity_counts: dict[str, int] = field(default_factory=dict)
    bounding_box: tuple[Point, Point] | None = None


@dataclass(frozen=True)
class DxfHatch:
    boundary_paths: tuple[ClosedPath, ...] = ()
    is_solid: bool = False
    layer: str = ""


def read_dxf(dxf_path: str) -> DxfInventory:
    """Return entity counts, layers, declared units, and approximate drawing bounds.

    ``dxf_path`` identifies the file to inspect. Bounds use source coordinates
    without unit conversion and include only the entity types handled by the
    coordinate collector; they are not the bounds of the imported Board.
    """

    doc = readfile(dxf_path)
    msp = doc.modelspace()
    layer_counts: dict[str, int] = defaultdict(int)
    entity_counts: dict[str, int] = defaultdict(int)
    all_x: list[float] = []
    all_y: list[float] = []

    for entity in msp:
        etype = entity.dxftype()
        layer_counts[entity.dxf.layer] += 1
        entity_counts[etype] += 1
        _collect_entity_coords(entity, all_x, all_y)

    bbox = None
    if all_x and all_y:
        bbox = (Point(min(all_x), min(all_y)), Point(max(all_x), max(all_y)))

    return DxfInventory(
        filepath=dxf_path,
        dxf_version=doc.dxfversion,
        units=_detect_units(doc),
        layers=dict(layer_counts),
        entity_counts=dict(entity_counts),
        bounding_box=bbox,
    )


def import_dxf(
    dxf_path: str,
    *,
    layer_map: dict[str, str] | None = None,
    unit: str | None = None,
    hole_policy: HolePolicy | str = HolePolicy.CUTOUT,
) -> MechanicalImport:
    """Return board geometry in millimeters, source metadata, and conversion messages.

    ``dxf_path`` identifies the source file. ``layer_map`` maps exact layer names
    to outline, cutout, hole, keepout, soldermask, bend, height, or annotation.
    Other layers use name heuristics. ``unit`` overrides declared units; without
    a declaration, scale is inferred and reported. ``hole_policy`` selects Board
    cutouts or connectable components for circular holes with unknown plating.
    """

    doc = readfile(dxf_path)
    msp = doc.modelspace()
    unit_scale = _resolve_unit_scale(doc, unit, msp)
    source_units = unit or _detect_units(doc)
    hole_policy = HolePolicy(hole_policy)

    layer_lines: dict[str, list[tuple[Point, Point]]] = defaultdict(list)
    layer_arcs: dict[str, list[ArcPathSegment]] = defaultdict(list)
    paths: list[ClosedPath] = []
    circles: list[CircleGeometry] = []
    texts: list[TextGeometry] = []
    hatches: list[DxfHatch] = []
    unclassified_types: dict[str, int] = defaultdict(int)
    unclosed_paths: list[SourceObject] = []

    for entity in msp:
        etype = entity.dxftype()
        layer = entity.dxf.layer
        if etype == "LINE":
            layer_lines[layer].append(
                (
                    Point(entity.dxf.start.x * unit_scale, entity.dxf.start.y * unit_scale),
                    Point(entity.dxf.end.x * unit_scale, entity.dxf.end.y * unit_scale),
                )
            )
        elif etype == "ARC":
            layer_arcs[layer].append(_parse_arc_entity(entity, unit_scale))
        elif etype == "LWPOLYLINE":
            path = _parse_lwpolyline(entity, unit_scale)
            if path is not None:
                paths.append(path)
            else:
                unclassified_types["open LWPOLYLINE"] += 1
        elif etype == "CIRCLE":
            circles.append(
                CircleGeometry(
                    center=Point(
                        entity.dxf.center.x * unit_scale, entity.dxf.center.y * unit_scale
                    ),
                    radius=entity.dxf.radius * unit_scale,
                    source_layer=layer,
                )
            )
        elif etype in ("TEXT", "MTEXT"):
            texts.append(_parse_text_entity(entity, unit_scale))
        elif etype == "HATCH":
            hatch = _parse_hatch_entity(entity, unit_scale)
            if hatch is not None:
                hatches.append(hatch)
            else:
                unclassified_types["HATCH boundary"] += 1
        else:
            unclassified_types[etype] += 1

    for layer in sorted(set(layer_lines) | set(layer_arcs)):
        lines, arcs = layer_lines.get(layer, []), layer_arcs.get(layer, [])
        closed = assemble_closed_paths(lines, arcs, source_layer=layer)
        paths.extend(closed)
        unused = len(lines) + len(arcs) - sum(len(path.segments) for path in closed)
        if unused:
            unclosed_paths.append(
                SourceObject("open_path", layer, "Unclosed LINE/ARC segments", unused)
            )

    result = MechanicalImport(
        source_path=dxf_path,
        source_format="dxf",
        source_units=source_units,
        unit_scale=unit_scale,
        unclassified=unclosed_paths,
    )

    if unit is None and source_units is None:
        result.warn(
            f"DXF units are unspecified; inferred a scale of {unit_scale} to millimeters.",
            hint="Pass --unit to specify the source drawing units.",
        )
    _classify_into_import(result, paths, circles, texts, hatches, layer_map, hole_policy)
    for etype, count in sorted(unclassified_types.items()):
        result.unclassified.append(SourceObject("unsupported_dxf_entity", etype, count=count))
    return result


def _classify_into_import(
    result: MechanicalImport,
    paths: list[ClosedPath],
    circles: list[CircleGeometry],
    texts: list[TextGeometry],
    hatches: list[DxfHatch],
    layer_map: dict[str, str] | None,
    hole_policy: HolePolicy,
) -> None:
    outline_candidates: list[Geometry] = []
    unresolved_paths: list[Geometry] = []
    unresolved_circles: list[CircleGeometry] = []

    for path in paths:
        role = _mapped_role(path.source_layer, layer_map)
        if role == "outline":
            outline_candidates.append(path)
        elif role in ("cutout", "hole"):
            result.board_cutouts.append(path)
        elif role in ("keepout", "soldermask", "bend", "height"):
            result.regions.append(
                MechanicalRegion(role=role, geometry=path, source_name=path.source_layer)
            )
        elif role == "annotation":
            result.regions.append(
                MechanicalRegion(
                    role="annotation_region", geometry=path, source_name=path.source_layer
                )
            )
        else:
            unresolved_paths.append(path)

    for circle in circles:
        role = _mapped_role(circle.source_layer, layer_map)
        if role == "outline":
            outline_candidates.append(circle)
        elif role in ("hole", "cutout"):
            _add_hole(result, circle, HolePlating.UNKNOWN, hole_policy, source=circle.source_layer)
        elif role in ("keepout", "soldermask", "bend", "height"):
            result.regions.append(
                MechanicalRegion(role=role, geometry=circle, source_name=circle.source_layer)
            )
        elif role == "annotation":
            result.regions.append(
                MechanicalRegion(
                    role="annotation_region", geometry=circle, source_name=circle.source_layer
                )
            )
        else:
            unresolved_circles.append(circle)

    if outline_candidates:
        result.board_outline = max(outline_candidates, key=lambda path: abs(geometry_area(path)))
        unresolved_paths.extend(
            path for path in outline_candidates if path is not result.board_outline
        )
    elif unresolved_paths:
        largest_idx = max(
            range(len(unresolved_paths)), key=lambda i: abs(geometry_area(unresolved_paths[i]))
        )
        result.board_outline = unresolved_paths.pop(largest_idx)

    if result.board_outline is not None:
        for path in unresolved_paths:
            if point_in_geometry(geometry_center(path), result.board_outline):
                result.board_cutouts.append(path)
            else:
                result.unclassified.append(
                    SourceObject("path", path.source_layer, "outside board outline")
                )
        for circle in unresolved_circles:
            if point_in_geometry(circle.center, result.board_outline):
                _add_hole(
                    result, circle, HolePlating.UNKNOWN, hole_policy, source=circle.source_layer
                )
            else:
                result.unclassified.append(
                    SourceObject("circle", circle.source_layer, "outside board outline")
                )
    else:
        for path in unresolved_paths:
            result.unclassified.append(
                SourceObject("path", path.source_layer, "no board outline detected")
            )
        for circle in unresolved_circles:
            result.unclassified.append(
                SourceObject("circle", circle.source_layer, "no board outline detected")
            )

    for text in texts:
        result.annotations.append(
            MechanicalAnnotation(
                role="text",
                text=text.content,
                position=text.position,
                height=text.height,
                rotation=text.rotation,
                source_name=text.source_layer,
            )
        )
    for hatch in hatches:
        for path in hatch.boundary_paths:
            result.regions.append(
                MechanicalRegion(
                    role="hatch_solid" if hatch.is_solid else "hatch",
                    geometry=path,
                    source_name=hatch.layer,
                )
            )


def _add_hole(
    result: MechanicalImport,
    circle: CircleGeometry,
    plating: HolePlating,
    hole_policy: HolePolicy,
    *,
    source: str,
    associated_with: str = "",
    hole_type: str = "",
    owner: str = "",
) -> None:
    ambiguous = plating == HolePlating.UNKNOWN
    imported_as = (
        "component"
        if hole_policy == HolePolicy.COMPONENT and plating != HolePlating.UNPLATED
        else "cutout"
    )
    result.holes.append(
        MechanicalHole(
            geometry=circle,
            plating=plating,
            associated_with=associated_with,
            hole_type=hole_type,
            owner=owner,
            imported_as=imported_as,
        )
    )

    if imported_as == "component":
        _add_component_hole(result, circle, plating, source=source)
    else:
        result.board_cutouts.append(circle)

    if ambiguous:
        result.messages.append(
            ImportMessage(
                MessageSeverity.WARNING,
                text=f"Circular interior geometry has unknown plating and was imported as a {imported_as}.",
                source=source,
                hint="Use --hole-policy component when these holes should be electrically connectable single-pin components.",
            )
        )


def _add_component_hole(
    result: MechanicalImport,
    circle: CircleGeometry,
    plating: HolePlating,
    *,
    source: str,
) -> None:
    key_radius = round(circle.radius, 6)
    for idx, component in enumerate(result.mechanical_components):
        if round(component.geometry.radius, 6) == key_radius and component.plating == plating:
            result.mechanical_components[idx] = MechanicalComponent(
                name=component.name,
                geometry=component.geometry,
                placements=component.placements + (circle.center,),
                plating=component.plating,
                source=component.source,
            )
            return
    result.mechanical_components.append(
        MechanicalComponent(
            name=f"MechanicalHole_{len(result.mechanical_components) + 1}",
            geometry=CircleGeometry(
                center=Point(0.0, 0.0), radius=circle.radius, source_layer=source
            ),
            placements=(circle.center,),
            plating=plating,
            source=source,
        )
    )


def _mapped_role(layer_name: str, layer_map: dict[str, str] | None) -> str | None:
    if layer_map and layer_name in layer_map:
        return layer_map[layer_name]
    return _classify_layer(layer_name)


def _classify_layer(layer_name: str) -> str | None:
    lower = layer_name.lower()
    matches = [
        (len(pattern), role)
        for role, patterns in _LAYER_PATTERNS.items()
        for pattern in patterns
        if pattern in lower
    ]
    return max(matches)[1] if matches else None


def _detect_units(doc: Drawing) -> str | None:
    code = int(doc.header.get("$INSUNITS", 0))
    if not code:
        return None
    try:
        return units.decode(code) or units.unit_name(code)
    except IndexError as exc:
        raise ValueError(f"Unsupported DXF unit code {code}; pass --unit") from exc


def _resolve_unit_scale(doc: Drawing, forced_unit: str | None, msp) -> float:
    if forced_unit:
        if forced_unit not in _UNIT_TO_MM:
            raise ValueError(f"Invalid unit {forced_unit!r}; expected one of {sorted(_UNIT_TO_MM)}")
        return _UNIT_TO_MM[forced_unit]

    code = int(doc.header.get("$INSUNITS", 0))
    if code:
        decoded = _detect_units(doc)
        if decoded in _UNIT_TO_MM:
            return _UNIT_TO_MM[decoded]
        try:
            return units.conversion_factor(InsertUnits(code), InsertUnits.Millimeters)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported DXF unit code {code}; pass --unit") from exc

    xs: list[float] = []
    ys: list[float] = []
    for entity in msp:
        _collect_entity_coords(entity, xs, ys)
    raw_extent = max(max(xs) - min(xs), max(ys) - min(ys)) if xs and ys else 0.0
    return _UNIT_TO_MM["mil"] if raw_extent > 500 else 1.0


def _collect_entity_coords(entity, xs: list[float], ys: list[float]) -> None:
    etype = entity.dxftype()
    if etype == "LINE":
        xs.extend([entity.dxf.start.x, entity.dxf.end.x])
        ys.extend([entity.dxf.start.y, entity.dxf.end.y])
    elif etype == "CIRCLE":
        cx, cy = entity.dxf.center.x, entity.dxf.center.y
        r = entity.dxf.radius
        xs.extend([cx - r, cx + r])
        ys.extend([cy - r, cy + r])
    elif etype == "ARC":
        cx, cy = entity.dxf.center.x, entity.dxf.center.y
        r = entity.dxf.radius
        xs.extend([cx - r, cx + r])
        ys.extend([cy - r, cy + r])
    elif etype == "LWPOLYLINE":
        for x, y, *_ in entity.get_points(format="xyseb"):
            xs.append(x)
            ys.append(y)
    elif etype == "SPLINE":
        try:
            for pt in entity.control_points:
                xs.append(pt[0])
                ys.append(pt[1])
        except Exception as exc:
            _logger.warning("Could not extract SPLINE control points: %s", exc)


def _parse_arc_entity(entity, unit_scale: float) -> ArcPathSegment:
    cx = entity.dxf.center.x * unit_scale
    cy = entity.dxf.center.y * unit_scale
    radius = entity.dxf.radius * unit_scale
    start_angle = entity.dxf.start_angle
    end_angle = start_angle + (entity.dxf.end_angle - start_angle) % 360
    return ArcPathSegment(
        center=Point(cx, cy),
        radius=radius,
        start_angle=start_angle,
        end_angle=end_angle,
        start_point=Point(
            cx + radius * math.cos(math.radians(start_angle)),
            cy + radius * math.sin(math.radians(start_angle)),
        ),
        end_point=Point(
            cx + radius * math.cos(math.radians(end_angle)),
            cy + radius * math.sin(math.radians(end_angle)),
        ),
    )


def _parse_lwpolyline(entity, unit_scale: float) -> ClosedPath | None:
    if not entity.closed:
        return None
    raw_points = list(entity.get_points(format="xyseb"))
    points = [(p[0] * unit_scale, p[1] * unit_scale) for p in raw_points]
    bulges = [p[4] if len(p) > 4 else 0.0 for p in raw_points]
    return lwpolyline_to_closed_path(points, bulges, entity.dxf.layer)


def _parse_text_entity(entity, unit_scale: float) -> TextGeometry:
    if entity.dxftype() == "MTEXT":
        content = entity.text
        pos = entity.dxf.insert
        height = entity.dxf.char_height
        rotation = entity.get_rotation()
    else:
        content = entity.dxf.text
        pos = entity.dxf.insert
        height = entity.dxf.height
        rotation = getattr(entity.dxf, "rotation", 0.0)
    return TextGeometry(
        content=content,
        position=Point(pos.x * unit_scale, pos.y * unit_scale),
        height=height * unit_scale,
        rotation=rotation,
        source_layer=entity.dxf.layer,
    )


def _parse_hatch_entity(entity, unit_scale: float) -> DxfHatch | None:
    try:
        is_solid = bool(entity.dxf.solid_fill)
    except Exception:
        is_solid = False

    boundary_paths: list[ClosedPath] = []
    try:
        for bpath in entity.paths:
            if hasattr(bpath, "vertices"):
                verts = list(bpath.vertices)
                if len(verts) >= 3:
                    pts = [(v[0] * unit_scale, v[1] * unit_scale) for v in verts]
                    bulges = [v[2] if len(v) > 2 else 0.0 for v in verts]
                    boundary_paths.append(lwpolyline_to_closed_path(pts, bulges, entity.dxf.layer))
            elif hasattr(bpath, "edges"):
                lines = []
                arcs = []
                for edge in bpath.edges:
                    if edge.EDGE_TYPE == "LineEdge":
                        lines.append(
                            (
                                Point(edge.start[0] * unit_scale, edge.start[1] * unit_scale),
                                Point(edge.end[0] * unit_scale, edge.end[1] * unit_scale),
                            )
                        )
                    elif edge.EDGE_TYPE == "ArcEdge":
                        cx = edge.center[0] * unit_scale
                        cy = edge.center[1] * unit_scale
                        radius = edge.radius * unit_scale
                        sweep = (edge.end_angle - edge.start_angle) % 360 or 360
                        start_angle = edge.start_angle if edge.ccw else edge.end_angle
                        end_angle = start_angle + (sweep if edge.ccw else -sweep)
                        arcs.append(
                            ArcPathSegment(
                                center=Point(cx, cy),
                                radius=radius,
                                start_angle=start_angle,
                                end_angle=end_angle,
                                start_point=Point(
                                    cx + radius * math.cos(math.radians(start_angle)),
                                    cy + radius * math.sin(math.radians(start_angle)),
                                ),
                                end_point=Point(
                                    cx + radius * math.cos(math.radians(end_angle)),
                                    cy + radius * math.sin(math.radians(end_angle)),
                                ),
                            )
                        )
                boundary_paths.extend(
                    assemble_closed_paths(lines, arcs, source_layer=entity.dxf.layer)
                )
    except Exception as exc:
        _logger.warning("Failed to parse HATCH boundary on layer %r: %s", entity.dxf.layer, exc)
        return None

    if not boundary_paths:
        return None
    return DxfHatch(tuple(boundary_paths), is_solid=is_solid, layer=entity.dxf.layer)
