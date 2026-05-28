from __future__ import annotations

import ezdxf

from jitx_mechanical.exporters.dxf import DxfExportConfig, export_dxf


def _export_xml(tmp_path, xml_text: str, config: DxfExportConfig | None = None):
    xml = tmp_path / "board.xml"
    output = tmp_path / "board.dxf"
    xml.write_text(xml_text)
    export_dxf(str(xml), str(output), config=config)
    return ezdxf.readfile(output)


def _circle_entities(doc):
    return [entity for entity in doc.modelspace() if entity.dxftype() == "CIRCLE"]


def _entities_on(doc, layer: str):
    return [entity for entity in doc.modelspace() if entity.dxf.layer == layer]


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


def test_board_context_hole_circle_exports_on_drill_layer(tmp_path):
    doc = _export_xml(
        tmp_path,
        """<ROOT>
<BOARD>
  <SHAPE>
    <LAYER-SPECIFIER NAME="HOLE" SIDE="Top"/>
    <CIRCLE RADIUS="0.8"><POINT X="5" Y="6"/></CIRCLE>
  </SHAPE>
</BOARD>
</ROOT>
""",
    )

    circles = _circle_entities(doc)
    assert len(circles) == 1
    assert circles[0].dxf.layer == "Drill"
    assert tuple(round(v, 6) for v in circles[0].dxf.center) == (5.0, 6.0, 0.0)
    assert round(circles[0].dxf.radius, 6) == 0.8
    assert not any(entity.dxf.layer.startswith("HOLE") for entity in doc.modelspace())


def test_board_context_drill_survives_no_annotations(tmp_path):
    doc = _export_xml(
        tmp_path,
        """<ROOT>
<BOARD>
  <SHAPE>
    <LAYER-SPECIFIER NAME="HOLE" SIDE="Top"/>
    <CIRCLE RADIUS="0.8"><POINT X="5" Y="6"/></CIRCLE>
  </SHAPE>
  <SHAPE>
    <LAYER-SPECIFIER NAME="SILKSCREEN" SIDE="Top"/>
    <CIRCLE RADIUS="1.2"><POINT X="7" Y="8"/></CIRCLE>
  </SHAPE>
</BOARD>
</ROOT>
""",
        config=DxfExportConfig(include_annotations=False),
    )

    circles = _circle_entities(doc)
    assert len(circles) == 1
    assert circles[0].dxf.layer == "Drill"


def test_board_context_drill_respects_no_drill(tmp_path):
    doc = _export_xml(
        tmp_path,
        """<ROOT>
<BOARD>
  <SHAPE>
    <LAYER-SPECIFIER NAME="HOLE" SIDE="Top"/>
    <CIRCLE RADIUS="0.8"><POINT X="5" Y="6"/></CIRCLE>
  </SHAPE>
</BOARD>
</ROOT>
""",
        config=DxfExportConfig(include_drill=False),
    )

    assert _circle_entities(doc) == []
    assert _entities_on(doc, "Drill") == []


def test_board_context_common_shapes_export(tmp_path):
    doc = _export_xml(
        tmp_path,
        """<ROOT>
<BOARD>
  <SHAPE>
    <LAYER-SPECIFIER NAME="SILKSCREEN" SIDE="Top"/>
    <CIRCLE RADIUS="1.2"><POINT X="7" Y="8"/></CIRCLE>
  </SHAPE>
  <SHAPE>
    <LAYER-SPECIFIER NAME="SILKSCREEN" SIDE="Top"/>
    <ARC X="3" Y="4" RADIUS="2" START_ANGLE="10" END_ANGLE="80"/>
  </SHAPE>
  <SHAPE>
    <LAYER-SPECIFIER NAME="SILKSCREEN" SIDE="Top"/>
    <RECTANGLE WIDTH="4" HEIGHT="2"><POSE X="20" Y="30" ANGLE="0"/></RECTANGLE>
  </SHAPE>
</BOARD>
</ROOT>
""",
    )

    entities = _entities_on(doc, "Silkscreen_Top")
    assert [entity.dxftype() for entity in entities].count("CIRCLE") == 1
    assert [entity.dxftype() for entity in entities].count("ARC") == 1
    assert [entity.dxftype() for entity in entities].count("LWPOLYLINE") == 1

    circle = next(entity for entity in entities if entity.dxftype() == "CIRCLE")
    assert tuple(round(v, 6) for v in circle.dxf.center) == (7.0, 8.0, 0.0)
    arc = next(entity for entity in entities if entity.dxftype() == "ARC")
    assert tuple(round(v, 6) for v in arc.dxf.center) == (3.0, 4.0, 0.0)
    assert round(arc.dxf.start_angle, 6) == 10.0
    assert round(arc.dxf.end_angle, 6) == 80.0


def test_package_common_shapes_transform_through_instance_pose(tmp_path):
    doc = _export_xml(
        tmp_path,
        """<ROOT>
<BOARD>
  <PACKAGE NAME="PKG">
    <SHAPE>
      <LAYER-SPECIFIER NAME="SILKSCREEN" SIDE="Top"/>
      <CIRCLE RADIUS="0.5"><POINT X="1" Y="0"/></CIRCLE>
    </SHAPE>
    <SHAPE>
      <LAYER-SPECIFIER NAME="SILKSCREEN" SIDE="Top"/>
      <ARC X="2" Y="0" RADIUS="1" START_ANGLE="0" END_ANGLE="90"/>
    </SHAPE>
    <SHAPE>
      <LAYER-SPECIFIER NAME="SILKSCREEN" SIDE="Top"/>
      <RECTANGLE WIDTH="2" HEIGHT="1"><POSE X="3" Y="0" ANGLE="0"/></RECTANGLE>
    </SHAPE>
  </PACKAGE>
  <INST DESIGNATOR="U1" PACKAGE="PKG" SIDE="Top">
    <POSE X="10" Y="20" ANGLE="90"/>
  </INST>
</BOARD>
</ROOT>
""",
    )

    entities = _entities_on(doc, "Silkscreen_Top")
    circle = next(entity for entity in entities if entity.dxftype() == "CIRCLE")
    arc = next(entity for entity in entities if entity.dxftype() == "ARC")
    rect = next(entity for entity in entities if entity.dxftype() == "LWPOLYLINE")

    assert tuple(round(v, 6) for v in circle.dxf.center) == (10.0, 21.0, 0.0)
    assert tuple(round(v, 6) for v in arc.dxf.center) == (10.0, 22.0, 0.0)
    assert round(arc.dxf.start_angle, 6) == 90.0
    assert round(arc.dxf.end_angle, 6) == 180.0
    assert len(rect.get_points("xy")) == 4
