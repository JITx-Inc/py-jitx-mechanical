"""IDF 2.0/3.0 mechanical importer, including EMN board files."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from jitxlib.mechanical.geometry import arc_from_chord, circle_from_center
from jitxlib.mechanical.models import (
    CircleGeometry,
    ClosedPath,
    Geometry,
    HolePlating,
    HolePolicy,
    ImportMessage,
    LinePathSegment,
    MechanicalAnnotation,
    MechanicalComponent,
    MechanicalHole,
    MechanicalImport,
    MechanicalPlacement,
    MechanicalRegion,
    MessageSeverity,
    Point,
    Pose,
    SourceObject,
)

logger = logging.getLogger(__name__)

# TNM (ten nanometers) is an IDF 2.0 unit that IDF 3.0 dropped.
_UNIT_TO_MM = {"MM": 1.0, "THOU": 0.0254, "TNM": 1e-5}
_FILE_TYPES = frozenset({"BOARD_FILE", "PANEL_FILE"})
_SUPPORTED_VERSIONS = frozenset({2.0, 3.0})
_ROUTING_LAYERS = frozenset({"TOP", "BOTTOM", "BOTH", "INNER", "ALL"})
_BOARD_SIDES = frozenset({"TOP", "BOTTOM", "BOTH"})
_OWNERS = frozenset({"ECAD", "MCAD", "UNOWNED"})
_PLACEMENT_SIDES = frozenset({"TOP", "BOTTOM"})
# FIXED is IDF 2.0 only and MCAD/ECAD are IDF 3.0 only; both are accepted for either version.
_PLACEMENT_STATUSES = frozenset({"PLACED", "UNPLACED", "FIXED", "MCAD", "ECAD"})
_CLOSURE_EPSILON = 1e-6


class IdfException(Exception):
    """Exception for IDF parsing errors."""


@dataclass(frozen=True)
class IdfHeader:
    filetype: str
    idf_version: float
    source_system: str
    date: str
    version: int
    name: str
    units: str


@dataclass(frozen=True)
class LoopPoint:
    id: int
    loop_n: int
    x: float
    y: float
    angle: float


@dataclass(frozen=True)
class RawHole:
    dia: float
    x: float
    y: float
    plating: str
    assoc: str
    hole_type: str
    owner: str


@dataclass(frozen=True)
class _Record:
    """One line of an IDF file, split into fields."""

    line: int
    fields: tuple[str, ...]


@dataclass(frozen=True)
class _Section:
    """A section's keyword line and the lines up to its end keyword."""

    keyword: str
    line: int
    header: tuple[str, ...]
    records: tuple[_Record, ...]


@dataclass(frozen=True)
class _RegionLayout:
    """The record that follows an outline or keepout section's keyword line.

    Field names are "layers" (routing layers), "side" (board side), "thickness"
    (thickness or maximum height), "min_height" (IDF 2.0 minimum height), and
    "identifier" (read but not kept). When the section has no layers or side
    field, ``default_layers`` applies.
    """

    role: str
    fields: tuple[str, ...]
    default_layers: str = ""


_REGION_LAYOUTS_V3 = {
    ".OTHER_OUTLINE": _RegionLayout("other_outline", ("identifier", "thickness", "side")),
    ".ROUTE_OUTLINE": _RegionLayout("route_outline", ("layers",)),
    ".PLACE_OUTLINE": _RegionLayout("place_outline", ("side", "thickness")),
    ".ROUTE_KEEPOUT": _RegionLayout("route_keepout", ("layers",)),
    ".VIA_KEEPOUT": _RegionLayout("via_keepout", ()),
    ".PLACE_KEEPOUT": _RegionLayout("place_keepout", ("side", "thickness")),
}
# IDF 2.0 routing outlines apply to all routing layers and placement outlines to
# both board sides; IDF 3.0 added the records that select them.
_REGION_LAYOUTS_V2 = {
    ".OTHER_OUTLINE": _RegionLayout("other_outline", ("identifier", "thickness")),
    ".ROUTE_OUTLINE": _RegionLayout("route_outline", (), default_layers="ALL"),
    ".PLACE_OUTLINE": _RegionLayout("place_outline", (), default_layers="BOTH"),
    ".ROUTE_KEEPOUT": _RegionLayout("route_keepout", ("layers",)),
    ".VIA_KEEPOUT": _RegionLayout("via_keepout", ()),
    ".PLACE_KEEPOUT": _RegionLayout("place_keepout", ("side", "thickness", "min_height")),
}
_SECTION_KEYWORDS = frozenset(
    {
        ".HEADER",
        ".BOARD_OUTLINE",
        ".PANEL_OUTLINE",
        ".DRILLED_HOLES",
        ".NOTES",
        ".PLACEMENT",
        *_REGION_LAYOUTS_V3,
    }
)


