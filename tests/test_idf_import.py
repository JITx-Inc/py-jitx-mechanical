from __future__ import annotations

from pathlib import Path

from jitx_mechanical.importers.idf import import_idf
from jitx_mechanical.models import CircleGeometry, ClosedPath, HolePlating, HolePolicy


def _write(tmp_path: Path, text: str, name: str = "test.emn") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


SIMPLE = """.HEADER
IDF_FILE 3.0 "Test System" "2024-01-01" 1 "TestBoard" "MM"
.END_HEADER

.BOARD_OUTLINE "OWNER" 1.6
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
IDF_FILE 3.0 "Test System" "2024-01-01" 1 "TestBoard" "MM"
.END_HEADER

.BOARD_OUTLINE "OWNER" 1.6
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
    content = SIMPLE + """
.DRILLED_HOLES
2.0 10 10 "PTH" "VIA" "THRU" "OWNER1"
3.0 25 15 "NPTH" "MTG" "THRU" "OWNER2"
.END_DRILLED_HOLES
"""
    imported = import_idf(str(_write(tmp_path, content)))
    assert len(imported.holes) == 2
    assert len([g for g in imported.board_cutouts if isinstance(g, CircleGeometry)]) == 2
    assert imported.holes[0].plating == HolePlating.PLATED
    assert imported.holes[1].plating == HolePlating.UNPLATED


def test_idf_hole_policy_component_deduplicates(tmp_path):
    content = SIMPLE + """
.DRILLED_HOLES
2.0 10 10 "PTH" "VIA" "THRU" "OWNER1"
2.0 20 10 "PTH" "VIA" "THRU" "OWNER1"
.END_DRILLED_HOLES
"""
    imported = import_idf(str(_write(tmp_path, content)), hole_policy=HolePolicy.COMPONENT)
    assert len(imported.mechanical_components) == 1
    assert len(imported.mechanical_components[0].placements) == 2


def test_idf_notes_placement_keepouts_and_unknown_sections_reported(tmp_path):
    content = SIMPLE + """
.NOTES
15 25 1.5 12 "KEEP OUT AREA"
.END_NOTES

.PLACEMENT
"0603" "R0603_10K" "R1" 20 10 0 0 "TOP" "PLACED"
.END_PLACEMENT

.ROUTE_KEEPOUT "OWNER" "TOP"
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
    imported = import_idf(str(_write(tmp_path, content)))
    assert len(imported.annotations) == 1
    assert len(imported.placements) == 1
    assert any(region.role == "route_keepout" for region in imported.regions)
    assert any(obj.source_name == ".BEND" for obj in imported.unclassified)


def test_idf_arc_start_boundary_does_not_construct_jitx_arc(tmp_path):
    content = """.HEADER
IDF_FILE 3.0 "Test" "2024-01-01" 1 "Test" "MM"
.END_HEADER

.BOARD_OUTLINE "OWNER" 1.6
0 0 0 0
0 10 0 0
0 10 10 90
0 0 10 0
0 0 0 0
.END_BOARD_OUTLINE
"""
    imported = import_idf(str(_write(tmp_path, content)))
    assert isinstance(imported.board_outline, ClosedPath)
