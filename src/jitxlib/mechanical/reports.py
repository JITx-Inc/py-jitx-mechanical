"""Import report writers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CircleGeometry, ClosedPath, MechanicalImport


def write_import_report(imported: MechanicalImport, output_path: str) -> None:
    """Write the import's geometry summary and conversion messages as UTF-8 text.

    ``output_path`` selects JSON with a .json suffix and Markdown otherwise.
    Report coordinates come from ``imported`` before code-generation recentering.
    An existing report is replaced; its parent directory must already exist.
    """

    path = Path(output_path)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(import_to_dict(imported), indent=2) + "\n", encoding="utf-8")
    else:
        path.write_text(import_to_markdown(imported), encoding="utf-8")


def import_to_markdown(imported: MechanicalImport) -> str:
    lines = [
        "# Mechanical Import Report",
        "",
        f"- Source: `{imported.source_path}`",
        f"- Format: `{imported.source_format}`",
        f"- Source units: `{imported.source_units or 'unspecified'}`",
        f"- Unit scale to mm: `{imported.unit_scale}`",
        "",
        "## Summary",
        "",
        f"- Board outline: `{_geometry_name(imported.board_outline)}`",
        f"- Board cutouts: `{len(imported.board_cutouts)}`",
        f"- Holes: `{len(imported.holes)}`",
        f"- Mechanical components: `{len(imported.mechanical_components)}`",
        f"- Regions/custom layers: `{len(imported.regions)}`",
        f"- Annotations: `{len(imported.annotations)}`",
        f"- Placements: `{len(imported.placements)}`",
        f"- Unclassified objects: `{len(imported.unclassified)}`",
        "",
    ]

    if imported.messages:
        lines.extend(["## Messages", ""])
        for message in imported.messages:
            suffix = f" Hint: {message.hint}" if message.hint else ""
            source = f" [{message.source}]" if message.source else ""
            lines.append(f"- **{message.severity.value}**{source}: {message.text}{suffix}")
        lines.append("")

    if imported.holes:
        lines.extend(
            [
                "## Holes",
                "",
                "| Radius mm | X mm | Y mm | Plating | Imported As | Source |",
                "|---:|---:|---:|---|---|---|",
            ]
        )
        for hole in imported.holes:
            g = hole.geometry
            lines.append(
                f"| {g.radius:.4f} | {g.center.x:.4f} | {g.center.y:.4f} | "
                f"{hole.plating.value} | {hole.imported_as} | {g.source_section or g.source_layer} |"
            )
        lines.append("")

    if imported.regions:
        lines.extend(
            [
                "## Regions",
                "",
                "| Role | Geometry | Source | Layers | Owner |",
                "|---|---|---|---|---|",
            ]
        )
        for region in imported.regions:
            lines.append(
                f"| {region.role} | {_geometry_name(region.geometry)} | "
                f"{region.source_name} | {region.layers} | {region.owner} |"
            )
        lines.append("")

    if imported.annotations:
        lines.extend(
            [
                "## Annotations",
                "",
                "| Role | Text | X mm | Y mm | Source |",
                "|---|---|---:|---:|---|",
            ]
        )
        for annotation in imported.annotations:
            lines.append(
                f"| {annotation.role} | {_escape_md(annotation.text)} | "
                f"{annotation.position.x:.4f} | {annotation.position.y:.4f} | {annotation.source_name} |"
            )
        lines.append("")

    if imported.placements:
        lines.extend(
            [
                "## Placements",
                "",
                "| Refdes | Package | X mm | Y mm | Side | Status |",
                "|---|---|---:|---:|---|---|",
            ]
        )
        for placement in imported.placements:
            lines.append(
                f"| {placement.refdes} | {placement.package} | {placement.pose.x:.4f} | "
                f"{placement.pose.y:.4f} | {placement.side} | {placement.status} |"
            )
        lines.append("")

    if imported.unclassified:
        lines.extend(
            ["## Unclassified", "", "| Role | Source | Detail | Count |", "|---|---|---|---:|"]
        )
        for obj in imported.unclassified:
            lines.append(f"| {obj.role} | {obj.source_name} | {obj.detail} | {obj.count} |")
        lines.append("")

    return "\n".join(lines)


def import_to_dict(imported: MechanicalImport) -> dict[str, Any]:
    return {
        "source_path": imported.source_path,
        "source_format": imported.source_format,
        "source_units": imported.source_units,
        "unit_scale": imported.unit_scale,
        "board_outline": _geometry_to_dict(imported.board_outline),
        "board_cutouts": [_geometry_to_dict(g) for g in imported.board_cutouts],
        "holes": [
            {
                "geometry": _geometry_to_dict(h.geometry),
                "plating": h.plating.value,
                "associated_with": h.associated_with,
                "type": h.hole_type,
                "owner": h.owner,
                "imported_as": h.imported_as,
            }
            for h in imported.holes
        ],
        "regions": [
            {
                "role": r.role,
                "geometry": _geometry_to_dict(r.geometry),
                "source_name": r.source_name,
                "layers": r.layers,
                "owner": r.owner,
                "thickness": r.thickness,
            }
            for r in imported.regions
        ],
        "annotations": [
            {
                "role": a.role,
                "text": a.text,
                "position": {"x": a.position.x, "y": a.position.y},
                "height": a.height,
                "rotation": a.rotation,
                "source_name": a.source_name,
            }
            for a in imported.annotations
        ],
        "placements": [
            {
                "refdes": p.refdes,
                "package": p.package,
                "part_number": p.part_number,
                "pose": {"x": p.pose.x, "y": p.pose.y, "angle": p.pose.angle},
                "side": p.side,
                "status": p.status,
            }
            for p in imported.placements
        ],
        "messages": [
            {
                "severity": m.severity.value,
                "text": m.text,
                "source": m.source,
                "hint": m.hint,
            }
            for m in imported.messages
        ],
        "unclassified": [
            {
                "role": o.role,
                "source_name": o.source_name,
                "detail": o.detail,
                "count": o.count,
            }
            for o in imported.unclassified
        ],
    }


def _geometry_name(geometry) -> str:
    if geometry is None:
        return "none"
    if isinstance(geometry, CircleGeometry):
        return "Circle"
    if isinstance(geometry, ClosedPath):
        return f"ClosedPath({len(geometry.segments)} segments)"
    return type(geometry).__name__


def _geometry_to_dict(geometry) -> dict[str, Any] | None:
    if geometry is None:
        return None
    if isinstance(geometry, CircleGeometry):
        return {
            "type": "circle",
            "center": {"x": geometry.center.x, "y": geometry.center.y},
            "radius": geometry.radius,
            "source_layer": geometry.source_layer,
            "source_section": geometry.source_section,
        }
    if isinstance(geometry, ClosedPath):
        return {
            "type": "closed_path",
            "segments": len(geometry.segments),
            "source_layer": geometry.source_layer,
            "source_section": geometry.source_section,
        }
    return {"type": type(geometry).__name__}


def _escape_md(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