def import_idf(
    idf_path: str,
    *,
    hole_policy: HolePolicy | str = HolePolicy.CUTOUT,
) -> MechanicalImport:
    """Return an IDF board or panel file's geometry in millimeters, with conversion messages.

    Reads IDF 2.0 and 3.0 board and panel files, such as EMN files, whatever the
    file name suffix. Each line is one record, as the IDF specifications define,
    and lines that begin with ``#`` are comments. Library files and XML
    interchange files are not supported.

    Args:
        idf_path: The IDF file to import.
        hole_policy: Whether plated holes and holes of unknown plating become
            Board cutouts or connectable components. Unplated holes are always
            cutouts.

    Returns:
        The imported geometry, source metadata, and messages.

    Raises:
        IdfException: If the file is not an IDF 2.0 or 3.0 board or panel file,
            a section or line is missing or has the wrong number of fields, an
            owner or a placement side or status is not a known value, or the
            file has more than one board outline.
        ValueError: If ``hole_policy`` is not recognized or a numeric field is not
            a number.
        OSError: If the file cannot be read.
    """

    parser = IdfParser(idf_path, hole_policy=HolePolicy(hole_policy))
    return parser.parse()


class IdfParser:
    """Parser behind `import_idf` for IDF 2.0 and 3.0 text files."""

    def __init__(self, filename: str, *, hole_policy: HolePolicy = HolePolicy.CUTOUT):
        self.filename = filename
        self.hole_policy = hole_policy
        self.ucnv = 1.0
        self.idf_version = 3.0
        self.loop_id_seq = 0
        self.header: IdfHeader | None = None
        self.result = MechanicalImport(
            source_path=filename,
            source_format=_format_from_suffix(filename),
        )

    def parse(self) -> MechanicalImport:
        records = _read_records(Path(self.filename).read_text())

        board_outlines: list[tuple[Geometry, list[Geometry]]] = []
        panel_outlines: list[tuple[Geometry, list[Geometry]]] = []

        for section in self._sections(records):
            keyword = section.keyword
            if self.header is None and keyword != ".HEADER":
                raise _error(
                    section.line, f"expected .HEADER as the first section, found {keyword}"
                )
            if keyword == ".HEADER":
                self._parse_header(section)
            elif keyword in (".BOARD_OUTLINE", ".PANEL_OUTLINE"):
                outline = self._parse_board_or_panel_outline(section)
                if outline is not None and keyword == ".PANEL_OUTLINE":
                    panel_outlines.append(outline)
                elif outline is not None:
                    board_outlines.append(outline)
            elif keyword in _REGION_LAYOUTS_V3:
                layouts = _REGION_LAYOUTS_V2 if self.idf_version < 3.0 else _REGION_LAYOUTS_V3
                self._parse_region(section, layouts[keyword])
            elif keyword == ".DRILLED_HOLES":
                self._parse_holes(section)
            elif keyword == ".NOTES":
                self._parse_notes(section)
            elif keyword == ".PLACEMENT":
                self._parse_placement(section)
            else:
                self.result.unclassified.append(
                    SourceObject(
                        "unknown_section", keyword, detail=f"{len(section.records)} line(s)"
                    )
                )

        self._finish_primary_outline(board_outlines, panel_outlines)
        if self.header is None:
            raise IdfException("Expected exactly 1 header, found 0")
        if self.result.board_outline is None:
            raise IdfException("No board outline or panel outline found")
        return self.result

    def _sections(self, records: list[_Record]) -> Iterator[_Section]:
        i = 0
        while i < len(records):
            record = records[i]
            keyword = record.fields[0].upper()
            if not keyword.startswith(".") or keyword.startswith(".END_"):
                self.result.warn(
                    f"Ignored line {record.line} outside any section: {record.fields[0]}"
                )
                i += 1
                continue
            end_keyword = ".END_" + keyword[1:]
            end = _find_end(records, i + 1, end_keyword)
            if end is None:
                if keyword in _SECTION_KEYWORDS:
                    raise _error(record.line, f"{keyword} has no {end_keyword}")
                self.result.unclassified.append(SourceObject("unknown_token", record.fields[0]))
                i += 1
                continue
            yield _Section(keyword, record.line, record.fields[1:], tuple(records[i + 1 : end]))
            i = end + 1

    def _parse_header(self, section: _Section) -> None:
        if self.header is not None:
            raise _error(section.line, "found a second .HEADER section")
        _expect_keyword_only(section)
        if len(section.records) != 2:
            raise _error(
                section.line,
                f".HEADER has {len(section.records)} line(s), expected 2: "
                "FILE_TYPE IDF_VERSION SOURCE DATE FILE_VERSION, then BOARD_NAME UNITS",
            )
        file_record, board_record = section.records
        _check_fields(
            file_record, 5, ".HEADER record (FILE_TYPE IDF_VERSION SOURCE DATE FILE_VERSION)"
        )
        _check_fields(board_record, 2, ".HEADER record (BOARD_NAME UNITS)")
        filetype, version_text, source_system, date, file_version = file_record.fields
        if filetype.upper() not in _FILE_TYPES:
            raise _error(
                file_record.line,
                f"file type {filetype!r} is not supported; expected BOARD_FILE or PANEL_FILE",
            )
        idf_version = _to_float(version_text, file_record.line, "IDF version")
        if idf_version not in _SUPPORTED_VERSIONS:
            raise _error(
                file_record.line,
                f"IDF version {version_text} is not supported; expected 2.0 or 3.0",
            )
        name, units = board_record.fields
        self.header = IdfHeader(
            filetype=filetype,
            idf_version=idf_version,
            source_system=source_system,
            date=date,
            version=_to_int(file_version, file_record.line, "file version"),
            name=name,
            units=units,
        )
        self.idf_version = idf_version
        self.ucnv = _UNIT_TO_MM.get(units.upper(), 1.0)
        self.result.source_units = units
        self.result.unit_scale = self.ucnv
        if units.upper() not in _UNIT_TO_MM:
            self.result.warn(f"Unknown IDF units {units!r}; assuming millimeters")

    def _section_owner(self, section: _Section) -> str:
        if len(section.header) > 1:
            raise _error(
                section.line,
                f"{section.keyword} line has {len(section.header)} fields after the keyword, "
                "expected only the owner",
            )
        if not section.header:
            if self.idf_version >= 3.0:
                self.result.warn(
                    f"IDF {self.idf_version} {section.keyword} on line {section.line} has no owner",
                    source=section.keyword,
                )
            return ""
        owner = section.header[0]
        if owner.upper() not in _OWNERS:
            raise _error(
                section.line,
                f"IDF {self.idf_version} {section.keyword} owner is {owner!r}; "
                "expected ECAD, MCAD, or UNOWNED",
            )
        return owner

    def _parse_board_or_panel_outline(
        self, section: _Section
    ) -> tuple[Geometry, list[Geometry]] | None:
        self._section_owner(section)
        if not section.records:
            return None
        thickness = section.records[0]
        _check_fields(thickness, 1, f"{section.keyword} thickness record")
        _to_float(thickness.fields[0], thickness.line, "thickness")
        loop_points = self._parse_loop_points(section, section.records[1:])
        geometries = self._points_to_geometries(loop_points, section.keyword)
        if not geometries:
            return None
        return geometries[0], geometries[1:]

    def _parse_region(self, section: _Section, layout: _RegionLayout) -> None:
        owner = self._section_owner(section)
        records = section.records
        values: dict[str, str] = {}
        thickness = 0.0
        if layout.fields:
            if not records:
                raise _error(section.line, f"{section.keyword} has no lines")
            first = records[0]
            names = " ".join(name.upper() for name in layout.fields)
            _check_fields(
                first,
                len(layout.fields),
                f"IDF {self.idf_version} {section.keyword} record ({names})",
            )
            values = dict(zip(layout.fields, first.fields, strict=True))
            if "thickness" in values:
                thickness = _to_float(values["thickness"], first.line, "thickness") * self.ucnv
            if "min_height" in values:
                min_height = _to_float(values["min_height"], first.line, "minimum height")
                if min_height > 0:
                    self.result.warn(
                        f"IDF {self.idf_version} {section.keyword} on line {first.line} "
                        f"has minimum height {values['min_height']}, which is not kept",
                        source=section.keyword,
                    )
            records = records[1:]
        layers = self._region_layers(section, layout, values)
        geometries = self._points_to_geometries(
            self._parse_loop_points(section, records), section.keyword
        )
        for idx, geometry in enumerate(geometries):
            self.result.regions.append(
                MechanicalRegion(
                    role=layout.role,
                    geometry=geometry,
                    source_name=section.keyword if idx == 0 else f"{section.keyword} cutout",
                    layers=layers,
                    owner=owner,
                    thickness=thickness,
                )
            )

    def _region_layers(
        self, section: _Section, layout: _RegionLayout, values: dict[str, str]
    ) -> str:
        if "layers" in values:
            return self._known_layers(section, values["layers"], _ROUTING_LAYERS, "ALL")
        if "side" in values:
            return self._known_layers(section, values["side"], _BOARD_SIDES, "BOTH")
        return layout.default_layers

    def _known_layers(
        self, section: _Section, value: str, known: frozenset[str], fallback: str
    ) -> str:
        if value.upper() in known:
            return value.upper()
        description = "all layers" if fallback == "ALL" else "both board sides"
        self.result.warn(
            f"Unknown {section.keyword} layer {value!r}; using {description}",
            source=section.keyword,
        )
        return fallback

    def _parse_loop_points(self, section: _Section, records: Sequence[_Record]) -> list[LoopPoint]:
        points = []
        for record in records:
            _check_fields(record, 4, f"{section.keyword} loop point (LOOP X Y ANGLE)")
            label, x, y, angle = record.fields
            points.append(
                LoopPoint(
                    id=self.loop_id_seq,
                    loop_n=_to_int(label, record.line, "loop label"),
                    x=_to_float(x, record.line, "X coordinate"),
                    y=_to_float(y, record.line, "Y coordinate"),
                    angle=_to_float(angle, record.line, "angle"),
                )
            )
            self.loop_id_seq += 1
        return points

    def _points_to_geometries(self, loop_points: list[LoopPoint], section: str) -> list[Geometry]:
        if not loop_points:
            return []

        loops: dict[int, list[LoopPoint]] = {}
        for point in loop_points:
            loops.setdefault(point.loop_n, []).append(point)

        geometries: list[Geometry] = []
        for loop_num, points in sorted(loops.items()):
            points.sort(key=lambda p: p.id)
            first = Point(points[0].x * self.ucnv, points[0].y * self.ucnv)
            current = first
            segments = []
            loop_circles: list[CircleGeometry] = []

            for point in points:
                new_point = Point(point.x * self.ucnv, point.y * self.ucnv)
                if point.angle == 0.0:
                    if _points_differ(current, new_point):
                        segments.append(LinePathSegment(start=current, end=new_point))
                    current = new_point
                elif abs(point.angle) == 360.0:
                    circle = circle_from_center(current, new_point, section=section)
                    if circle is not None:
                        loop_circles.append(circle)
                    current = new_point
                else:
                    arc = arc_from_chord(current, new_point, point.angle)
                    if arc is None:
                        self.result.warn(
                            f"Skipped invalid arc in loop {loop_num}",
                            source=section,
                        )
                    else:
                        segments.append(arc)
                    current = new_point

            if segments and _points_differ(current, first):
                segments.append(LinePathSegment(start=current, end=first))
            if segments:
                geometries.append(ClosedPath(tuple(segments), source_section=section))
            elif loop_circles:
                geometries.extend(loop_circles)
        return geometries

    def _parse_holes(self, section: _Section) -> None:
        _expect_keyword_only(section)
        if self.idf_version < 3.0:
            layout = "DIAMETER X Y PLATING ASSOCIATED_PART"
        else:
            layout = "DIAMETER X Y PLATING ASSOCIATED_PART HOLE_TYPE OWNER"
        for record in section.records:
            _check_fields(
                record,
                len(layout.split()),
                f"IDF {self.idf_version} .DRILLED_HOLES record ({layout})",
            )
            self._add_hole(self._raw_hole(record))

    def _raw_hole(self, record: _Record) -> RawHole:
        fields = record.fields
        return RawHole(
            dia=_to_float(fields[0], record.line, "hole diameter") * self.ucnv,
            x=_to_float(fields[1], record.line, "X coordinate") * self.ucnv,
            y=_to_float(fields[2], record.line, "Y coordinate") * self.ucnv,
            plating=fields[3],
            assoc=fields[4],
            hole_type=fields[5] if len(fields) > 5 else "",
            owner=fields[6] if len(fields) > 6 else "",
        )

    def _add_hole(self, raw: RawHole) -> None:
        plating = _plating(raw.plating)
        circle = CircleGeometry(
            center=Point(raw.x, raw.y),
            radius=raw.dia / 2.0,
            source_section=".DRILLED_HOLES",
        )
        imported_as = (
            "component"
            if self.hole_policy == HolePolicy.COMPONENT and plating != HolePlating.UNPLATED
            else "cutout"
        )
        self.result.holes.append(
            MechanicalHole(
                geometry=circle,
                plating=plating,
                associated_with=raw.assoc,
                hole_type=raw.hole_type,
                owner=raw.owner,
                imported_as=imported_as,
            )
        )
        if imported_as == "component":
            self._add_component_hole(circle, plating)
        else:
            self.result.board_cutouts.append(circle)
        if plating == HolePlating.UNKNOWN:
            self.result.messages.append(
                ImportMessage(
                    MessageSeverity.WARNING,
                    text=f"IDF drilled hole has unknown plating and was imported as a {imported_as}.",
                    source=".DRILLED_HOLES",
                    hint="Use --hole-policy component when the hole should be electrically connectable.",
                )
            )

    def _add_component_hole(self, circle: CircleGeometry, plating: HolePlating) -> None:
        key_radius = round(circle.radius, 6)
        for idx, component in enumerate(self.result.mechanical_components):
            if round(component.geometry.radius, 6) == key_radius and component.plating == plating:
                self.result.mechanical_components[idx] = MechanicalComponent(
                    name=component.name,
                    geometry=component.geometry,
                    placements=component.placements + (circle.center,),
                    plating=component.plating,
                    source=component.source,
                )
                return
        self.result.mechanical_components.append(
            MechanicalComponent(
                name=f"MechanicalHole_{len(self.result.mechanical_components) + 1}",
                geometry=CircleGeometry(center=Point(0.0, 0.0), radius=circle.radius),
                placements=(circle.center,),
                plating=plating,
                source=".DRILLED_HOLES",
            )
        )

    def _parse_notes(self, section: _Section) -> None:
        _expect_keyword_only(section)
        for record in section.records:
            _check_fields(record, 5, ".NOTES record (X Y HEIGHT LENGTH TEXT)")
            x, y, height, _length, text = record.fields
            self.result.annotations.append(
                MechanicalAnnotation(
                    role="note",
                    text=text.replace(chr(1), "").replace(chr(2), ""),
                    position=Point(
                        _to_float(x, record.line, "X coordinate") * self.ucnv,
                        _to_float(y, record.line, "Y coordinate") * self.ucnv,
                    ),
                    height=_to_float(height, record.line, "text height") * self.ucnv,
                    source_name=".NOTES",
                )
            )

    def _parse_placement(self, section: _Section) -> None:
        _expect_keyword_only(section)
        if self.idf_version < 3.0:
            layout = "X Y ROTATION SIDE [STATUS]"
            field_counts = (4, 5)
        else:
            layout = "X Y MOUNTING_OFFSET ROTATION SIDE STATUS"
            field_counts = (6,)
        records = section.records
        for i in range(0, len(records), 2):
            part = records[i]
            _check_fields(part, 3, ".PLACEMENT part record (PACKAGE PART_NUMBER REFDES)")
            package, part_number, refdes = part.fields
            if i + 1 == len(records):
                raise _error(part.line, f".PLACEMENT part {refdes!r} has no location line")
            location = records[i + 1]
            _check_fields(
                location,
                field_counts,
                f"IDF {self.idf_version} .PLACEMENT location for {refdes!r} ({layout})",
            )
            if self.idf_version < 3.0:
                x, y, rotation, side = location.fields[:4]
                # IDF 2.0 assumes a component with a blank status is placed.
                status = location.fields[4] if len(location.fields) == 5 else ""
                status = status or "PLACED"
            else:
                x, y, _offset, rotation, side, status = location.fields
            if side.upper() not in _PLACEMENT_SIDES:
                raise _error(
                    location.line,
                    f".PLACEMENT side {side!r} for {refdes!r} is not TOP or BOTTOM",
                )
            if status.upper() not in _PLACEMENT_STATUSES:
                raise _error(
                    location.line,
                    f".PLACEMENT status {status!r} for {refdes!r} is not "
                    "PLACED, UNPLACED, FIXED, MCAD, or ECAD",
                )
            self.result.placements.append(
                MechanicalPlacement(
                    package=package,
                    part_number=part_number,
                    refdes=refdes,
                    pose=Pose(
                        _to_float(x, location.line, "X coordinate") * self.ucnv,
                        _to_float(y, location.line, "Y coordinate") * self.ucnv,
                        _to_float(rotation, location.line, "rotation"),
                    ),
                    side=side,
                    status=status,
                )
            )

    def _finish_primary_outline(
        self,
        board_outlines: list[tuple[Geometry, list[Geometry]]],
        panel_outlines: list[tuple[Geometry, list[Geometry]]],
    ) -> None:
        if board_outlines and panel_outlines:
            self.result.warn(
                "File contains both .BOARD_OUTLINE and .PANEL_OUTLINE; using .BOARD_OUTLINE"
            )
            primary = board_outlines
        elif board_outlines:
            primary = board_outlines
        else:
            primary = panel_outlines

        if not primary:
            return
        if len(primary) != 1:
            raise IdfException(f"Expected exactly 1 board/panel outline, found {len(primary)}")
        self.result.board_outline = primary[0][0]
        self.result.board_cutouts.extend(primary[0][1])


