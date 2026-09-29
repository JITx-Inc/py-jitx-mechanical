"""Live capture gate; run explicitly against a stable JITX runtime."""

import os

import ezdxf
import jitx
import pytest
from jitx import Board, Circuit, Component, Copper, Cutout, Net, Port, Side
from jitx.landpattern import Landpattern, Pad
from jitx.sample import SampleDesign, SampleSubstrate
from jitx.shapes.composites import rectangle
from jitx.shapes.primitive import Circle, Polygon
from jitx.symbol import Pin, Symbol

from jitxlib.mechanical import export_dxf

URI = os.environ.get("JITX_MECHANICAL_RUNTIME_URI")
pytestmark = pytest.mark.skipif(not URI, reason="Set JITX_MECHANICAL_RUNTIME_URI for live capture")


class CaptureBoard(Board):
    shape = rectangle(30, 20)
    mounting_hole = Cutout(Circle(diameter=2).at(5, 5))


class CapturePad(Pad):
    shape = Circle(diameter=2)
    cutout = Cutout(Circle(diameter=1))


class CapturePattern(Landpattern):
    p1 = CapturePad().at(0, 0)


class CaptureSymbol(Symbol):
    p1 = Pin(at=(0, 0))


class CaptureHole(Component):
    reference_designator_prefix = "MH"
    p1 = Port()
    landpattern = CapturePattern()
    symbol = CaptureSymbol()


class CaptureCircuit(Circuit):
    def __init__(self):
        self.mount = CaptureHole().at(-5, 0, on=Side.Bottom)
        self.via = SampleSubstrate.THVia().at(0, 0)
        self.copper = Copper(Polygon([(0, 0), (3, 0), (3, 3), (0, 3)]), layer=0)
        self.gnd = Net([self.mount.p1, self.via, self.copper])


class CaptureDesign(SampleDesign):
    board = CaptureBoard()
    circuit = CaptureCircuit()


def test_live_submit_capture_export(tmp_path):
    with jitx.runtime(uri=URI) as runtime:
        design = runtime.submit(CaptureDesign)
        design.capture()
        output = export_dxf(design, tmp_path / "captured.dxf")
    doc = ezdxf.readfile(output)
    assert not doc.audit().has_errors
    assert len(doc.modelspace().query('LWPOLYLINE[layer=="BoardOutline"]')) == 1
    holes = list(doc.modelspace().query('CIRCLE[layer=="Drill"]'))
    assert len(holes) == 3
    assert sorted(tuple(h.dxf.center)[:2] for h in holes) == [(-5, 0), (0, 0), (5, 5)]
    assert sorted(h.dxf.radius for h in holes) == [0.15, 0.5, 1]
    assert len(doc.modelspace().query('CIRCLE[layer=="Pads_Bottom"]')) == 1
    assert len(doc.modelspace().query('CIRCLE[layer=="Vias_Top"]')) == 1
    copper = list(doc.modelspace().query('LWPOLYLINE[layer=="Copper_Top"]'))
    assert len(copper) == 1
    assert list(copper[0].get_points("xy")) == [(0, 0), (3, 0), (3, 3), (0, 3)]
