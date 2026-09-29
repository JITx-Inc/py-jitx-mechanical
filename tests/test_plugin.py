import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest
from jitx.plugin import Export

from jitxlib.mechanical.plugin import DxfExport


def test_installed_plugin_entry_point():
    distribution = importlib.metadata.distribution("jitxlib-mechanical")
    entry = next(e for e in distribution.entry_points if e.group == "jitx-plugin")
    entry.load()
    assert isinstance(Export.get("dxf"), DxfExport)


@pytest.mark.parametrize(
    "args,expected",
    [
        (["jitx", "design", "export", "--help"], "dxf"),
        (["jitx", "design", "export", "dxf", "--help"], "--no-drill"),
        (["jitxlib.mechanical", "--help"], "inspect"),
        (["jitxlib.mechanical", "import", "--help"], "--hole-policy"),
    ],
)
def test_installed_cli_help(args, expected, tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", *args], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert expected in result.stdout


def test_preflight_rejects_existing_output(tmp_path):
    path = tmp_path / "board.dxf"
    path.write_text("untouched")
    with pytest.raises(FileExistsError, match="overwrite"):
        DxfExport().preflight(output=path)
    assert path.read_text() == "untouched"
    DxfExport().preflight(output=path, overwrite=True)


def test_installed_import_commands(tmp_path):
    import importlib.util

    import ezdxf

    doc = ezdxf.new()
    doc.units = ezdxf.units.MM
    doc.modelspace().add_circle((0, 0), 10, dxfattribs={"layer": "outline"})
    doc.modelspace().add_circle((2, 3), 1, dxfattribs={"layer": "drill"})
    source = tmp_path / "board.dxf"
    output = tmp_path / "board.py"
    doc.saveas(source)
    for arguments in (
        ["inspect", str(source)],
        ["import", str(source), "--hole-policy", "component", "--output", str(output)],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "jitxlib.mechanical", *arguments],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    for filename, name in [
        (output, "ImportedBoard"),
        (tmp_path / "board_components.py", "MechanicalComponentsCircuit"),
    ]:
        spec = importlib.util.spec_from_file_location(filename.stem, filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[filename.stem] = module
        spec.loader.exec_module(module)
        assert hasattr(module, name)
    assert (tmp_path / "board.report.md").exists()
    executable = Path(sys.executable).with_name("jitx-mechanical")
    result = subprocess.run(
        [str(executable), "--help"], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "inspect" in result.stdout


def test_submitted_checks_default_output_before_capture(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from jitx.plugin.export import ExportError

    monkeypatch.chdir(tmp_path)
    output = tmp_path / "project.Board.dxf"
    output.write_bytes(b"original")
    with pytest.raises(ExportError, match="overwrite"):
        DxfExport().submitted(SimpleNamespace(name="project.Board"))
    assert output.read_bytes() == b"original"
    DxfExport().submitted(SimpleNamespace(name="project.Board"), overwrite=True)
