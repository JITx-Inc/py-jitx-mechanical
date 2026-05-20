"""EMN/IDF/IDX mechanical importer."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from jitx_mechanical.geometry import arc_from_chord, full_circle_from_diameter
from jitx_mechanical.models import (
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

_UNIT_TO_MM = {"MM": 1.0, "THOU": 0.0254}
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


def import_idf(
    idf_path: str,
    *,
    hole_policy: HolePolicy | str = HolePolicy.CUTOUT,
) -> MechanicalImport:
    """Import an EMN/IDF/IDX-compatible mechanical file."""

    parser = IdfParser(idf_path, hole_policy=HolePolicy(hole_policy))
    return parser.parse()


class IdfParser:
    """Parser for IDF 2.0/3.0 style EMN, IDF, and compatible IDX files."""

    def __init__(self, filename: str, *, hole_policy: HolePolicy = HolePolicy.CUTOUT):
        self.filename = filename
        self.hole_policy = hole_policy
        self.ucnv = 1.0
        self.loop_id_seq = 0
        self.header: IdfHeader | None = None
        self.result = MechanicalImport(
            source_path=filename,
            source_format=_format_from_suffix(filename),
        )

    def parse(self) -> MechanicalImport:
        content = Path(self.filename).read_text()
        tokens = self._tokenize(content)

        board_outlines: list[tuple[Geometry, list[Geometry]]] = []
        panel_outlines: list[tuple[Geometry, list[Geometry]]] = []

        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token == ".HEADER":
                end_pos = self._find_section_end(tokens[i + 1 :], ".END_HEADER")
                self._parse_header(tokens[i + 1 : i + 1 + end_pos])
                i = i + 1 + end_pos + 1
            elif token in (".BOARD_OUTLINE", ".PANEL_OUTLINE"):
                end_marker = ".END_PANEL_OUTLINE" if token == ".PANEL_OUTLINE" else ".END_BOARD_OUTLINE"
                end_pos = self._find_section_end(tokens[i + 1 :], end_marker)
                outline = self._parse_board_or_panel_outline(token, tokens[i + 1 : i + 1 + end_pos])
                if outline is not None:
                    if token == ".PANEL_OUTLINE":
                        panel_outlines.append(outline)
                    else:
                        board_outlines.append(outline)
                i = i + 1 + end_pos + 1
            elif token == ".OTHER_OUTLINE":
                i = self._parse_outline_region(tokens, i, ".END_OTHER_OUTLINE", "other_outline", skip=4)
            elif token == ".ROUTE_OUTLINE":
                i = self._parse_outline_region(tokens, i, ".END_ROUTE_OUTLINE", "route_outline", skip=2)
            elif token == ".PLACE_OUTLINE":
                i = self._parse_outline_region(tokens, i, ".END_PLACE_OUTLINE", "place_outline", skip=3, has_thickness=True)
            elif token == ".ROUTE_KEEPOUT":
                i = self._parse_outline_region(tokens, i, ".END_ROUTE_KEEPOUT", "route_keepout", skip=2)
            elif token == ".VIA_KEEPOUT":
                i = self._parse_outline_region(tokens, i, ".END_VIA_KEEPOUT", "via_keepout", skip=1)
            elif token == ".PLACE_KEEPOUT":
                i = self._parse_outline_region(tokens, i, ".END_PLACE_KEEPOUT", "place_keepout", skip=3, has_thickness=True)
            elif token == ".DRILLED_HOLES":
                end_pos = self._find_section_end(tokens[i + 1 :], ".END_DRILLED_HOLES")
                version = self.header.idf_version if self.header else 3.0
                self._parse_holes(tokens[i + 1 : i + 1 + end_pos], version)
                i = i + 1 + end_pos + 1
            elif token == ".NOTES":
                end_pos = self._find_section_end(tokens[i + 1 :], ".END_NOTES")
                self._parse_notes(tokens[i + 1 : i + 1 + end_pos])
                i = i + 1 + end_pos + 1
            elif token == ".PLACEMENT":
                end_pos = self._find_section_end(tokens[i + 1 :], ".END_PLACEMENT")
                self._parse_placement(tokens[i + 1 : i + 1 + end_pos])
                i = i + 1 + end_pos + 1
            elif token.startswith(".") and not token.startswith(".END_"):
                i = self._skip_unknown_section(tokens, i, token)
            else:
                i += 1

        self._finish_primary_outline(board_outlines, panel_outlines)
        if self.header is None:
            raise IdfException("Expected exactly 1 header, found 0")
        if self.result.board_outline is None:
            raise IdfException("No board outline or panel outline found")
        return self.result

    def _tokenize(self, content: str) -> list[str]:
        tokens = []
        for line in content.replace("\r\n", "\n").split("\n"):
            tokens.extend(self._tokenize_line(line.strip()))
        return tokens

    def _tokenize_line(self, line: str) -> list[str]:
        tokens = []
        i = 0
        in_quote = False
        current = ""
        while i < len(line):
            char = line[i]
            if in_quote:
                if char == '"':
                    tokens.append(current)
                    current = ""
                    in_quote = False
                else:
                    current += char
            elif char == '"':
                in_quote = True
            elif char in (" ", "\t"):
                if current:
                    tokens.append(current)
                    current = ""
            else:
                current += char
            i += 1
        if current:
            tokens.append(current)
        return tokens

    def _find_section_end(self, tokens: list[str], match_str: str) -> int:
        try:
            return tokens.index(match_str)
        except ValueError as exc:
            raise IdfException(f"{match_str} not found.") from exc

    def _parse_header(self, header_tokens: list[str]) -> None:
        if len(header_tokens) < 7:
            raise IdfException("HEADER section has too few fields")
        self.header = IdfHeader(
            filetype=header_tokens[0],
            idf_version=float(header_tokens[1]),
            source_system=header_tokens[2],
            date=header_tokens[3],
            version=int(header_tokens[4]),
            name=header_tokens[5],
            units=header_tokens[6],
        )
        self.ucnv = _UNIT_TO_MM.get(self.header.units.upper(), 1.0)
        self.result.source_units = self.header.units
        self.result.unit_scale = self.ucnv
        if self.header.units.upper() not in _UNIT_TO_MM:
            self.result.warn(f"Unknown IDF units {self.header.units!r}; assuming millimeters")

    def _parse_board_or_panel_outline(
        self, section: str, section_tokens: list[str]
    ) -> tuple[Geometry, list[Geometry]] | None:
        if self.header and self.header.idf_version < 3.0:
            if len(section_tokens) < 1:
                return None
            loop_tokens = section_tokens[1:]
        else:
            if len(section_tokens) < 2:
                return None
            loop_tokens = section_tokens[2:]
        geometries = self._points_to_geometries(self._parse_loop_points(loop_tokens), section)
        if not geometries:
            return None
        return geometries[0], geometries[1:]

    def _parse_outline_region(
        self,
        tokens: list[str],
        i: int,
        end_marker: str,
        role: str,
        *,
        skip: int,
        has_thickness: bool = False,
    ) -> int:
        end_pos = self._find_section_end(tokens[i + 1 :], end_marker)
        section_tokens = tokens[i + 1 : i + 1 + end_pos]
        owner = section_tokens[0] if section_tokens else ""
        layers = section_tokens[1] if len(section_tokens) > 1 else ""
        thickness = 0.0
        if has_thickness and len(section_tokens) > 2:
            thickness = float(section_tokens[2]) * self.ucnv
        geometries = self._points_to_geometries(self._parse_loop_points(section_tokens[skip:]), tokens[i])
        for idx, geometry in enumerate(geometries):
            self.result.regions.append(
                MechanicalRegion(
                    role=role,
                    geometry=geometry,
                    source_name=tokens[i] if idx == 0 else f"{tokens[i]} cutout",
                    layers=layers,
                    owner=owner,
                    thickness=thickness,
                )
            )
        return i + 1 + end_pos + 1

    def _parse_loop_points(self, tokens: list[str]) -> list[LoopPoint]:
        points = []
        i = 0
        while i + 3 < len(tokens):
            points.append(
                LoopPoint(
                    id=self.loop_id_seq,
                    loop_n=int(tokens[i]),
                    x=float(tokens[i + 1]),
                    y=float(tokens[i + 2]),
                    angle=float(tokens[i + 3]),
                )
            )
            self.loop_id_seq += 1
            i += 4
        if i < len(tokens):
            self.result.warn(
                f"Loop point data has {len(tokens) - i} trailing token(s)",
                source="loop_points",
            )
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
                    circle = full_circle_from_diameter(current, new_point, section=section)
                    if circle is not None:
                        loop_circles.append(circle)
                    current = new_point
                else:
                    arc = arc_from_chord(current, new_point, point.angle, loop_num=loop_num)
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

    def _parse_holes(self, tokens: list[str], idf_version: float) -> None:
        record_size = 5 if idf_version < 3.0 else 7
        i = 0
        while i + record_size - 1 < len(tokens):
            hole = self._raw_hole(tokens[i : i + record_size], idf_version)
            self._add_hole(hole)
            i += record_size
        if i < len(tokens):
            self.result.warn(
                f"DRILLED_HOLES has {len(tokens) - i} trailing token(s)",
                source=".DRILLED_HOLES",
            )

    def _raw_hole(self, tokens: list[str], idf_version: float) -> RawHole:
        if idf_version < 3.0:
            return RawHole(
                dia=float(tokens[0]) * self.ucnv,
                x=float(tokens[1]) * self.ucnv,
                y=float(tokens[2]) * self.ucnv,
                plating=tokens[3],
                assoc=tokens[4],
                hole_type="",
                owner="",
            )
        return RawHole(
            dia=float(tokens[0]) * self.ucnv,
            x=float(tokens[1]) * self.ucnv,
            y=float(tokens[2]) * self.ucnv,
            plating=tokens[3],
            assoc=tokens[4],
            hole_type=tokens[5],
            owner=tokens[6],
        )

    def _add_hole(self, raw: RawHole) -> None:
        plating = _plating(raw.plating)
        circle = CircleGeometry(
            center=Point(raw.x, raw.y),
            radius=raw.dia / 2.0,
            source_section=".DRILLED_HOLES",
        )
        imported_as = "component" if self.hole_policy == HolePolicy.COMPONENT else "cutout"
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
        if imported_as == "component" or plating == HolePlating.PLATED:
            if self.hole_policy == HolePolicy.COMPONENT:
                self._add_component_hole(circle, plating)
            else:
                self.result.board_cutouts.append(circle)
        else:
            self.result.board_cutouts.append(circle)
        if plating == HolePlating.UNKNOWN:
            self.result.messages.append(
                ImportMessage(
                    MessageSeverity.WARNING,
                    text="IDF drilled hole has unknown plating and was imported as an unplated cutout.",
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

    def _parse_notes(self, tokens: list[str]) -> None:
        i = 0
        while i + 4 < len(tokens):
            text = tokens[i + 4].replace(chr(1), "").replace(chr(2), "")
            self.result.annotations.append(
                MechanicalAnnotation(
                    role="note",
                    text=text,
                    position=Point(float(tokens[i]) * self.ucnv, float(tokens[i + 1]) * self.ucnv),
                    height=float(tokens[i + 2]) * self.ucnv,
                    source_name=".NOTES",
                )
            )
            i += 5
        if i < len(tokens):
            self.result.warn(f"NOTES has {len(tokens) - i} trailing token(s)", source=".NOTES")

    def _parse_placement(self, tokens: list[str]) -> None:
        i = 0
        while i + 8 < len(tokens):
            self.result.placements.append(
                MechanicalPlacement(
                    package=tokens[i],
                    part_number=tokens[i + 1],
                    refdes=tokens[i + 2],
                    pose=Pose(
                        float(tokens[i + 3]) * self.ucnv,
                        float(tokens[i + 4]) * self.ucnv,
                        float(tokens[i + 6]),
                    ),
                    side=tokens[i + 7],
                    status=tokens[i + 8],
                )
            )
            i += 9
        if i < len(tokens):
            self.result.warn(
                f"PLACEMENT has {len(tokens) - i} trailing token(s)",
                source=".PLACEMENT",
            )

    def _skip_unknown_section(self, tokens: list[str], i: int, token: str) -> int:
        end_marker = ".END_" + token[1:]
        try:
            end_pos = self._find_section_end(tokens[i + 1 :], end_marker)
        except IdfException:
            self.result.unclassified.append(SourceObject("unknown_token", token))
            return i + 1
        self.result.unclassified.append(
            SourceObject("unknown_section", token, detail=f"{end_pos} token(s)")
        )
        return i + 1 + end_pos + 1

    def _finish_primary_outline(
        self,
        board_outlines: list[tuple[Geometry, list[Geometry]]],
        panel_outlines: list[tuple[Geometry, list[Geometry]]],
    ) -> None:
        if board_outlines and panel_outlines:
            self.result.warn("File contains both .BOARD_OUTLINE and .PANEL_OUTLINE; using .BOARD_OUTLINE")
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


def sanitize_identifier(name: str) -> str:
    if name and (name[0].isalpha() or name[0] == "_"):
        return re.sub(r"[^a-zA-Z0-9_]", "_", name)
    return "_" + re.sub(r"[^a-zA-Z0-9_]", "_", name)
