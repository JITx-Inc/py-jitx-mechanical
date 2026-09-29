"""Read mechanical geometry from a captured RuntimeDesign."""

from __future__ import annotations

import re

from jitx import Component, Copper, Cutout, KeepOut, Pad, Placement, Side, Via
from jitx.feature import Custom, SurfaceFeature
from jitx.landpattern import PadShape
from jitx.proxy import typeof
from jitx.run import RuntimeDesign
from jitx.shapes import Shape
from jitx.shapes.primitive import Circle
from jitx.transform import IDENTITY, Transform
from jitx.via import ViaDiameter

from .geometry import shape_geometry
from .model import Category, DxfExportConfig, Entity, Marker, Text


def _layer_token(value: str) -> str:
    return re.sub(r'[<>/\\":;?*|=\x00-\x1f]', "_", value) or "Unnamed"


def _placed(parent: Transform | None, own: Transform | None, description: str) -> Transform:
    if parent is None or own is None:
        raise ValueError(
            f"Cannot export unplaced {description}; place it before capturing the design"
        )
    return parent * own


def _side(transform: Transform) -> Side:
    return transform.side if isinstance(transform, Placement) else Side.Top


def extract_design(design: RuntimeDesign, config: DxfExportConfig) -> list[Entity]:
    """Extract placed outlines, physical holes, copper, and drawing features."""
    entities: list[Entity] = []
    conductors = list(design.root.substrate.stackup.conductors)
    count = len(conductors)
    if not count:
        raise ValueError("Cannot export a design without conductor layers")
    layer_names = [
        _layer_token(conductor.name or f"L{i}") for i, conductor in enumerate(conductors)
    ]
    if len(set(name.casefold() for name in layer_names)) != count:
        layer_names = [f"L{i}_{name}" for i, name in enumerate(layer_names)]

    def layer_name(prefix: str, index: int) -> str:
        index = count + index if index < 0 else index
        if not 0 <= index < count:
            raise ValueError(f"Conductor layer {index} is outside the {count}-layer stackup")
        # Retain the established outer-pad names; inner pads carry the stackup name.
        name = "Top" if index == 0 else "Bottom" if index == count - 1 else layer_names[index]
        return f"{prefix}_{name if prefix == 'Pads' else layer_names[index]}"

    def selected_layers(category: Category, prefix: str) -> bool:
        return any(config.includes(category, layer_name(prefix, i)) for i in range(count))

    drill_selected = config.includes("drill", "Drill")
    vias_selected = selected_layers("vias", "Vias")

    def add(
        shape: Shape,
        transform: Transform | None,
        layer: str,
        category: Category,
        *,
        board: bool = False,
    ) -> None:
        if not config.includes(category, layer) and not (
            board and config.includes("drill", "Drill")
        ):
            return
        if transform is None:
            raise ValueError(f"Cannot export unplaced geometry on {layer}")
        for geometry, hole in shape_geometry(shape, transform):
            target, group = ("Drill", "drill") if board and hole else (layer, category)
            if config.includes(group, target):
                entities.append(Entity(target, group, geometry))

    add(design.root.board.shape, IDENTITY, "BoardOutline", "board_outline", board=True)

    if config.includes("components", "Components"):
        for trace, component in design.query(Component):
            transform = _placed(trace.transform, component.transform, f"component {trace.path}")
            position = transform * (0.0, 0.0)
            entities.append(Entity("Components", "components", Marker(position)))
            if component.reference_designator:
                entities.append(
                    Entity(
                        "Components",
                        "components",
                        Text(position, component.reference_designator, 1.0),
                    )
                )

    if selected_layers("pads", "Pads"):
        for trace, pad in design.query(Pad):
            transform = _placed(trace.transform, pad.transform, f"pad {trace.path}")
            for index, shape in PadShape.stack(pad, count):
                shape = shape.shape if isinstance(shape, PadShape) else shape
                add(shape, transform, layer_name("Pads", _side(transform).apply(index)), "pads")

    if drill_selected:
        # Pad and board cutouts are traversed once; via drill geometry is handled below.
        for trace, cutout in design.query(Cutout, opaque=(Via,)):
            add(cutout.shape, trace.transform, "Drill", "drill")

    if vias_selected or drill_selected:
        for trace, via in design.query(Via):
            transform = _placed(trace.transform, via.transform, f"via {trace.path}")
            if vias_selected:
                for index, diameter in ViaDiameter.stack(via, count):
                    if float(diameter) > 0:
                        add(
                            Circle(diameter=float(diameter)),
                            transform,
                            layer_name("Vias", index),
                            "vias",
                        )
            if drill_selected:
                add(Circle(diameter=via.hole_diameter), transform, "Drill", "drill")
                if via.backdrill is not None:
                    raise ValueError(
                        "DXF export does not yet represent backdrill depths; exclude drills to export other geometry"
                    )

    if selected_layers("copper", "Copper"):
        for trace, copper in design.query(Copper, opaque=Via | Pad):
            if trace.transform is None:
                raise ValueError(f"Cannot export unplaced copper at {trace.path}")
            add(
                copper.shape,
                trace.transform,
                layer_name("Copper", _side(trace.transform).apply(copper.layer)),
                "copper",
            )

    if config.include_annotations:
        for trace, feature in design.query(SurfaceFeature):
            if trace.transform is None:
                raise ValueError(f"Cannot export unplaced feature at {trace.path}")
            side = _side(trace.transform) * feature.side
            name = (
                feature.name
                if isinstance(feature, Custom) and feature.name
                else typeof(feature).__name__
            )
            add(
                feature.shape,
                trace.transform,
                f"{_layer_token(name)}_{side.name}",
                "annotations",
            )
        for trace, keepout in design.query(KeepOut):
            indexes: set[int] = set()
            for start, stop in keepout.layers.ranges:
                start = count + start if start < 0 else start
                stop = count + stop if stop < 0 else stop
                if not 0 <= start <= stop < count:
                    raise ValueError(f"Invalid keepout layer range {start}..{stop}")
                indexes.update(range(start, stop + 1))
            for index in sorted(indexes):
                side = _side(trace.transform) if trace.transform is not None else Side.Top
                add(
                    keepout.shape,
                    trace.transform,
                    layer_name("KeepOut", side.apply(index)),
                    "annotations",
                )
    return entities
