import math

import ezdxf
import pytest
from jitx import Board, Circuit, Component, Copper, Cutout, KeepOut, LayerSet, Net, Side
from jitx.feature import Custom, Silkscreen, Soldermask
from jitx.landpattern import Landpattern, Pad
from jitx.net import Port
from jitx.sample import SampleDesign, SampleSubstrate
from jitx.shapes.composites import rectangle
from jitx.shapes.primitive import Arc, ArcPolygon, ArcPolyline, Circle, Polygon, Text
from jitx.symbol import Pin, Symbol

from jitxlib.mechanical import DxfExportConfig, export_dxf
from jitxlib.mechanical.exporters.dxf import write_dxf
from jitxlib.mechanical.exporters.geometry import shape_geometry
from jitxlib.mechanical.exporters.model import Entity


class HolePad(Pad):
    shape = Circle(diameter=2)
    cutout = Cutout(Circle(diameter=1).at(0.2, 0))
    mask = Soldermask(shape)


class HolePattern(Landpattern):
    p1 = HolePad().at(1, 0)
    silk = Silkscreen(Text("PKG", 1).at(2, 0, rotate=30))
    hole = Cutout(Circle(radius=0.4).at(3, 0))


class HoleSymbol(Symbol):
    p1 = Pin(at=(0, 0))


class HoleComponent(Component):
    reference_designator_prefix = "MH"
    p1 = Port()
    landpattern = HolePattern()
    symbol = HoleSymbol()


class ExportBoard(Board):
    shape = Polygon([(0, 0), (30, 0), (30, 30), (0, 30)], holes=[[(2, 2), (3, 2), (3, 3), (2, 3)]])
    hole = Cutout(Circle(radius=0.8).at(5, 6))
    silk = Silkscreen(Circle(radius=1.2).at(7, 8))
    text = Custom(Text("BOARD", 1.5).at(2, 3, rotate=15), name="Note")
    keepout = KeepOut(rectangle(2, 2), LayerSet.all(), route=True)


class ExportCircuit(Circuit):
    def __init__(self):
        self.top = HoleComponent().at(10, 20, rotate=90)
        self.bottom = HoleComponent().at(20, 20, rotate=90, on=Side.Bottom)
        self.via = SampleSubstrate.THVia().at(12, 12)
        self.trace = Copper(ArcPolyline(0.2, [Arc((10, 10), 2, 350, 30)]), layer=0)
        self.fill = Copper(
            Polygon(
                [(15, 1), (25, 1), (25, 8), (15, 8)], holes=[[(18, 3), (19, 3), (19, 4), (18, 4)]]
            ),
            layer=-1,
        )
        self.gnd = Net([self.top.p1, self.bottom.p1, self.via, self.trace, self.fill])


class MechanicalDesign(SampleDesign):
    board = ExportBoard()
    circuit = ExportCircuit()


def _export(tmp_path, design_context, config=None):
    with design_context(MechanicalDesign) as (design, _):
        output = export_dxf(design, tmp_path / "board.dxf", config=config)
    return ezdxf.readfile(output)


def _on(doc, layer):
    return [entity for entity in doc.modelspace() if entity.dxf.layer == layer]


def test_board_and_package_geometry(tmp_path, design_context):
    doc = _export(tmp_path, design_context)
    assert doc.units == ezdxf.units.MM
    assert not doc.audit().has_errors
    assert len(_on(doc, "BoardOutline")) == 1
    assert len(_on(doc, "Drill")) == 7
    centers = [tuple(e.dxf.center)[:2] for e in _on(doc, "Drill") if e.dxftype() == "CIRCLE"]
    for expected in [(5, 6), (10, 21.2), (10, 23), (20, 18.8), (20, 17), (12, 12)]:
        assert any(math.dist(c, expected) < 1e-8 for c in centers)
    assert len(_on(doc, "Pads_Top")) == len(_on(doc, "Pads_Bottom")) == 2
    assert len(_on(doc, "Vias_Top")) == len(_on(doc, "Vias_Bottom")) == 1
    assert len(_on(doc, "KeepOut_Top")) == len(_on(doc, "KeepOut_Bottom")) == 1


def test_text_rotation_mirroring_and_anchor(tmp_path, design_context):
    doc = _export(tmp_path, design_context)
    top = next(e for e in _on(doc, "Silkscreen_Top") if e.dxftype() == "TEXT")
    bottom = next(e for e in _on(doc, "Silkscreen_Bottom") if e.dxftype() == "TEXT")
    assert top.dxf.rotation == pytest.approx(120)
    assert tuple(top.dxf.align_point)[:2] == pytest.approx((10, 22))
    assert bottom.dxf.rotation == pytest.approx(240)
    assert bottom.dxf.text_generation_flag == 4
    note = _on(doc, "Note_Top")[0]
    assert note.dxf.rotation == pytest.approx(15)
    assert tuple(note.dxf.align_point)[:2] == pytest.approx((2, 3))


def test_copper_arcs_and_holes(tmp_path, design_context):
    doc = _export(tmp_path, design_context)
    trace = _on(doc, "Copper_Top")[0]
    assert trace.get_points("xyseb")[0][2:4] == pytest.approx((0.2, 0.2))
    assert trace.get_points("xyb")[0][2] == pytest.approx(math.tan(math.radians(30) / 4))
    assert len(_on(doc, "Copper_Bottom")) == 2
    assert all(e.closed for e in _on(doc, "Copper_Bottom"))


