from __future__ import annotations

from pathlib import Path

import ezdxf

from jitx_mechanical.importers.dxf import import_dxf, read_dxf
from jitx_mechanical.models import CircleGeometry, HolePolicy

FIXTURES = Path(__file__).parent / "fixtures" / "dxf"


def test_read_dxf_inventory():
    inv = read_dxf(str(FIXTURES / "hawk_outline.dxf"))
    assert inv.dxf_version == "AC1009"
    assert inv.entity_counts.get("LINE") == 32
    assert inv.bounding_box is not None


def test_dxf_outline_primary_import():
    imported = import_dxf(str(FIXTURES / "hawk_outline.dxf"))
    assert imported.board_outline is not None
    assert len(imported.board_cutouts) == 0


def test_dxf_interior_non_circular_paths_import_as_cutouts():
    imported = import_dxf(str(FIXTURES / "hawk_outline_screwholes.dxf"))
    assert imported.board_outline is not None
    assert len(imported.board_cutouts) == 4


def test_dxf_interior_circles_default_to_ambiguous_cutouts():
    imported = import_dxf(
        str(FIXTURES / "beeper_flex_outline.dxf"),
        layer_map={"OUTER_PROFILES": "outline", "INTERIOR_PROFILES": "hole"},
    )
    circle_cutouts = [g for g in imported.board_cutouts if isinstance(g, CircleGeometry)]
    assert len(circle_cutouts) == 2
    assert len(imported.holes) == 2
    assert any("unknown plating" in message.text for message in imported.messages)


def test_dxf_hole_policy_component_deduplicates_identical_geometry(tmp_path):
    dxf_path = tmp_path / "holes.dxf"
    doc = ezdxf.new("R2010")
    doc.units = ezdxf.units.MM
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (20, 0), (20, 20), (0, 20)], close=True, dxfattribs={"layer": "OUTLINE"})
    msp.add_circle((5, 5), radius=1.0, dxfattribs={"layer": "DRILL"})
    msp.add_circle((15, 15), radius=1.0, dxfattribs={"layer": "DRILL"})
    doc.saveas(dxf_path)

    imported = import_dxf(
        str(dxf_path),
        layer_map={"OUTLINE": "outline", "DRILL": "hole"},
        hole_policy=HolePolicy.COMPONENT,
    )
    assert len(imported.mechanical_components) == 1
    assert len(imported.mechanical_components[0].placements) == 2
