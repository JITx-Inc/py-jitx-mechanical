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
    """Raise if ``output`` cannot be written with the given overwrite permission.

    Args:
        output: Destination file.
        overwrite: Whether an existing file may be replaced.

    Raises:
        IsADirectoryError: If ``output`` is a directory.
        FileExistsError: If ``output`` exists, or is a symbolic link, and
            ``overwrite`` is false.
    """
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
    """Write placed millimeter geometry to a DXF file.

    Missing parent directories are created. The file is written beside the
    destination and then moved into place, so a failed write leaves an existing
    output unchanged.

    Args:
        entities: Geometry to write, each with its DXF layer and category.
        output: Destination file.
        config: Selects entities by layer and category and sets layer colors.
            None includes every entity.
        overwrite: Whether an existing file may be replaced.

    Returns:
        The output path.

    Raises:
        IsADirectoryError: If ``output`` is a directory.
        FileExistsError: If ``output`` exists and ``overwrite`` is false,
            including when another process creates it during the write.
        ValueError: If an entity has unsupported geometry.
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
    """Write a captured design's board geometry to a millimeter DXF file.

    Capture the design with JITX before calling; this function neither submits
    nor captures it. Geometry is extracted before the file is written, so
    unsupported geometry leaves an existing output unchanged.

    Args:
        design: The captured design.
        output: Destination file.
        config: Selects layers and geometry categories and sets layer colors.
            None includes every category.
        overwrite: Whether an existing file may be replaced.

    Returns:
        The output path.

    Raises:
        IsADirectoryError: If ``output`` is a directory.
        FileExistsError: If ``output`` exists and ``overwrite`` is false.
        ValueError: If selected geometry is unplaced or cannot be represented in
            DXF, such as a backdrilled via or a nonuniformly scaled shape.
    """
    from .runtime import extract_design

    validate_output(Path(output), overwrite=overwrite)
    config = config or DxfExportConfig()
    return write_dxf(extract_design(design, config), output, config=config, overwrite=overwrite)
