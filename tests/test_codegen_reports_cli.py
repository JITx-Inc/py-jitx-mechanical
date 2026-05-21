from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from jitx_mechanical.codegen import generate_board_module, generate_components_module
from jitx_mechanical.importers.dxf import import_dxf
from jitx_mechanical.importers.idf import import_idf
from jitx_mechanical.models import HolePolicy
from jitx_mechanical.reports import import_to_markdown, write_import_report

FIXTURES = Path(__file__).parent / "fixtures" / "dxf"
EMN_FIXTURES = Path(__file__).parent / "fixtures" / "emn"


def test_codegen_board_only_no_design_or_circuit():
    imported = import_dxf(str(FIXTURES / "hawk_outline_screwholes.dxf"))
    code = generate_board_module(imported, class_name="HawkBoard")
    ast.parse(code)
    assert "class HawkBoard(Board):" in code
    assert "class HawkCircuit" not in code
    assert "class HawkDesign" not in code
    assert "BOARD_CUTOUTS" in code


def test_report_includes_messages_and_regions(tmp_path):
    imported = import_dxf(
        str(FIXTURES / "beeper_flex_outline.dxf"),
        layer_map={"OUTER_PROFILES": "outline", "INTERIOR_PROFILES": "hole"},
    )
    text = import_to_markdown(imported)
    assert "Mechanical Import Report" in text
    assert "unknown plating" in text
    report = tmp_path / "report.md"
    write_import_report(imported, str(report))
    assert report.exists()


def test_json_report(tmp_path):
    imported = import_dxf(str(FIXTURES / "hawk_outline.dxf"))
    report = tmp_path / "report.json"
    write_import_report(imported, str(report))
    assert '"source_format": "dxf"' in report.read_text()


def test_codegen_from_emn_fixture():
    """Codegen on a real EMN file produces a syntactically valid Board module."""
    imported = import_idf(str(EMN_FIXTURES / "squarecut.emn"))
    code = generate_board_module(imported, class_name="SquareCutBoard")
    ast.parse(code)
    assert "class SquareCutBoard(Board):" in code
    assert "BOARD_CUTOUTS" in code
    assert "from jitx.board import Board" in code


def test_cli_import_emn_end_to_end(tmp_path):
    """`jitx-mechanical import board.emn` produces a valid Python module and report."""
    output = tmp_path / "board.py"
    report = tmp_path / "board.report.md"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jitx_mechanical.cli",
            "import",
            str(EMN_FIXTURES / "squarecut.emn"),
            "-o",
            str(output),
            "--report",
            str(report),
            "--class-name",
            "SquareCutBoard",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"CLI failed: stderr={result.stderr}"
    assert output.exists()
    assert report.exists()
    code = output.read_text()
    ast.parse(code)
    assert "class SquareCutBoard(Board):" in code
    assert "from jitx.board import Board" in code


def test_components_module_empty_when_policy_cutout():
    """Default (cutout) policy yields no mechanical components, so codegen returns ''."""
    imported = import_idf(str(EMN_FIXTURES / "352a900-1.emn"))
    assert imported.mechanical_components == []
    assert generate_components_module(imported) == ""


def test_components_module_emits_real_component_classes():
    """--hole-policy component produces Component classes with through-hole pads."""
    imported = import_idf(
        str(EMN_FIXTURES / "352a900-1.emn"), hole_policy=HolePolicy.COMPONENT
    )
    assert imported.mechanical_components, "expected mechanical components in this fixture"

    code = generate_components_module(imported)
    ast.parse(code)

    # Real JITX imports, not metadata dicts.
    assert "from jitx.component import Component" in code
    assert "from jitx.landpattern import Landpattern, Pad" in code
    assert "from jitx.feature import Cutout, Soldermask" in code
    assert "from jitx.symbol import Pin, Symbol" in code
    assert "from jitx.net import Port" in code

    # Single-pin Component + through-hole pad.
    assert "class _SinglePinSymbol(Symbol):" in code
    assert "class MechanicalHole_" in code
    assert "(Component):" in code
    assert 'reference_designator_prefix = "MH"' in code
    assert "p1 = Port()" in code
    assert "self.cutout = Cutout(Circle(diameter=" in code
    assert "self.soldermask = Soldermask(" in code

    # Circuit instantiating placements.
    assert "class MechanicalComponentsCircuit(Circuit):" in code
    assert ".at(" in code

    # Old metadata-dict format must not leak into the new module.
    assert '"hole_radius":' not in code
    assert '"plating":' not in code


def test_board_module_points_to_companion_when_components_present():
    """When components are detected the Board file references the companion module."""
    imported = import_idf(
        str(EMN_FIXTURES / "352a900-1.emn"), hole_policy=HolePolicy.COMPONENT
    )
    code = generate_board_module(imported, class_name="MyBoard")
    ast.parse(code)
    assert "_components.py" in code
    assert "MechanicalComponentsCircuit" in code
    # No metadata-dict block when components were emitted as classes.
    assert "MECHANICAL_COMPONENTS = [" not in code


def test_cli_emits_components_file_with_component_policy(tmp_path):
    """End-to-end: CLI writes a syntactically valid `_components.py` companion."""
    output = tmp_path / "m900_board.py"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jitx_mechanical.cli",
            "import",
            str(EMN_FIXTURES / "352a900-1.emn"),
            "-o",
            str(output),
            "--class-name",
            "M900Board",
            "--hole-policy",
            "component",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"CLI failed: stderr={result.stderr}"
    assert output.exists()
    components = output.with_name("m900_board_components.py")
    assert components.exists(), "companion components file was not written"
    assert f"Components: {components}" in result.stderr

    code = components.read_text()
    ast.parse(code)
    assert "class MechanicalComponentsCircuit(Circuit):" in code


def test_cli_skips_components_file_with_cutout_policy(tmp_path):
    """Default cutout policy: no companion file is created."""
    output = tmp_path / "board.py"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jitx_mechanical.cli",
            "import",
            str(EMN_FIXTURES / "352a900-1.emn"),
            "-o",
            str(output),
            "--class-name",
            "M900Board",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"CLI failed: stderr={result.stderr}"
    companion = output.with_name("board_components.py")
    assert not companion.exists()
    assert "Components:" not in result.stderr


def test_cli_inspect_emn(tmp_path):
    """`jitx-mechanical inspect board.emn` prints a summary without error."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jitx_mechanical.cli",
            "inspect",
            str(EMN_FIXTURES / "352a900-1.emn"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"CLI failed: stderr={result.stderr}"
    assert "Mechanical import" in result.stdout
    assert "Holes:" in result.stdout
