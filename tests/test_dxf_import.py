from __future__ import annotations

import math
from pathlib import Path

import ezdxf
import pytest

from jitxlib.mechanical.importers.dxf import import_dxf, read_dxf
from jitxlib.mechanical.models import CircleGeometry, HolePolicy, Point

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
    msp.add_lwpolyline(
        [(0, 0), (20, 0), (20, 20), (0, 20)], close=True, dxfattribs={"layer": "OUTLINE"}
    )
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


def test_declared_units_are_not_replaced_by_size_heuristic(tmp_path):
    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    doc.modelspace().add_lwpolyline(
        [(0, 0), (6000, 0), (6000, 10), (0, 10)], close=True, dxfattribs={"layer": "OUTLINE"}
    )
    path = tmp_path / "large.dxf"
    doc.saveas(path)
    imported = import_dxf(str(path))
    assert imported.unit_scale == 1
    assert imported.source_units == "mm"
    assert not imported.messages


def test_unitless_drawing_reports_inference(tmp_path):
    doc = ezdxf.new()
    doc.units = 0
    doc.modelspace().add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 500), (0, 500)], close=True, dxfattribs={"layer": "OUTLINE"}
    )
    path = tmp_path / "unitless.dxf"
    doc.saveas(path)
    imported = import_dxf(str(path))
    assert imported.unit_scale == 0.0254
    assert any("units are unspecified" in m.text for m in imported.messages)
    forced = import_dxf(str(path), unit="mm")
    assert forced.unit_scale == 1
    assert not forced.messages


def test_mil_unit_code_and_component_warning(tmp_path):
    doc = ezdxf.new()
    doc.units = 9
    doc.modelspace().add_lwpolyline(
        [(0, 0), (1000, 0), (1000, 500), (0, 500)], close=True, dxfattribs={"layer": "OUTLINE"}
    )
    doc.modelspace().add_circle((200, 200), radius=20, dxfattribs={"layer": "DRILL"})
    path = tmp_path / "mils.dxf"
    doc.saveas(path)
    imported = import_dxf(str(path), hole_policy="component")
    assert imported.source_units == "mil"
    assert imported.unit_scale == 0.0254
    assert imported.holes[0].imported_as == "component"
    assert "as a component" in imported.messages[0].text


def test_round_board_and_inner_contour_on_outline_layer(tmp_path):
    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    doc.modelspace().add_circle((10, 20), 10, dxfattribs={"layer": "BoardOutline"})
    doc.modelspace().add_circle((10, 20), 2, dxfattribs={"layer": "BoardOutline"})
    path = tmp_path / "round.dxf"
    doc.saveas(path)
    imported = import_dxf(str(path))
    assert imported.board_outline.radius == 10
    assert len(imported.board_cutouts) == 1
    assert imported.board_cutouts[0].radius == 2


@pytest.mark.parametrize("clockwise", [False, True])
def test_hatch_wrapped_arc_direction(tmp_path, clockwise):
    from jitxlib.mechanical.models import ArcPathSegment

    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    model = doc.modelspace()
    model.add_lwpolyline(
        [(-5, -5), (5, -5), (5, 5), (-5, 5)], close=True, dxfattribs={"layer": "outline"}
    )
    hatch = model.add_hatch()
    edge = hatch.paths.add_edge_path()
    arc = edge.add_arc((0, 0), 2, 350, 20, ccw=not clockwise)
    edge.add_line(arc.real_end_point, arc.real_start_point)
    path = tmp_path / "hatch.dxf"
    doc.saveas(path)
    imported = import_dxf(str(path))
    assert len(imported.regions) == 1
    assert imported.regions[0].role == "hatch_solid"
    arc = next(s for s in imported.regions[0].geometry.segments if isinstance(s, ArcPathSegment))
    # Assembly may reverse the whole contour to follow its first line segment.
    assert abs(arc.end_angle - arc.start_angle) == pytest.approx(30)


def test_full_circle_hatch(tmp_path):
    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    hatch = doc.modelspace().add_hatch()
    hatch.paths.add_edge_path().add_arc((0, 0), 2, 0, 360)
    path = tmp_path / "circle-hatch.dxf"
    doc.saveas(path)
    imported = import_dxf(str(path))
    assert len(imported.regions) == 1
    from jitxlib.mechanical.geometry import geometry_area

    assert geometry_area(imported.regions[0].geometry) == pytest.approx(4 * math.pi)


def test_layer_roles_and_mtext_direction(tmp_path):
    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    model = doc.modelspace()
    model.add_circle((0, 0), 10, dxfattribs={"layer": "BoardOutline"})
    model.add_circle((0, 0), 1, dxfattribs={"layer": "route_keepout"})
    model.add_circle((3, 3), 1, dxfattribs={"layer": "interior_profile"})
    model.add_mtext("rotated", dxfattribs={"text_direction": (0, 1, 0)})
    source = tmp_path / "roles.dxf"
    doc.saveas(source)
    imported = import_dxf(str(source))
    assert [r.role for r in imported.regions] == ["keepout"]
    assert len(imported.board_cutouts) == 1
    assert imported.board_cutouts[0].center == Point(3, 3)
    assert imported.annotations[0].rotation == 90


def test_annotation_circle_does_not_become_a_hole(tmp_path):
    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    doc.modelspace().add_circle((0, 0), 10, dxfattribs={"layer": "BoardOutline"})
    doc.modelspace().add_circle((0, 0), 1, dxfattribs={"layer": "annotation"})
    source = tmp_path / "annotation-circle.dxf"
    doc.saveas(source)
    imported = import_dxf(str(source))
    assert not imported.board_cutouts
    assert not imported.holes
    assert len(imported.regions) == 1
    assert imported.regions[0].role == "annotation_region"


def test_unclosed_line_arc_chain_is_reported(tmp_path):
    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    model = doc.modelspace()
    model.add_circle((0, 0), 10, dxfattribs={"layer": "BoardOutline"})
    model.add_line((0, 0), (1, 0), dxfattribs={"layer": "detail"})
    model.add_arc((1, 1), 1, 270, 360, dxfattribs={"layer": "detail"})
    source = tmp_path / "unclosed.dxf"
    doc.saveas(source)
    imported = import_dxf(str(source))
    assert len(imported.unclassified) == 1
    assert imported.unclassified[0].source_name == "detail"
    assert imported.unclassified[0].count == 2
    assert not imported.board_cutouts
