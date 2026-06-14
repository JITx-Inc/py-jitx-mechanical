# jitx-mechanical

> **Support status — please read.** This code is provided on a best-effort
> basis with **no guarantee of future compatibility**. Newer JITX releases
> are expected to deprecate the functions exposed here as native mechanical
> import/export support lands in the JITX core. Pin to a specific version if
> you need stable behavior, and expect to migrate when the deprecation arrives.

Mechanical import and export helpers for JITX Python projects.

This package imports DXF and EMN/IDF/IDX-compatible mechanical board data into
a shared Python IR, then generates a Board-focused JITX Python module and an
import report. Import does not generate user-facing `Design` or `Circuit`
classes.

## Install

```bash
pip install -e .
pip install -e ".[dev]"
```

## CLI

```bash
# Inspect source mechanical data
jitx-mechanical inspect board.dxf
jitx-mechanical inspect board.emn

# Import and generate a Board-focused module plus report
jitx-mechanical import board.dxf --output imported_board.py --report imported_board.report.md
jitx-mechanical import board.emn --output imported_board.py

# Treat ambiguous/plated circular holes as single-pin mechanical components
jitx-mechanical import board.emn --hole-policy component --output imported_board.py

# Export JITX XML board data to DXF
jitx-mechanical export-dxf board.xml --output board.dxf
jitx-mechanical export-dxf board.xml --output outline.dxf --layers BoardOutline Drill
```

## Python API

```python
from jitx_mechanical import generate_board_module, import_dxf, import_idf, write_import_report

imported = import_dxf("board.dxf")
code = generate_board_module(imported, class_name="ImportedBoard")
write_import_report(imported, "board.report.md")
```

## Import Policy

- Board outline import is the primary target.
- Interior non-circular geometry is imported as board cutout geometry.
- Interior circular geometry is imported as a cutout when plating is unknown.
- Use `--hole-policy component` when circular holes should become deduplicated
  single-pin mechanical component definitions for user wiring.
- Keepouts, placement outlines, bend regions, height regions, notes, placements,
  unknown sections, and unclassified source objects are preserved in the report.

## Development

```bash
python3 -m pip install -e ".[dev]"
python3 -m pytest -q
ruff check src tests
uv run --with build --with twine python3 -m build
uv run --with twine twine check dist/*
```

Generated JITX Python stays decoupled from a required `jitx` package dependency.
To validate generated imports, constructors, and placements against an installed
JITX 4.2.x runtime API surface, run the optional smoke tests in a JITX 4.2.1
environment:

```bash
JITX_MECHANICAL_RUN_JITX_RUNTIME_TESTS=1 python3 -m pytest -q tests/test_jitx_runtime_compat.py -m jitx_runtime
```
