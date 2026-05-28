"""Integration tests for real-world EMN/IDF files.

Parametrized over the fixtures in tests/fixtures/emn/. Skipped at module
level if the directory is empty so the suite still runs in stripped-down
checkouts.
"""

from __future__ import annotations

import time
from collections import Counter
from pathlib import Path

import pytest

from jitx_mechanical.importers.idf import import_idf
from jitx_mechanical.models import CircleGeometry, ClosedPath, MechanicalImport

REAL_EMN_DIR = Path(__file__).parent / "fixtures" / "emn"
ALL_EMN_FILES = sorted(REAL_EMN_DIR.glob("*.emn")) if REAL_EMN_DIR.exists() else []

pytestmark = pytest.mark.skipif(
    not ALL_EMN_FILES,
    reason="Real EMN fixtures not available in tests/fixtures/emn/",
)


def _emn_id(path: Path) -> str:
    return path.name


def _region_counts(imported: MechanicalImport) -> Counter[str]:
    return Counter(region.role for region in imported.regions)


class TestParseNoCrash:
    """import_idf returns a MechanicalImport for every fixture."""

    @pytest.mark.parametrize("emn_file", ALL_EMN_FILES, ids=_emn_id)
    def test_parse_no_crash(self, emn_file: Path) -> None:
        result = import_idf(str(emn_file))
        assert isinstance(result, MechanicalImport)


class TestBoardOutlineValid:
    """Every fixture yields a usable board outline."""

    @pytest.mark.parametrize("emn_file", ALL_EMN_FILES, ids=_emn_id)
    def test_board_outline_valid(self, emn_file: Path) -> None:
        result = import_idf(str(emn_file))
        outline = result.board_outline
        assert outline is not None, f"{emn_file.name} has no board outline"
        assert isinstance(outline, (ClosedPath, CircleGeometry))
        if isinstance(outline, ClosedPath):
            assert len(outline.segments) >= 3
        else:
            assert outline.radius > 0


class TestLargeFilePerformance:
    """Large EMN files must parse well under the budget."""

    @pytest.mark.parametrize(
        "filename",
        ["353A814.emn", "360a409-1.emn"],
        ids=lambda x: x,
    )
    def test_large_file_performance(self, filename: str) -> None:
        emn_file = REAL_EMN_DIR / filename
        start = time.monotonic()
        result = import_idf(str(emn_file))
        elapsed = time.monotonic() - start
        assert isinstance(result, MechanicalImport)
        assert elapsed < 5.0, f"Parsing {filename} took {elapsed:.2f}s (limit: 5s)"


class TestSectionCounts:
    """Spot-checks against known section counts for specific fixtures."""

    def test_353A814_counts(self) -> None:
        r = import_idf(str(REAL_EMN_DIR / "353A814.emn"))
        regions = _region_counts(r)
        assert r.source_units == "THOU"
        assert len(r.holes) == 49
        assert len(r.placements) == 897
        assert regions["route_keepout"] == 2521
        assert regions["via_keepout"] == 126
        assert regions["place_keepout"] == 32
        assert regions["place_outline"] == 1

    def test_360a409_counts(self) -> None:
        r = import_idf(str(REAL_EMN_DIR / "360a409-1.emn"))
        regions = _region_counts(r)
        assert len(r.holes) == 99
        assert len(r.board_cutouts) == 147
        assert regions["place_keepout"] == 365
        assert regions["place_outline"] == 12

    def test_352a900_counts(self) -> None:
        r = import_idf(str(REAL_EMN_DIR / "352a900-1.emn"))
        regions = _region_counts(r)
        assert len(r.holes) == 66
        assert len(r.placements) == 5
        assert len(r.annotations) == 7
        assert regions["place_keepout"] == 6

    def test_squarecut_has_cutout(self) -> None:
        r = import_idf(str(REAL_EMN_DIR / "squarecut.emn"))
        assert isinstance(r.board_outline, ClosedPath)
        assert len(r.board_cutouts) == 1
        assert r.source_units == "MM"

    def test_f16_idf_v2(self) -> None:
        """IDF 2.0 file in THOU units with three NPTH holes."""
        r = import_idf(str(REAL_EMN_DIR / "f16_amcii_hio_rev1.emn"))
        assert r.source_units == "THOU"
        assert r.unit_scale == pytest.approx(0.0254)
        assert isinstance(r.board_outline, ClosedPath)
        assert len(r.holes) == 3
