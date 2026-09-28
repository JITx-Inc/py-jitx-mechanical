from contextlib import contextmanager

import jitx.run
import pytest
from jitx._instantiation import instantiation
from jitx.run import RuntimeDesign
from jitx.run.runtime import _instantiate_design, _package_design

jitx.run.autodetect_design = False


@pytest.fixture
def design_context():
    """Translate a design without claiming a runtime capture."""

    @contextmanager
    def build(cls):
        with instantiation.activate(), instantiation.frame():
            root = _instantiate_design(cls)
            package, mapper = _package_design(root)
            yield RuntimeDesign(root, cls.__name__, mapper), package

    return build
