from __future__ import annotations

import re
from pathlib import Path

import pytest
from synthetic import place_keepout, route_keepout, via_keepout, write_emn

from jitxlib.mechanical.importers.idf import IdfException, import_idf
from jitxlib.mechanical.models import CircleGeometry, ClosedPath, HolePlating, HolePolicy, Pose


def _write(tmp_path: Path, text: str, name: str = "test.emn") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


SIMPLE = """.HEADER
BOARD_FILE 3.0 "Test System" 2024/01/01.00:00:00 1
"TestBoard" MM
.END_HEADER

.BOARD_OUTLINE "ECAD"
1.6
0 0 0 0
0 100 0 0
0 100 50 0
0 0 50 0
0 0 0 0
.END_BOARD_OUTLINE
"""

IDF2_BOARD = """.HEADER
BOARD_FILE 2.0 "Test System" 1996/01/01.00:00:00 1
"TestBoard" MM
.END_HEADER

.BOARD_OUTLINE
1.6
0 0 0 0
0 100 0 0
0 100 50 0
0 0 50 0
0 0 0 0
.END_BOARD_OUTLINE
"""


def test_idf_simple_board_outline(tmp_path):
    imported = import_idf(str(_write(tmp_path, SIMPLE)))
    assert isinstance(imported.board_outline, ClosedPath)
    assert imported.source_units == "MM"
    assert imported.unit_scale == 1.0


def test_idf_board_cutout_loop(tmp_path):
    content = """.HEADER
BOARD_FILE 3.0 "Test System" 2024/01/01.00:00:00 1
"TestBoard" MM
.END_HEADER

.BOARD_OUTLINE "ECAD"
1.6
0 0 0 0
0 100 0 0
0 100 50 0
0 0 50 0
0 0 0 0
1 40 20 0
1 60 20 0
1 60 30 0
1 40 30 0
1 40 20 0
.END_BOARD_OUTLINE
"""
    imported = import_idf(str(_write(tmp_path, content)))
    assert len(imported.board_cutouts) == 1


def test_idf_drilled_holes_default_to_cutouts(tmp_path):
    content = (
        SIMPLE
        + """
.DRILLED_HOLES
2.0 10 10 "PTH" "VIA" "THRU" "OWNER1"
3.0 25 15 "NPTH" "MTG" "THRU" "OWNER2"
.END_DRILLED_HOLES
"""
    )
    imported = import_idf(str(_write(tmp_path, content)))
    assert len(imported.holes) == 2
    assert len([g for g in imported.board_cutouts if isinstance(g, CircleGeometry)]) == 2
    assert imported.holes[0].plating == HolePlating.PLATED
    assert imported.holes[1].plating == HolePlating.UNPLATED


def test_idf_hole_policy_component_deduplicates(tmp_path):
    content = (
        SIMPLE
        + """
.DRILLED_HOLES
2.0 10 10 "PTH" "VIA" "THRU" "OWNER1"
2.0 20 10 "PTH" "VIA" "THRU" "OWNER1"
.END_DRILLED_HOLES
"""
    )
    imported = import_idf(str(_write(tmp_path, content)), hole_policy=HolePolicy.COMPONENT)
    assert len(imported.mechanical_components) == 1
    assert len(imported.mechanical_components[0].placements) == 2


def test_idf_notes_placement_keepouts_and_unknown_sections_reported(tmp_path):
    content = (
        SIMPLE
        + """
.NOTES
15 25 1.5 12 "KEEP OUT AREA"
.END_NOTES

.PLACEMENT
"0603" "R0603_10K" "R1"
20 10 0 0 "TOP" "PLACED"
.END_PLACEMENT

.ROUTE_KEEPOUT "ECAD"
"TOP"
0 10 10 0
0 20 10 0
0 20 20 0
0 10 20 0
0 10 10 0
.END_ROUTE_KEEPOUT

.BEND "OWNER"
0 1 1 0
.END_BEND
"""
    )
    imported = import_idf(str(_write(tmp_path, content)))
    assert len(imported.annotations) == 1
    assert len(imported.placements) == 1
    assert any(region.role == "route_keepout" for region in imported.regions)
    assert any(obj.source_name == ".BEND" for obj in imported.unclassified)


@pytest.mark.parametrize(
    ("board", "r1_location", "c1_location"),
    [
        (IDF2_BOARD, "20 10 90", "30 15 180"),
        (SIMPLE, "20 10 0.5 90", "30 15 0.5 180"),
    ],
    ids=["idf2", "idf3"],
)
def test_idf_placement_record_layout_follows_header_version(
    tmp_path, board, r1_location, c1_location
):
    content = (
        board
        + f"""
.PLACEMENT
"0603" "R0603_10K" "R1"
{r1_location} "TOP" "PLACED"
"0805" "C0805_1U" "C1"
{c1_location} "BOTTOM" "PLACED"
.END_PLACEMENT
"""
    )
    imported = import_idf(str(_write(tmp_path, content)))
    assert [(p.refdes, p.pose, p.side, p.status) for p in imported.placements] == [
        ("R1", Pose(20, 10, 90), "TOP", "PLACED"),
        ("C1", Pose(30, 15, 180), "BOTTOM", "PLACED"),
    ]
    assert not imported.messages