@pytest.mark.parametrize(
    "config,absent,present",
    [
        (DxfExportConfig(include_components=False), "Components", "Pads_Top"),
        (DxfExportConfig(include_vias=False), "Vias_Top", "Drill"),
        (DxfExportConfig(include_annotations=False), "Silkscreen_Top", "Drill"),
        (DxfExportConfig(include_drill=False), "Drill", "Vias_Top"),
        (DxfExportConfig(include_pads=False), "Pads_Top", "Drill"),
        (DxfExportConfig(include_copper=False), "Copper_Top", "Pads_Top"),
        (DxfExportConfig(include_board_outline=False), "BoardOutline", "Drill"),
    ],
)
def test_independent_filters(tmp_path, design_context, config, absent, present):
    doc = _export(tmp_path, design_context, config)
    assert not _on(doc, absent)
    assert _on(doc, present)


def test_layer_selection_and_colors(tmp_path, design_context):
    doc = _export(
        tmp_path,
        design_context,
        DxfExportConfig(layers={"BoardOutline", "Drill"}, layer_colors={"Drill": 3}),
    )
    assert {e.dxf.layer for e in doc.modelspace()} == {"BoardOutline", "Drill"}
    assert doc.layers.get("Drill").color == 3


@pytest.mark.parametrize("sweep", [-270, -90, 90, 270, 360, -360])
def test_signed_arc_readback(tmp_path, sweep):
    shape = ArcPolygon([Arc((0, 0), 2, 350, sweep)])
    geometry = list(shape_geometry(shape))[0][0]
    path = write_dxf([Entity("BoardOutline", "board_outline", geometry)], tmp_path / "arc.dxf")
    polyline = next(iter(ezdxf.readfile(path).modelspace()))
    bulges = [p[2] for p in polyline.get_points("xyb") if p[2]]
    assert sum(math.degrees(4 * math.atan(b)) for b in bulges) == pytest.approx(sweep)


def test_overwrite_and_failed_export_leave_existing_file(tmp_path, design_context):
    path = tmp_path / "board.dxf"
    path.write_text("existing")
    with design_context(MechanicalDesign) as (design, _):
        with pytest.raises(FileExistsError):
            export_dxf(design, path)
        assert path.read_text() == "existing"
        export_dxf(design, path, overwrite=True)
        assert ezdxf.readfile(path).units == ezdxf.units.MM
        with pytest.raises(IsADirectoryError):
            export_dxf(design, tmp_path, overwrite=True)


def test_nonuniform_transform_rejected():
    from jitx.transform import Transform

    with pytest.raises(ValueError, match="uniform"):
        list(shape_geometry(Circle(radius=1), Transform((0, 0), scale=(2, 1))))


def test_mirrored_major_arc_readback(tmp_path):
    from jitx.transform import Transform

    shape = ArcPolyline(0.3, [Arc((0, 0), 2, 10, 270)])
    geometry = list(shape_geometry(shape, Transform((5, 7), scale=(-1, 1))))[0][0]
    path = write_dxf([Entity("Copper_Top", "copper", geometry)], tmp_path / "mirror.dxf")
    vertices = next(iter(ezdxf.readfile(path).modelspace())).get_points("xyb")
    assert vertices[0][:2] == pytest.approx(
        (5 - 2 * math.cos(math.radians(10)), 7 + 2 * math.sin(math.radians(10)))
    )
    assert math.degrees(4 * math.atan(vertices[0][2])) == pytest.approx(-270)


def test_failed_writer_preserves_existing_output(tmp_path):
    path = tmp_path / "existing.dxf"
    path.write_bytes(b"original")

    def broken_entities():
        from jitxlib.mechanical.exporters.model import Circle as DxfCircle

        yield Entity("Drill", "drill", DxfCircle((0, 0), 1))
        raise ValueError("invalid geometry")

    with pytest.raises(ValueError, match="invalid geometry"):
        write_dxf(broken_entities(), path, overwrite=True)
    assert path.read_bytes() == b"original"
    assert list(tmp_path.iterdir()) == [path]


def test_layer_filter_excludes_backdrills(tmp_path, design_context):
    from jitx.via import Backdrill

    class BackdrilledVia(SampleSubstrate.THVia):
        backdrill = Backdrill(0.5, 0.9, 1.0, 0.2)

    class BackdrillCircuit(Circuit):
        via = BackdrilledVia().at(1, 1)
        net = Net([via])

    class BackdrillDesign(SampleDesign):
        board = ExportBoard()
        circuit = BackdrillCircuit()

    with design_context(BackdrillDesign) as (design, _):
        with pytest.raises(ValueError, match="backdrill"):
            export_dxf(design, tmp_path / "all.dxf")
        output = export_dxf(
            design, tmp_path / "outline.dxf", config=DxfExportConfig(layers={"BoardOutline"})
        )
    model = ezdxf.readfile(output).modelspace()
    assert {entity.dxf.layer for entity in model} == {"BoardOutline"}


@pytest.mark.parametrize("layers", [{"BoardOutline"}, set()])
def test_unselected_unplaced_component_features_are_skipped(tmp_path, design_context, layers):
    class UnplacedCircuit(Circuit):
        hole = HoleComponent()
        net = Net([hole.p1])

    class UnplacedDesign(SampleDesign):
        board = ExportBoard()
        circuit = UnplacedCircuit()

    with design_context(UnplacedDesign) as (design, _):
        output = export_dxf(
            design, tmp_path / "selected.dxf", config=DxfExportConfig(layers=layers)
        )
    assert {e.dxf.layer for e in ezdxf.readfile(output).modelspace()} == layers
