"""Import the example files printed in the IDF specifications.

The fixtures in fixtures/idf are the Board and Panel File examples from the IDF
3.0 specification (Revision 1, October 31, 1996) and the Board File example from
the IDF 2.0 specification (Revision 3, January 5, 1993). Both specifications
state that the format is non-proprietary and may be used for any purpose.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from jitxlib.mechanical.importers.idf import import_idf
from jitxlib.mechanical.models import CircleGeometry, ClosedPath, HolePlating

FIXTURES = Path(__file__).parent / "fixtures" / "idf"
THOU = 0.0254


def _import(name: str):
    return import_idf(str(FIXTURES / name))


def test_idf30_spec_board_example():
    imported = _import("idf30_spec_board.emn")

    assert (imported.source_units, imported.unit_scale) == ("THOU", THOU)
    assert isinstance(imported.board_outline, ClosedPath)
    assert len(imported.board_cutouts) == 1 + len(imported.holes)
    assert Counter((h.plating, h.hole_type, h.owner) for h in imported.holes) == {
        (HolePlating.PLATED, "PIN", "ECAD"): 76,
        (HolePlating.PLATED, "VIA", "ECAD"): 8,
        (HolePlating.UNPLATED, "MTG", "ECAD"): 4,
        (HolePlating.PLATED, "MTG", "UNOWNED"): 2,
        (HolePlating.UNPLATED, "TOOL", "MCAD"): 1,
    }
    assert [(r.role, r.layers, r.owner, type(r.geometry)) for r in imported.regions] == [
        ("route_outline", "ALL", "ECAD", ClosedPath),
        ("place_outline", "TOP", "MCAD", ClosedPath),
        ("place_outline", "BOTTOM", "UNOWNED", ClosedPath),
        ("route_keepout", "ALL", "ECAD", CircleGeometry),
        ("place_keepout", "BOTH", "MCAD", CircleGeometry),
        ("place_keepout", "TOP", "MCAD", ClosedPath),
    ]
    assert [r.thickness for r in imported.regions] == pytest.approx(
        [0, 1000 * THOU, 200 * THOU, 0, 0, 300 * THOU]
    )

    placements = {p.refdes: p for p in imported.placements}
    assert list(placements) == ["C1", "C2", "C3", "C4", "C5", "J1", "J2", "U1", "U2", "U3", "U4"]
    u3 = placements["U3"]
    assert (u3.pose.x, u3.pose.y, u3.pose.angle) == pytest.approx((3000 * THOU, 3300 * THOU, 14))
    assert [(p.side, p.status) for p in (placements["C1"], placements["J1"], placements["U1"])] == [
        ("TOP", "PLACED"),
        ("TOP", "MCAD"),
        ("BOTTOM", "ECAD"),
    ]

    assert [a.text for a in imported.annotations] == [
        "This component rotated 14 degrees",
        "Component height limited by enclosure latch",
        "Do not move connectors!",
    ]
    note = imported.annotations[2]
    assert (note.position.x, note.position.y, note.height) == pytest.approx(
        (1800 * THOU, 300 * THOU, 75 * THOU)
    )
    assert not imported.messages
    assert not imported.unclassified


def test_idf30_spec_panel_example():
    imported = _import("idf30_spec_panel.emn")

    assert isinstance(imported.board_outline, ClosedPath)
    assert [(h.plating, h.hole_type, h.owner) for h in imported.holes] == [
        (HolePlating.UNPLATED, "TOOL", "MCAD")
    ] * 3
    assert [(r.role, r.layers, r.owner, r.thickness) for r in imported.regions] == [
        ("place_keepout", "BOTTOM", "MCAD", 0),
        ("place_keepout", "BOTTOM", "MCAD", 0),
    ]
    assert [(p.refdes, p.side, p.status) for p in imported.placements] == [
        ("BOARD", "TOP", "MCAD"),
        ("BOARD", "BOTTOM", "MCAD"),
    ]
    assert not imported.messages