@pytest.mark.parametrize(
    ("board", "records", "message"),
    [
        (
            SIMPLE,
            '"0603" "R0603_10K" "R1"\n20 10 90 "TOP" "PLACED"',
            "line 17: IDF 3.0 .PLACEMENT location for 'R1' "
            "(X Y MOUNTING_OFFSET ROTATION SIDE STATUS) has 5 field(s), expected 6",
        ),
        (
            SIMPLE,
            '"0603" "R0603_10K" "R1" 20 10 0 90 "TOP" "PLACED"',
            ".PLACEMENT part record (PACKAGE PART_NUMBER REFDES) has 9 field(s), expected 3",
        ),
        (
            SIMPLE,
            '"0603" "R0603_10K" "R1"\n20 10 0 90 "MIDDLE" "PLACED"',
            ".PLACEMENT side 'MIDDLE' for 'R1' is not TOP or BOTTOM",
        ),
        (
            IDF2_BOARD,
            '"0603" "R0603_10K" "R1"\n20 10 90 "TOP" "MOUNTED"',
            ".PLACEMENT status 'MOUNTED' for 'R1' is not PLACED, UNPLACED, FIXED, MCAD, or ECAD",
        ),
        (
            SIMPLE,
            '"0603" "R0603_10K" "R1"\n20 10 0 90 "TOP" "PLACED"\n"0805" "C0805_1U" "C1"',
            ".PLACEMENT part 'C1' has no location line",
        ),
    ],
    ids=[
        "idf2-location-in-idf3",
        "records-on-one-line",
        "unknown-side",
        "unknown-status",
        "missing-location",
    ],
)
def test_idf_malformed_placement_raises(tmp_path, board, records, message):
    content = board + f"\n.PLACEMENT\n{records}\n.END_PLACEMENT\n"
    with pytest.raises(IdfException, match=re.escape(message)):
        import_idf(str(_write(tmp_path, content)))


@pytest.mark.parametrize(
    ("section", "message"),
    [
        (
            ".ROUTE_KEEPOUT 1.6\nTOP\n0 10 10 0\n0 20 10 0\n0 10 10 0\n.END_ROUTE_KEEPOUT\n",
            "line 15: IDF 3.0 .ROUTE_KEEPOUT owner is '1.6'; expected ECAD, MCAD, or UNOWNED",
        ),
        (
            ".ROUTE_KEEPOUT ECAD TOP\n0 10 10 0\n0 20 10 0\n0 10 10 0\n.END_ROUTE_KEEPOUT\n",
            ".ROUTE_KEEPOUT line has 2 fields after the keyword, expected only the owner",
        ),
    ],
    ids=["not-an-owner", "record-on-keyword-line"],
)
def test_idf_invalid_section_owner_raises(tmp_path, section, message):
    with pytest.raises(IdfException, match=re.escape(message)):
        import_idf(str(_write(tmp_path, SIMPLE + "\n" + section)))


def test_idf3_section_without_owner_warns(tmp_path):
    section = (
        ".ROUTE_KEEPOUT\nTOP\n0 10 10 0\n0 20 10 0\n0 20 20 0\n0 10 10 0\n.END_ROUTE_KEEPOUT\n"
    )
    imported = import_idf(str(_write(tmp_path, SIMPLE + "\n" + section)))
    (region,) = imported.regions
    assert (region.layers, region.owner) == ("TOP", "")
    assert [m.text for m in imported.messages] == ["IDF 3.0 .ROUTE_KEEPOUT on line 15 has no owner"]


