# jitx-mechanical

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
uv run --with pytest --with ruff pytest -q
uv run --with ruff ruff check src tests
uv run --with build --with twine python3 -m build
uv run --with twine twine check dist/*
```
