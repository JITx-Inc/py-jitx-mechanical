import importlib.util
import sys
from pathlib import Path

import ezdxf
import pytest
from jitx import Circuit, Component, Cutout, Pad
from jitx.feature import Custom
from jitx.landpattern import PadMapping
from jitx.sample import SampleDesign

from jitxlib.mechanical import (
    export_dxf,
    generate_board_module,
    generate_components_module,
    import_idf,
)
from jitxlib.mechanical.codegen import sanitize_identifier
from jitxlib.mechanical.models import (
    CircleGeometry,
    MechanicalAnnotation,
    MechanicalImport,
    MechanicalRegion,
    Point,
)

FIXTURES = Path(__file__).parent / "fixtures" / "emn"


def load_code(path, code):
    path.write_text(code, encoding="utf-8")
    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename", ["squarecut.emn", "352a900-1.emn", "353A814.emn"])
def test_generated_boards_translate_with_attached_features(tmp_path, design_context, filename):
    imported = import_idf(str(FIXTURES / filename))
    module = load_code(tmp_path / "generated_board.py", generate_board_module(imported))
    design_cls = type(
        "ImportedDesign", (SampleDesign,), {"board": module.ImportedBoard(), "circuit": Circuit()}
    )
    with design_context(design_cls) as (design, package):
        assert package.v1.boards
        assert len(list(design.query(Cutout))) == len(imported.board_cutouts)
        assert len(list(design.query(Custom))) == sum(
            r.role not in {"route_keepout", "via_keepout"} for r in imported.regions
        ) + len(imported.annotations)
        if filename == "squarecut.emn":
            output = export_dxf(design, tmp_path / "square.dxf")
            cutout = next(e for e in ezdxf.readfile(output).modelspace() if e.dxf.layer == "Drill")
            assert cutout.closed
            assert list(cutout.get_points("xy")) == pytest.approx(
                [(-10, -10), (10, -10), (10, 10), (-10, 10)]
            )


@pytest.mark.parametrize("recenter", [False, True])
def test_mixed_holes_remain_connected_and_placed(tmp_path, design_context, recenter):
    source = tmp_path / "mixed.emn"
    source.write_text(""".HEADER
IDF_FILE 3.0 "Test" "2026-01-01" 1 "Board" "MM"
.END_HEADER
.BOARD_OUTLINE "ECAD" 1.6
0 0 0 0
0 20 0 0
0 20 20 0
0 0 20 0
0 0 0 0
.END_BOARD_OUTLINE
.DRILLED_HOLES
2 4 5 PTH BOARD MTG ECAD
2 6 7 PTH BOARD MTG ECAD
3 10 10 NPTH BOARD MTG ECAD
2 12 13 UNKNOWN BOARD MTG ECAD
.END_DRILLED_HOLES
""")
    imported = import_idf(str(source), hole_policy="component")
    assert [hole.imported_as for hole in imported.holes] == [
        "component",
        "component",
        "cutout",
        "component",
    ]
    assert len(imported.mechanical_components) == 2
    assert "as a component" in imported.messages[0].text
    board = load_code(
        tmp_path / "mixed_board.py", generate_board_module(imported, recenter=recenter)
    )
    components = load_code(
        tmp_path / "mixed_components.py", generate_components_module(imported, recenter=recenter)
    )
    design_cls = type(
        "MixedDesign",
        (SampleDesign,),
        {"board": board.ImportedBoard(), "circuit": components.MechanicalComponentsCircuit()},
    )
    with design_context(design_cls) as (design, _):
        assert len(list(design.query(Component))) == 3
        assert len(list(design.query(Pad))) == 3
        mappings = list(design.query(PadMapping))
        assert len(mappings) == 3
        assert all(len(list(mapping.items())) == 1 for _, mapping in mappings)
        assert all(pad.transform.translation == (0, 0) for _, pad in design.query(Pad))
        output = export_dxf(design, tmp_path / "mixed.dxf")
    circles = [e for e in ezdxf.readfile(output).modelspace() if e.dxf.layer == "Drill"]
    offset = 10 if recenter else 0
    expected = [
        (4 - offset, 5 - offset),
        (6 - offset, 7 - offset),
        (10 - offset, 10 - offset),
        (12 - offset, 13 - offset),
    ]
    actual = sorted(tuple(e.dxf.center)[:2] for e in circles)
    assert actual == pytest.approx(sorted(expected))
    assert sorted(e.dxf.radius for e in circles) == [1, 1, 1, 1.5]


