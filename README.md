# jitxlib-mechanical

Import mechanical board drawings into JITX Python and export captured designs to
DXF. Requires Python 3.12+ and JITX 4.6.

Support is provided on a best-effort basis. Pin a package version for reproducible
builds.

## Install

From this checkout:

```bash
pip install --extra-index-url https://pypi.jitx.com/jitx/main/+simple .
```

## Import

```bash
jitx-mechanical inspect board.dxf
jitx-mechanical import board.dxf --output imported_board.py
jitx-mechanical import board.emn --hole-policy component --output imported_board.py
```

Imports write a Board subclass and a Markdown report. `--report report.json`
selects JSON. DXF and IDF 2.0/3.0 text are supported; `.emn`, `.idf`, `.idx`, and
`.bdf` select the IDF text reader. XML IDX interchange is not supported.

DXF import reads XY geometry from closed polylines, circles, connected line/arc
loops, supported hatch boundaries, and text. Block inserts, splines, dimensions,
open polylines, and unclosed line/arc chains are reported without conversion.

The Board includes its cutouts, route/via keepouts, and drawing annotations.
Other regions become custom drawing features, not enforced placement or routing
constraints. Component placements and unsupported source objects remain in the
report. Select the Board in your own Design and supply your substrate and circuit.

The default hole policy creates unplated cutouts. `--hole-policy component`
creates connectable single-pin components for plated or unknown holes in a
companion `<output_stem>_components.py` module; known NPTH holes remain cutouts.
Instantiate `MechanicalComponentsCircuit` from that module and wire its component
ports as appropriate. Generated pads assume a 0.4 mm copper margin around holes.

Coordinates are converted to millimeters and recentered together. Use
`--no-recenter` to preserve the source origin. DXF imports honor declared units;
unspecified units are inferred with a report warning. `--unit` overrides them.
`--layer-map LAYER=ROLE ...` overrides layer classification; `--class-name` and
`--precision` control generated code. Run `jitx-mechanical import --help` for options.

```python
from jitxlib.mechanical import generate_board_module, import_dxf, write_import_report

imported = import_dxf("board.dxf")
code = generate_board_module(imported, class_name="ImportedBoard")
write_import_report(imported, "board.report.md")
```

## Export

Installing the package registers the `dxf` plugin:

```bash
jitx design export dxf myproject.MyDesign --output board.dxf
jitx design export dxf myproject.MyDesign --output outline.dxf --layers BoardOutline --layers Drill
```

The JITX CLI submits and captures the design before export. A running JITX runtime
is required. Output defaults to `<design-name>.dxf`; use `--overwrite` to replace
an existing file.

Switches `--no-board-outline`, `--no-components`, `--no-pads`, `--no-drill`,
`--no-vias`, `--no-copper`, and `--no-annotations` independently exclude geometry.
Component markers, pad copper, via copper, and drills are separate categories.
Outer pad layers are `Pads_Top`/`Pads_Bottom`; inner pads, vias, and copper use
stackup layer names. Drawing features use their feature name and board side.

DXF output uses millimeters and contour geometry, including interior polygon
boundaries. It is a mechanical drawing, not a fabrication packet. Nonuniform
scaling, shear, and backdrill depth export are unsupported and raise errors.

Python callers can pass an already captured `RuntimeDesign` to
`export_dxf(design, output, *, config=None, overwrite=False)`. The result is the
written `Path`; `DxfExportConfig` controls layers, categories, and layer colors.

## Migration and development

Uninstall the old distribution with `pip uninstall jitx-mechanical` before
installing this package; both distributions provide the `jitx-mechanical` command.
Replace `jitx_mechanical` imports with `jitxlib.mechanical`. The former
`export-dxf` command and XML APIs are removed. Generated cutouts are now attached
to the Board; remove any manual attachment of the old `BOARD_CUTOUTS` list.
The import command remains `jitx-mechanical`; `python -m jitxlib.mechanical` is
also supported.

```bash
pip install --extra-index-url https://pypi.jitx.com/jitx/main/+simple -e '.[dev]' 'jitx==4.6.0' 'jitxcore==4.6.0'
pytest -q
ruff check src tests
ruff format --check src tests
python -m pyright --pythonpath "$(command -v python)"
python -m build
python -m twine check dist/*
```

The normal suite translates designs without launching JITX. The runtime capture
test is opt-in: set `JITX_MECHANICAL_RUNTIME_URI` to a running 4.6 runtime's
websocket URI and run `pytest tests/test_runtime_capture.py -q`. `jitx runtime start
--bg` prints the URI. The [4.6 validation record](docs/validation-4.6.md) lists
the tested versions and the remaining release gates.

## Release

Pushing a `vX.Y.Z` tag runs `.github/workflows/release.yaml`. It checks
formatting, types, and tests on Python 3.12, 3.13, and 3.14 with the
organization's `pybuild-action`. It then publishes the wheel and sdist built on
Python 3.12 to `https://pypi.jitx.com/jitx/main` and creates a GitHub release.
The package version comes from the tag. A manual run of the workflow publishes
only when started from a tag; nothing else publishes.

The workflow uses the organization secrets `GITHUBAPP_CLIENT_ID`,
`GITHUBAPP_PRIVATE_KEY`, and `HATCH_INDEX_AUTH` and the variable
`HATCH_INDEX_USER`. An organization owner must grant this repository access to
them before the first release. The workflow's first job names any it cannot read.

