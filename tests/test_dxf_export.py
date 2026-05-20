from __future__ import annotations

from jitx_mechanical.exporters.dxf import DxfExportConfig, export_dxf


def test_export_dxf_with_layer_filter(tmp_path):
    xml = tmp_path / "board.xml"
    xml.write_text(
        """<ROOT>
<BOARD>
  <BOARD-BOUNDARY><LINE WIDTH="0"><POINT X="0" Y="0"/><POINT X="10" Y="0"/></LINE></BOARD-BOUNDARY>
  <BOARD-BOUNDARY><LINE WIDTH="0"><POINT X="10" Y="0"/><POINT X="10" Y="5"/></LINE></BOARD-BOUNDARY>
  <BOARD-BOUNDARY><LINE WIDTH="0"><POINT X="10" Y="5"/><POINT X="0" Y="5"/></LINE></BOARD-BOUNDARY>
  <BOARD-BOUNDARY><LINE WIDTH="0"><POINT X="0" Y="5"/><POINT X="0" Y="0"/></LINE></BOARD-BOUNDARY>
</BOARD>
</ROOT>
"""
    )
    output = tmp_path / "board.dxf"
    export_dxf(str(xml), str(output), config=DxfExportConfig(layers={"BoardOutline"}))
    assert output.exists()
    assert output.stat().st_size > 0