def test_generated_custom_region_and_rotated_text(tmp_path, design_context):
    imported = MechanicalImport(
        'bad"""file\\name.dxf', "dxf", board_outline=CircleGeometry(radius=10)
    )
    imported.regions.append(MechanicalRegion("keepout", CircleGeometry(radius=2)))
    imported.annotations.append(MechanicalAnnotation("text", "a\"'\\\n\x00z", Point(3, 4), 1, 35))
    imported.warn("bad\"'\n\x00message")
    module = load_code(
        tmp_path / "quoted_board.py", generate_board_module(imported, class_name="class")
    )
    design_cls = type(
        "QuotedDesign", (SampleDesign,), {"board": module.class_(), "circuit": Circuit()}
    )
    with design_context(design_cls) as (design, _):
        features = [feature for _, feature in design.query(Custom)]
        note = next(f for f in features if f.name == "Note")
        assert note.shape.geometry.string == imported.annotations[0].text
        assert note.shape.transform.trs[1] == 35
    assert module.IMPORT_MESSAGES[0]["message"] == imported.messages[0].text
    assert sanitize_identifier("123 board") == "_123_board"


def test_all_unplated_fixture_has_no_components():
    imported = import_idf(str(FIXTURES / "352a900-1.emn"), hole_policy="component")
    assert not imported.mechanical_components
    assert generate_components_module(imported) == ""
    assert len(imported.holes) == 66
    assert all(h.imported_as == "cutout" for h in imported.holes)


def test_region_identifiers_do_not_collide(tmp_path, design_context):
    imported = MechanicalImport("board", "dxf", board_outline=CircleGeometry(radius=10))
    imported.regions = [
        MechanicalRegion("a-b", CircleGeometry(radius=1)),
        MechanicalRegion("a_b", CircleGeometry(radius=2)),
    ]
    module = load_code(tmp_path / "regions.py", generate_board_module(imported))
    design_cls = type(
        "RegionsDesign", (SampleDesign,), {"board": module.ImportedBoard(), "circuit": Circuit()}
    )
    with design_context(design_cls) as (design, _):
        assert len(list(design.query(Custom))) == 2


@pytest.mark.parametrize("recenter", [True, False])
def test_keepouts_annotations_and_cutouts_share_origin(tmp_path, design_context, recenter):
    from jitx import KeepOut

    imported = MechanicalImport(
        "offset.dxf", "dxf", board_outline=CircleGeometry(Point(10, 20), 10)
    )
    imported.board_cutouts.append(CircleGeometry(Point(14, 25), 1))
    imported.annotations.append(MechanicalAnnotation("text", "rotated", Point(13, 24), 1, 35))
    imported.regions = [
        MechanicalRegion("route_keepout", CircleGeometry(Point(16, 27), 1), layers="BOTTOM"),
        MechanicalRegion("via_keepout", CircleGeometry(Point(17, 28), 1), layers="TOP"),
    ]
    module = load_code(tmp_path / "offset.py", generate_board_module(imported, recenter=recenter))
    design_cls = type(
        "OffsetDesign", (SampleDesign,), {"board": module.ImportedBoard(), "circuit": Circuit()}
    )
    dx, dy = (10, 20) if recenter else (0, 0)
    with design_context(design_cls) as (design, _):
        keepouts = [k for _, k in design.query(KeepOut)]
        assert len(keepouts) == 2
        route = next(k for k in keepouts if k.route)
        via = next(k for k in keepouts if k.via)
        assert route.layers.ranges == [(-1, -1)]
        assert via.layers.ranges == [(0, 0)]
        path = export_dxf(design, tmp_path / "offset.dxf")
    model = ezdxf.readfile(path).modelspace()
    for layer, expected in [
        ("BoardOutline", (10 - dx, 20 - dy)),
        ("Drill", (14 - dx, 25 - dy)),
        ("KeepOut_Bottom", (16 - dx, 27 - dy)),
        ("KeepOut_Top", (17 - dx, 28 - dy)),
    ]:
        entity = next(e for e in model if e.dxf.layer == layer)
        assert tuple(entity.dxf.center)[:2] == pytest.approx(expected)
    note = next(e for e in model if e.dxftype() == "TEXT")
    assert tuple(note.dxf.align_point)[:2] == pytest.approx((13 - dx, 24 - dy))
    assert note.dxf.rotation == pytest.approx(35)