def _read_records(content: str) -> list[_Record]:
    records = []
    for number, line in enumerate(content.replace("\r\n", "\n").split("\n"), start=1):
        text = line.strip()
        if text and not text.startswith("#"):
            records.append(_Record(number, tuple(_split_fields(text))))
    return records


def _split_fields(line: str) -> list[str]:
    fields = []
    in_quote = False
    current = ""
    for char in line:
        if in_quote:
            if char == '"':
                fields.append(current)
                current = ""
                in_quote = False
            else:
                current += char
        elif char == '"':
            in_quote = True
        elif char in (" ", "\t"):
            if current:
                fields.append(current)
                current = ""
        else:
            current += char
    if current:
        fields.append(current)
    return fields


def _find_end(records: list[_Record], start: int, end_keyword: str) -> int | None:
    for i in range(start, len(records)):
        if records[i].fields[0].upper() == end_keyword:
            return i
    return None


def _expect_keyword_only(section: _Section) -> None:
    if section.header:
        raise _error(
            section.line,
            f"{section.keyword} line has unexpected fields after the keyword: "
            f"{' '.join(section.header)}",
        )


def _check_fields(record: _Record, count: int | tuple[int, ...], what: str) -> None:
    counts = (count,) if isinstance(count, int) else count
    if len(record.fields) not in counts:
        expected = " or ".join(str(c) for c in counts)
        raise _error(record.line, f"{what} has {len(record.fields)} field(s), expected {expected}")


def _to_float(value: str, line: int, what: str) -> float:
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"line {line}: {what} {value!r} is not a number") from exc


def _to_int(value: str, line: int, what: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"line {line}: {what} {value!r} is not an integer") from exc


def _error(line: int, message: str) -> IdfException:
    return IdfException(f"line {line}: {message}")


def _points_differ(a: Point, b: Point) -> bool:
    return abs(a.x - b.x) > _CLOSURE_EPSILON or abs(a.y - b.y) > _CLOSURE_EPSILON


def _plating(value: str) -> HolePlating:
    normalized = value.strip().upper()
    if normalized in {"NPTH", "UNPLATED", "NO", "N"}:
        return HolePlating.UNPLATED
    if normalized in {"PTH", "PLATED", "YES", "Y"}:
        return HolePlating.PLATED
    return HolePlating.UNKNOWN


def _format_from_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if suffix in {"emn", "idf", "idx", "bdf"}:
        return suffix
    return "idf"
