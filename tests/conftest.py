"""Shared test fixtures for the openswmm-mcp test suite.

Tests run against the **real** ``openswmm.engine`` (and ``openswmm.legacy.engine``)
loaded from the bundled ``site_drainage_example.inp`` fixture.  The compiled
engine is a hard dependency: imports happen at collection time and a missing
engine raises ``ImportError`` rather than skipping.

Fixtures
--------
``inp_path`` (function-scoped):
    Path to a per-test copy of ``tests/data/site_drainage_example.inp``
    placed inside ``tmp_path``.  Tests get an isolated copy so the engine
    can write its ``.rpt`` / ``.out`` files alongside without colliding
    with sibling tests.

``session_manager`` (function-scoped):
    A :class:`~openswmm_mcp.session.SessionManager` whose working directory
    is the pytest-managed ``tmp_path``.

``engine`` (parametrized over ``["openswmm", "legacy"]``):
    Drives backend-aware tests through both backends in one run.  Tests
    that use this fixture pick up two test instances per call.

``open_session`` (factory):
    ``await open_session(session_manager, inp_path, engine="openswmm")``
    creates a session, opens / initializes the solver, and returns it.
    Mirrors what ``lifecycle.open_model`` does in production.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

# Hard-import the engines at collection time so any environment that lacks the
# compiled extension fails fast instead of silently falling back to mocks.
import openswmm.engine  # noqa: F401
import openswmm.legacy.engine  # noqa: F401


# ---------------------------------------------------------------------------
# Reference INP and expected counts
# ---------------------------------------------------------------------------

_REFERENCE_INP = (
    Path(__file__).parent / "data" / "site_drainage_example.inp"
).resolve()

# Sanity-check that the reference INP travelled with the repo.  If this raises
# at collection time, the user knows immediately that the fixture is missing.
assert _REFERENCE_INP.exists(), (
    f"Reference INP not found at {_REFERENCE_INP}; the fixture file must be "
    f"committed to the repository."
)


class ReferenceModel:
    """Constants describing the reference ``site_drainage_example.inp``.

    Tests assert against these instead of hard-coded numbers so that any
    future change to the fixture cascades through one place.
    """

    NODE_COUNT = 12
    LINK_COUNT = 11
    SUBCATCH_COUNT = 7
    GAGE_COUNT = 1
    POLLUTANT_COUNT = 0

    # First objects (alphabetical by name in the .inp file)
    FIRST_NODE_ID = "J1"
    FIRST_LINK_ID = "C1"
    FIRST_SUBCATCH_ID = "S1"
    FIRST_GAGE_ID = "RainGage"
    OUTFALL_NODE_ID = "O1"

    # Simulation runs from 00:00 to 06:00 = 0.25 days
    EXPECTED_DURATION_DAYS = 0.25
    EXPECTED_ROUTING_STEP_SECS = 15.0  # ROUTING_STEP 0:00:15 in the .inp


@pytest.fixture(scope="session")
def reference_model():
    """Return the :class:`ReferenceModel` constants for shared tests."""
    return ReferenceModel


# ---------------------------------------------------------------------------
# Per-test INP (isolated copy in tmp_path)
# ---------------------------------------------------------------------------


@pytest.fixture
def inp_path(tmp_path: Path) -> str:
    """Copy ``site_drainage_example.inp`` into ``tmp_path`` and return the path.

    Each test gets its own copy so the engine can write ``.rpt`` and
    ``.out`` files alongside without contention.
    """
    dest = tmp_path / "site_drainage_example.inp"
    shutil.copy(_REFERENCE_INP, dest)
    return str(dest)


# Backwards-compatible alias for tests that imported the old name.
@pytest.fixture
def tmp_inp(inp_path: str) -> str:
    """Alias for :func:`inp_path` retained for older tests."""
    return inp_path


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_manager(tmp_path: Path):
    """Return a :class:`SessionManager` whose working directory is ``tmp_path``.

    All sessions are torn down at end-of-test so engine C handles are not
    leaked between tests.
    """
    from openswmm_mcp.session import SessionManager

    sm = SessionManager(max_sessions=5, working_dir=str(tmp_path))
    try:
        yield sm
    finally:
        await sm.cleanup_all()


# ---------------------------------------------------------------------------
# Engine parametrization
# ---------------------------------------------------------------------------


@pytest.fixture(params=["openswmm", "legacy"])
def engine(request) -> str:
    """Parametrized fixture driving tests through both backends.

    Tests that take this fixture run twice, once per backend.  Use
    ``openswmm_only`` or ``legacy_only`` fixtures when a test should only
    exercise one backend.
    """
    return request.param


@pytest.fixture
def openswmm_only() -> str:
    """Return the ``"openswmm"`` engine kind without parametrizing."""
    return "openswmm"


@pytest.fixture
def legacy_only() -> str:
    """Return the ``"legacy"`` engine kind without parametrizing."""
    return "legacy"


# ---------------------------------------------------------------------------
# Higher-level session helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def open_session(session_manager, inp_path):
    """Factory: ``await open_session(engine="openswmm", session_id="s")``.

    Creates a session via the SessionManager, opens & initializes the
    solver, and returns the :class:`SimSession`.  The function-scoped
    ``session_manager`` fixture handles cleanup when the test finishes.
    """

    async def _open(
        engine: str = "openswmm",
        session_id: str = "default",
    ):
        session = await session_manager.create_session(
            session_id=session_id,
            inp_path=inp_path,
            engine=engine,
        )
        # Mirror what lifecycle.open_model does.
        session.backend.solver.open()
        session.state = "opened"
        session.backend.solver.initialize()
        session.state = "initialized"
        return session

    return _open


# ---------------------------------------------------------------------------
# Lifespan-context shim (so Context-aware tools can be invoked in tests)
# ---------------------------------------------------------------------------


class FakeContext:
    """Minimal stand-in for :class:`fastmcp.Context` used in tool unit tests.

    Tools call ``ctx.lifespan_context["session_manager"]`` and (for long-
    running tools) ``await ctx.report_progress(done, total)``.  This shim
    provides both without pulling in the full FastMCP runtime.
    """

    def __init__(self, session_manager) -> None:
        self.lifespan_context = {"session_manager": session_manager}
        self.progress_calls: list[tuple[float, float]] = []

    async def report_progress(self, done: float, total: float) -> None:
        self.progress_calls.append((done, total))


@pytest.fixture
def fake_ctx(session_manager) -> FakeContext:
    """Return a :class:`FakeContext` wired to the session manager fixture."""
    return FakeContext(session_manager)
