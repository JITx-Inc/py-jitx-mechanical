"""Command-line interface for jitx-mechanical."""

from __future__ import annotations

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

from .codegen import generate_board_module, generate_components_module
from .importers.dxf import import_dxf, read_dxf
from .importers.idf import import_idf
from .models import HolePolicy, MechanicalImport
from .reports import write_import_report


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jitx-mechanical",
        description="Inspect mechanical drawings and generate JITX Board code.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {version('jitxlib-mechanical')}"
    )
    subparsers = parser.add_subparsers(dest="command")

    p_inspect = subparsers.add_parser("inspect", help="Inspect a mechanical source file.")
    p_inspect.add_argument("input", help="Input DXF, EMN, IDF, IDX, or BDF file.")
    p_inspect.add_argument("--format", choices=["auto", "dxf", "emn", "idf", "idx"], default="auto")
    p_inspect.set_defaults(func=_cmd_inspect)

    p_import = subparsers.add_parser(
        "import", help="Import mechanical data and generate Board code."
    )
    p_import.add_argument("input", help="Input DXF, EMN, IDF, IDX, or BDF file.")
    p_import.add_argument("--format", choices=["auto", "dxf", "emn", "idf", "idx"], default="auto")
    p_import.add_argument("-o", "--output", help="Output Python module path.")
    p_import.add_argument("--report", help="Output report path (.md or .json).")
    p_import.add_argument(
        "--class-name", default="ImportedBoard", help="Generated Board class name."
    )
    p_import.add_argument("--unit", choices=["mm", "in", "mil", "cm", "m"], help="Force DXF units.")
    p_import.add_argument(
        "--layer-map", nargs="*", metavar="LAYER=ROLE", help="Map DXF layers to roles."
    )
    p_import.add_argument(
        "--hole-policy",
        choices=[policy.value for policy in HolePolicy],
        default=HolePolicy.CUTOUT.value,
        help="How to import ambiguous/plated circular holes.",
    )
    p_import.add_argument("--no-recenter", action="store_true", help="Keep source coordinates.")
    p_import.add_argument("--precision", type=int, default=4, help="Coordinate precision.")
    p_import.set_defaults(func=_cmd_import)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)
    args.func(args)


def _cmd_inspect(args: argparse.Namespace) -> None:
    input_path = _existing_path(args.input)
    fmt = _resolve_format(input_path, args.format)

    if fmt == "dxf":
        inventory = read_dxf(str(input_path))
        print(f"DXF File: {inventory.filepath}")
        print(f"Version:  {inventory.dxf_version}")
        print(f"Units:    {inventory.units or 'not specified'}")
        if inventory.bounding_box:
            bb = inventory.bounding_box
            print(f"Extent:   {bb[1].x - bb[0].x:.3f} x {bb[1].y - bb[0].y:.3f}")
        print("Layers:")
        for layer, count in sorted(inventory.layers.items()):
            print(f"  {layer:30s} {count:5d}")
        print("Entity types:")
        for etype, count in sorted(inventory.entity_counts.items()):
            print(f"  {etype:20s} {count:5d}")
        return

    imported = import_idf(str(input_path))
    _print_import_summary(imported)


def _cmd_import(args: argparse.Namespace) -> None:
    input_path = _existing_path(args.input)
    fmt = _resolve_format(input_path, args.format)
    output_path = Path(args.output) if args.output else input_path.with_suffix(".py")
    report_path = Path(args.report) if args.report else output_path.with_suffix(".report.md")

    if fmt == "dxf":
        imported = import_dxf(
            str(input_path),
            layer_map=_parse_layer_map(args.layer_map),
            unit=args.unit,
            hole_policy=args.hole_policy,
        )
    else:
        imported = import_idf(str(input_path), hole_policy=args.hole_policy)

    code = generate_board_module(
        imported,
        class_name=args.class_name,
        module_name=input_path.name,
        recenter=not args.no_recenter,
        precision=args.precision,
    )
    output_path.write_text(code, encoding="utf-8")
    write_import_report(imported, str(report_path))

    components_code = generate_components_module(
        imported,
        module_name=input_path.name,
        recenter=not args.no_recenter,
        precision=args.precision,
    )
    components_path: Path | None = None
    if components_code:
        components_path = output_path.with_name(f"{output_path.stem}_components.py")
        components_path.write_text(components_code, encoding="utf-8")

    _print_import_summary(imported, file=sys.stderr)
    print(f"  Python: {output_path}", file=sys.stderr)
    if components_path is not None:
        print(f"  Components: {components_path}", file=sys.stderr)
    print(f"  Report: {report_path}", file=sys.stderr)


def _existing_path(path: str) -> Path:
    input_path = Path(path)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)
    return input_path


def _resolve_format(path: Path, requested: str) -> str:
    if requested != "auto":
        return "idf" if requested in {"emn", "idx"} else requested
    suffix = path.suffix.lower()
    if suffix == ".dxf":
        return "dxf"
    if suffix in {".emn", ".idf", ".idx", ".bdf"}:
        return "idf"
    print(f"Error: Cannot infer format from {path.name}; pass --format", file=sys.stderr)
    sys.exit(1)


def _parse_layer_map(items: list[str] | None) -> dict[str, str] | None:
    if not items:
        return None
    result = {}
    for item in items:
        if "=" not in item:
            print(f"Error: Invalid layer map entry (expected LAYER=ROLE): {item}", file=sys.stderr)
            sys.exit(1)
        key, value = item.split("=", 1)
        result[key] = value
    return result


def _print_import_summary(imported: MechanicalImport, file=None) -> None:
    out = file or sys.stdout
    print(f"Mechanical import: {imported.source_path}", file=out)
    print(f"  Format:       {imported.source_format}", file=out)
    print(f"  Outline:      {'found' if imported.board_outline else 'not found'}", file=out)
    print(f"  Cutouts:      {len(imported.board_cutouts)}", file=out)
    print(f"  Holes:        {len(imported.holes)}", file=out)
    print(f"  Regions:      {len(imported.regions)}", file=out)
    print(f"  Annotations:  {len(imported.annotations)}", file=out)
    print(f"  Placements:   {len(imported.placements)}", file=out)
    print(f"  Messages:     {len(imported.messages)}", file=out)


if __name__ == "__main__":
    main()
