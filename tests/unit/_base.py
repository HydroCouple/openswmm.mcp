"""Pure-``unittest`` helpers shared across the converted unit-test suite.

These replace the pytest fixtures previously provided by ``conftest.py``
(``session_manager``, ``inp_path``, ``fake_ctx``) and the per-module
``pytest.importorskip("openswmm.engine")`` guard, so the unit tests run under
``python -m unittest`` without pytest.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

# Mirror the old ``pytest.importorskip("openswmm.engine")``: when the compiled
# engine is not importable, the engine-dependent test cases skip cleanly
# instead of erroring at import time.
try:
    import openswmm.engine  # noqa: F401

    HAS_ENGINE = True
except Exception:  # pragma: no cover - depends on environment
    HAS_ENGINE = False

# The reference network used by every unit test (12 nodes, 11 conduits,
# 7 subcatchments, 1 rain gage, 1 pollutant; 30 h simulation, 5 s routing step).
_REFERENCE_INP = (Path(__file__).parent / "data" / "site_drainage_model.inp").resolve()


class ModelRef:
    """Constants describing ``site_drainage_model.inp``."""

    NODE_COUNT = 12
    LINK_COUNT = 11
    SUBCATCH_COUNT = 7
    GAGE_COUNT = 1
    POLLUTANT_COUNT = 1

    FIRST_NODE_ID = "J1"
    OUTFALL_ID = "O1"
    FIRST_LINK_ID = "C1"
    FIRST_SUBCATCH_ID = "S1"
    GAGE_ID = "RainGage"
    POLLUTANT_ID = "TSS"

    EXPECTED_DURATION_DAYS = 1.25
    EXPECTED_ROUTING_STEP_SECS = 5.0


class MockContext:
    """Minimal stand-in for :class:`fastmcp.Context` used by tool unit tests.

    Tools read ``ctx.lifespan_context["session_manager"]`` and (for long-running
    tools) call ``await ctx.report_progress(done, total)``.
    """

    def __init__(self, session_manager) -> None:
        self.lifespan_context = {"session_manager": session_manager}
        self.progress_calls: list[tuple[float, float]] = []

    async def report_progress(self, current, total):  # noqa: ARG002
        self.progress_calls.append((current, total))


@unittest.skipUnless(HAS_ENGINE, "openswmm.engine not installed")
class EngineToolTestCase(unittest.IsolatedAsyncioTestCase):
    """Base async test case providing the per-test engine fixtures.

    Set up fresh for each test:
        self.session_manager : a ``SessionManager`` rooted in a temp dir.
        self.inp_path        : path to a per-test copy of the reference INP.
        self.ctx             : a :class:`MockContext` wired to the manager.
    """

    async def asyncSetUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)

        self.inp_path = str(tmp / "site_drainage_model.inp")
        shutil.copy(_REFERENCE_INP, self.inp_path)

        from openswmm_mcp.session import SessionManager

        self.session_manager = SessionManager(max_sessions=10, working_dir=str(tmp))
        self.addAsyncCleanup(self.session_manager.cleanup_all)
        self.ctx = MockContext(self.session_manager)
