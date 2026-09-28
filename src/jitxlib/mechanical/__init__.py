"""Mechanical import and export helpers for JITX Python projects."""

from .codegen import generate_board_module
from .exporters.dxf import DxfExportConfig, export_dxf
from .importers.dxf import import_dxf, read_dxf
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
