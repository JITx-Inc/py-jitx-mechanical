"""Write mechanical DXF drawings from captured JITX designs."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from ezdxf import units
from ezdxf.enums import TextEntityAlignment
from ezdxf.filemanagement import new

from .model import Circle, DxfExportConfig, Entity, Marker, Text
from .model import Path as GeometryPath

if TYPE_CHECKING:
    from jitx.run import RuntimeDesign

_ALIGNMENTS = {
    "NW": TextEntityAlignment.TOP_LEFT,
    "N": TextEntityAlignment.TOP_CENTER,
    "NE": TextEntityAlignment.TOP_RIGHT,
    "W": TextEntityAlignment.MIDDLE_LEFT,
    "C": TextEntityAlignment.MIDDLE_CENTER,
    "E": TextEntityAlignment.MIDDLE_RIGHT,
    "SW": TextEntityAlignment.BOTTOM_LEFT,
    "S": TextEntityAlignment.BOTTOM_CENTER,
    "SE": TextEntityAlignment.BOTTOM_RIGHT,
}


def validate_output(output: Path, *, overwrite: bool) -> None:
    if output.is_dir():
        raise IsADirectoryError(f"DXF output is a directory: {output}")
    if os.path.lexists(output) and not overwrite:
        raise FileExistsError(f"DXF output already exists: {output}; use --overwrite to replace it")


def write_dxf(
    entities: Iterable[Entity],
    output: str | Path,
    *,
    config: DxfExportConfig | None = None,
    overwrite: bool = False,
) -> Path:
    """Write placed millimeter geometry and return the output Path.

    Each entity supplies its DXF layer and geometry category. ``config`` selects
    entities and layer colors; None includes every category. Parent directories
    are created as needed. An existing output raises FileExistsError unless
    ``overwrite`` permits replacement. A failed write leaves existing output intact.
    """
    output = Path(output)
    validate_output(output, overwrite=overwrite)
    config = config or DxfExportConfig()
    doc = new("R2010")
    doc.units = units.MM
    modelspace = doc.modelspace()
    for entity in entities:
        if not config.includes(entity.category, entity.layer):
            continue
        if entity.layer not in doc.layers:
            doc.layers.add(entity.layer, color=config.layer_colors.get(entity.layer, 7))
        attrs = {"layer": entity.layer}
        match entity.geometry:
            case Marker(position):
                modelspace.add_point(position, dxfattribs=attrs)
            case Circle(center, radius):
                modelspace.add_circle(center, radius, dxfattribs=attrs)
            case GeometryPath(vertices, closed, width):
                modelspace.add_lwpolyline(
                    [(x, y, width, width, bulge) for x, y, bulge in vertices],
                    format="xyseb",
                    close=closed,
                    dxfattribs=attrs,
                )
            case Text(position, string, height, rotation, mirrored, anchor):
                text = modelspace.add_text(
                    string, height=height, rotation=rotation, dxfattribs=attrs
                )
                text.set_placement(position, align=_ALIGNMENTS[anchor])
                text.dxf.text_generation_flag = 4 if mirrored else 0
            case _:
                raise ValueError(f"Unsupported DXF geometry: {type(entity.geometry).__name__}")

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding=doc.output_encoding) as stream:
            doc.write(stream)
        if overwrite:
            os.replace(temporary, output)
        else:
            # Linking publishes the complete file and cannot replace an existing path.
            os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def export_dxf(
    design: RuntimeDesign,
    output: str | Path,
    *,
    config: DxfExportConfig | None = None,
    overwrite: bool = False,
) -> Path:
    """Write an already captured RuntimeDesign to DXF and return its output Path.

    Capture the design with JITX before calling; this function does not submit
    or capture it. ``config`` selects layers, categories, and colors; None
    includes every category. ``output`` may be a string or Path. An existing
    destination raises FileExistsError unless ``overwrite`` permits replacement.
    Unsupported selected geometry raises ValueError before the output is replaced.
    """
    from .runtime import extract_design

    validate_output(Path(output), overwrite=overwrite)
    config = config or DxfExportConfig()
    return write_dxf(extract_design(design, config), output, config=config, overwrite=overwrite)
