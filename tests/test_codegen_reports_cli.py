from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from jitx_mechanical.codegen import generate_board_module
from jitx_mechanical.importers.dxf import import_dxf
from jitx_mechanical.importers.idf import import_idf
from jitx_mechanical.reports import import_to_markdown, write_import_report

FIXTURES = Path(__file__).parent / "fixtures" / "dxf"
EMN_FIXTURES = Path(__file__).parent / "fixtures" / "emn"


def test_codegen_board_only_no_design_or_circuit():
    imported = import_dxf(str(FIXTURES / "outline_slots.dxf"))
    code = generate_board_module(imported, class_name="OutlineBoard")
    ast.parse(code)
    assert "class OutlineBoard(Board):" in code
    assert "class OutlineCircuit" not in code
    assert "class OutlineDesign" not in code
    assert "BOARD_CUTOUTS" in code


def test_report_includes_messages_and_regions(tmp_path):
    imported = import_dxf(
        str(FIXTURES / "flex_outline.dxf"),
        layer_map={"OUTER_PROFILES": "outline", "INTERIOR_PROFILES": "hole"},
    )
    text = import_to_markdown(imported)
    assert "Mechanical Import Report" in text
    assert "unknown plating" in text
    report = tmp_path / "report.md"
    write_import_report(imported, str(report))
    assert report.exists()


def test_json_report(tmp_path):
    imported = import_dxf(str(FIXTURES / "outline_r12.dxf"))
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


def test_cli_inspect_emn(tmp_path):
    """`jitx-mechanical inspect board.emn` prints a summary without error."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "jitx_mechanical.cli",
            "inspect",
            str(EMN_FIXTURES / "board_a.emn"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"CLI failed: stderr={result.stderr}"
    assert "Mechanical import" in result.stdout
    assert "Holes:" in result.stdout
