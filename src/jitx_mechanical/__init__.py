"""Mechanical import and export helpers for JITX Python projects."""

from .codegen import generate_board_module
from .importers.idf import import_idf
from .models import MechanicalImport
from .reports import write_import_report

__version__ = "0.1.0"

__all__ = [
    "DxfExportConfig",
    "MechanicalImport",
    "export_dxf",
    "generate_board_module",
    "import_dxf",
    "import_idf",
    "read_dxf",
    "write_import_report",
]


def __getattr__(name: str):
    if name in {"DxfExportConfig", "export_dxf"}:
        from .exporters.dxf import DxfExportConfig, export_dxf

        return {"DxfExportConfig": DxfExportConfig, "export_dxf": export_dxf}[name]
    if name in {"import_dxf", "read_dxf"}:
        from .importers.dxf import import_dxf, read_dxf

        return {"import_dxf": import_dxf, "read_dxf": read_dxf}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
