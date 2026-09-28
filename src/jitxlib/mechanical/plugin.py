"""JITX plugin for mechanical DXF export."""

import re
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING, override

import jitx.log
from jitx.plugin import Export
from jitx.plugin.export import ExportError

if TYPE_CHECKING:
    from jitx.run import RuntimeDesign

logger = getLogger(__name__)


def _output_path(design: "RuntimeDesign", output: Path | None) -> Path:
    if output is not None:
        return output
    stem = re.sub(r"[^\w.-]", "_", design.name).strip(".") or "design"
    return Path.cwd() / f"{stem}.dxf"


@Export.register("dxf")
class DxfExport(Export):
    """Export board geometry to a millimeter DXF drawing."""

    @override
    def preflight(self, *, output: Path | None = None, overwrite: bool = False, **kwargs) -> None:
        from .exporters.dxf import validate_output

        if output is not None:
            validate_output(output, overwrite=overwrite)

    @override
    def submitted(
        self,
        design: "RuntimeDesign",
        *,
        output: Path | None = None,
        overwrite: bool = False,
        **kwargs,
    ) -> None:
        from .exporters.dxf import validate_output

        # The default filename becomes known at submission, before capture.
        try:
            validate_output(_output_path(design, output), overwrite=overwrite)
        except OSError as error:
            raise ExportError(str(error)) from error

    @override
    def export(
        self,
        design: "RuntimeDesign",
        *,
        output: Path | None = None,
        overwrite: bool = False,
        layers: list[str] | None = None,
        board_outline: bool = True,
        components: bool = True,
        pads: bool = True,
        drill: bool = True,
        vias: bool = True,
        copper: bool = True,
        annotations: bool = True,
    ) -> None:
        from .exporters.dxf import DxfExportConfig, export_dxf

        result = export_dxf(
            design,
            _output_path(design, output),
            overwrite=overwrite,
            config=DxfExportConfig(
                layers=set(layers) if layers is not None else None,
                include_board_outline=board_outline,
                include_components=components,
                include_pads=pads,
                include_drill=drill,
                include_vias=vias,
                include_copper=copper,
                include_annotations=annotations,
            ),
        )
        jitx.log.notice(logger, "Wrote DXF drawing %s", result)
