from __future__ import annotations

import os
from importlib import metadata

import pytest

from jitx_mechanical.codegen import generate_board_module, generate_components_module
from jitx_mechanical.models import (
    ArcPathSegment,
    CircleGeometry,
    ClosedPath,
    HolePlating,
    LinePathSegment,
    MechanicalAnnotation,
    MechanicalComponent,
    MechanicalImport,
    MechanicalRegion,
    Point,
)

RUN_RUNTIME_TESTS = os.environ.get("JITX_MECHANICAL_RUN_JITX_RUNTIME_TESTS") == "1"

pytestmark = [
    pytest.mark.jitx_runtime,
    pytest.mark.skipif(
        not RUN_RUNTIME_TESTS,
        reason="set JITX_MECHANICAL_RUN_JITX_RUNTIME_TESTS=1 to run JITX runtime tests",
    ),
]


def test_generated_board_module_executes_against_jitx_4_2_runtime():
    _assert_jitx_4_2_runtime()
    code = generate_board_module(_runtime_smoke_import(), class_name="RuntimeSmokeBoard")
    namespace = _exec_generated_module(code, "runtime_smoke_board.py")

    board = namespace["RuntimeSmokeBoard"]()

    assert board.shape is not None
    assert namespace["BOARD_CUTOUTS"]
    assert board.route_keepout_0 is not None
    assert board.place_keepout_0 is not None
    assert board.note_0 is not None


def test_generated_components_module_executes_against_jitx_4_2_runtime():
    _assert_jitx_4_2_runtime()
    code = generate_components_module(_runtime_smoke_import())
    namespace = _exec_generated_module(code, "runtime_smoke_components.py")

    circuit = namespace["MechanicalComponentsCircuit"]()

    assert circuit.instances_2p0mm_plated


def _assert_jitx_4_2_runtime() -> None:
    try:
        import jitx
    except ModuleNotFoundError as exc:
        raise AssertionError(
            "JITX runtime tests require the jitx Python package to be importable"
        ) from exc

    version = _jitx_version(jitx)
    assert version.startswith("4.2."), f"expected JITX 4.2.x runtime, got {version!r}"


def _jitx_version(jitx_module: object) -> str:
    try:
        return metadata.version("jitx")
    except metadata.PackageNotFoundError:
        pass

    version = getattr(jitx_module, "__version__", None)
    if isinstance(version, str) and version:
        return version
    raise AssertionError(
        "could not determine JITX runtime version from package metadata or jitx.__version__"
    )


def _exec_generated_module(code: str, filename: str) -> dict[str, object]:
    namespace: dict[str, object] = {}
    exec(compile(code, filename, "exec"), namespace)
    return namespace


def _runtime_smoke_import() -> MechanicalImport:
    return MechanicalImport(
        source_path="runtime-smoke.emn",
        source_format="idf",
        board_outline=_arc_outline(),
        board_cutouts=[
            CircleGeometry(center=Point(2.0, 2.0), radius=0.5),
        ],
        regions=[
            MechanicalRegion(
                role="route_keepout",
                geometry=_square(3.0, 3.0, 4.0),
                layers="TOP",
            ),
            MechanicalRegion(
                role="place_keepout",
                geometry=_square(-4.0, -4.0, 1.0),
            ),
        ],
        annotations=[
            MechanicalAnnotation(
                role="note",
                text="fab note",
                position=Point(0.0, 0.0),
                height=1.0,
            ),
        ],
        mechanical_components=[
            MechanicalComponent(
                name="MH1",
                geometry=CircleGeometry(center=Point(0.0, 0.0), radius=1.0),
                placements=(Point(1.0, 1.0), Point(3.0, 1.0)),
                plating=HolePlating.PLATED,
            ),
        ],
    )


def _arc_outline() -> ClosedPath:
    return ClosedPath(
        segments=(
            LinePathSegment(start=Point(0.0, 0.0), end=Point(10.0, 0.0)),
            ArcPathSegment(
                center=Point(10.0, 5.0),
                radius=5.0,
                start_angle=270.0,
                end_angle=360.0,
                start_point=Point(10.0, 0.0),
                end_point=Point(15.0, 5.0),
            ),
            LinePathSegment(start=Point(15.0, 5.0), end=Point(0.0, 5.0)),
            LinePathSegment(start=Point(0.0, 5.0), end=Point(0.0, 0.0)),
        )
    )


def _square(x: float, y: float, size: float) -> ClosedPath:
    return ClosedPath(
        segments=(
            LinePathSegment(start=Point(x, y), end=Point(x + size, y)),
            LinePathSegment(start=Point(x + size, y), end=Point(x + size, y + size)),
            LinePathSegment(start=Point(x + size, y + size), end=Point(x, y + size)),
            LinePathSegment(start=Point(x, y + size), end=Point(x, y)),
        )
    )
