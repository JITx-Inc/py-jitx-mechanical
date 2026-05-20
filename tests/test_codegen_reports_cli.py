from __future__ import annotations

import ast
from pathlib import Path

from jitx_mechanical.codegen import generate_board_module
from jitx_mechanical.importers.dxf import import_dxf
from jitx_mechanical.reports import import_to_markdown, write_import_report

FIXTURES = Path(__file__).parent / "fixtures" / "dxf"


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