def test_idf_comments_and_lowercase_keywords(tmp_path):
    content = """# Exported by a test
.header
board_file 3.0 "Test System" 2024/01/01.00:00:00 1
"TestBoard" mm
.end_header
# Between sections
.board_outline ecad
1.6
0 0 0 0
0 100 0 0
0 100 50 0
0 0 0 0
.end_board_outline
"""
    imported = import_idf(str(_write(tmp_path, content)))
    assert isinstance(imported.board_outline, ClosedPath)
    assert imported.unit_scale == 1.0
    assert not imported.messages


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            '.HEADER\nLIBRARY_FILE 3.0 "Test" 2024/01/01.00:00:00 1\nLIB MM\n.END_HEADER\n',
            "line 2: file type 'LIBRARY_FILE' is not supported; expected BOARD_FILE or PANEL_FILE",
        ),
        (
            SIMPLE.replace("BOARD_FILE 3.0", "BOARD_FILE 4.0"),
            "line 2: IDF version 4.0 is not supported; expected 2.0 or 3.0",
        ),
        (
            SIMPLE.replace('"TestBoard" MM\n', ""),
            "line 1: .HEADER has 1 line(s), expected 2",
        ),
        (
            ".BOARD_OUTLINE ECAD\n1.6\n0 0 0 0\n.END_BOARD_OUTLINE\n" + SIMPLE,
            "line 1: expected .HEADER as the first section, found .BOARD_OUTLINE",
        ),
        (
            SIMPLE + '.HEADER\nBOARD_FILE 3.0 "Test" 2024/01/01.00:00:00 1\nB MM\n.END_HEADER\n',
            "line 14: found a second .HEADER section",
        ),
        (
            SIMPLE.replace("0 100 50 0\n", "0 100 50\n"),
            "line 10: .BOARD_OUTLINE loop point (LOOP X Y ANGLE) has 3 field(s), expected 4",
        ),
        (
            SIMPLE.replace(".END_BOARD_OUTLINE\n", ""),
            "line 6: .BOARD_OUTLINE has no .END_BOARD_OUTLINE",
        ),
    ],
    ids=[
        "library-file",
        "unsupported-version",
        "header-on-one-line",
        "section-before-header",
        "second-header",
        "short-loop-point",
        "missing-end",
    ],
)
def test_idf_malformed_file_raises(tmp_path, content, message):
    with pytest.raises(IdfException, match=re.escape(message)):
        import_idf(str(_write(tmp_path, content)))


def test_idf_non_number_raises_value_error_with_line(tmp_path):
    content = SIMPLE.replace("0 100 50 0\n", "0 100 abc 0\n")
    with pytest.raises(ValueError, match=re.escape("line 10: Y coordinate 'abc' is not a number")):
        import_idf(str(_write(tmp_path, content)))


def test_idf_line_outside_section_warns(tmp_path):
    imported = import_idf(str(_write(tmp_path, SIMPLE + "stray text\n")))
    assert [m.text for m in imported.messages] == ["Ignored line 14 outside any section: stray"]


def test_idf_arc_start_boundary_does_not_construct_jitx_arc(tmp_path):
    content = """.HEADER
BOARD_FILE 3.0 "Test" 2024/01/01.00:00:00 1
"Test" MM
.END_HEADER

.BOARD_OUTLINE "ECAD"
1.6
0 0 0 0
0 10 0 0
0 10 10 90
0 0 10 0
0 0 0 0
.END_BOARD_OUTLINE
"""
    imported = import_idf(str(_write(tmp_path, content)))
    assert isinstance(imported.board_outline, ClosedPath)


def test_360_degree_loop_is_center_and_point_on_circle(tmp_path):
    keepout = ".ROUTE_KEEPOUT ECAD\nALL\n0 10 10 0\n0 11.5 10 360\n.END_ROUTE_KEEPOUT"
    source = write_emn(tmp_path / "circle.emn", holes=[(2.0, 10, 10, "NPTH")], sections=[keepout])
    imported = import_idf(str(source))
    (region,) = imported.regions
    assert isinstance(region.geometry, CircleGeometry)
    assert (region.geometry.center.x, region.geometry.center.y) == (10, 10)
    assert region.geometry.radius == pytest.approx(1.5)


def test_region_layer_and_side_fields(tmp_path):
    other = ".OTHER_OUTLINE ECAD\nSTIFFENER 0.8 BOTTOM\n0 1 1 0\n0 4 1 0\n0 4 4 0\n0 1 1 0\n.END_OTHER_OUTLINE"
    unknown = ".ROUTE_KEEPOUT ECAD\nL7\n0 5 5 0\n0 6 5 0\n0 6 6 0\n0 5 5 0\n.END_ROUTE_KEEPOUT"
    source = write_emn(
        tmp_path / "fields.emn",
        sections=[
            other,
            via_keepout((10, 10, 12, 12)),
            place_keepout("BOTTOM", (20, 20, 22, 22), height=1.5),
            route_keepout("INNER", (30, 10, 32, 12)),
            unknown,
        ],
    )
    imported = import_idf(str(source))
    by_role = {region.role: region for region in imported.regions}
    assert by_role["other_outline"].layers == "BOTTOM"
    assert by_role["other_outline"].thickness == pytest.approx(0.8)
    assert by_role["via_keepout"].layers == ""
    assert by_role["place_keepout"].layers == "BOTTOM"
    assert by_role["place_keepout"].thickness == pytest.approx(1.5)
    route_layers = [r.layers for r in imported.regions if r.role == "route_keepout"]
    assert route_layers == ["INNER", "ALL"]
    assert any("'L7'" in message.text for message in imported.messages)
