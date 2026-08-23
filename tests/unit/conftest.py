"""Unit-test fixtures for openswmm-mcp using site_drainage_model.inp.

All unit tests run against the *real* ``openswmm.engine`` (Python 3.12 conda
env).  This conftest overrides the root-level ``inp_path`` and
``reference_model`` fixtures so that every test in ``tests/unit/`` uses the
``site_drainage_model.inp`` network, which has:

    12 nodes (J1–J11, O1), 11 conduits (C1–C11),
    7 subcatchments (S1–S7), 1 rain gage (RainGage), 1 pollutant (TSS).

Simulation period: 01/01/1998 00:00 → 01/02/1998 06:00 (30 h = 1.25 days).
Routing step: 5 s.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

_MODEL_INP = (Path(__file__).parent / "data" / "site_drainage_model.inp").resolve()

assert _MODEL_INP.exists(), (
    f"site_drainage_model.inp not found at {_MODEL_INP}; "
    "the fixture file must be committed to the repository."
)


# ---------------------------------------------------------------------------
# Reference constants
# ---------------------------------------------------------------------------


class ModelRef:
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

    # 01/01/1998 00:00 → 01/02/1998 06:00 = 30 h = 1.25 days
    EXPECTED_DURATION_DAYS = 1.25
    EXPECTED_ROUTING_STEP_SECS = 5.0


@pytest.fixture(scope="session")
def reference_model():
    return ModelRef


# ---------------------------------------------------------------------------
# Per-test INP copy
# ---------------------------------------------------------------------------


@pytest.fixture
def inp_path(tmp_path: Path) -> str:
    dest = tmp_path / "site_drainage_model.inp"
    shutil.copy(_MODEL_INP, dest)
    return str(dest)


@pytest.fixture
def tmp_inp(inp_path: str) -> str:
    return inp_path


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


@pytest.fixture
async def session_manager(tmp_path: Path):
    from openswmm_mcp.session import SessionManager

    sm = SessionManager(max_sessions=10, working_dir=str(tmp_path))
    try:
        yield sm
    finally:
        await sm.cleanup_all()


# ---------------------------------------------------------------------------
# FakeContext
# ---------------------------------------------------------------------------


class FakeContext:
    def __init__(self, session_manager) -> None:
        self.lifespan_context = {"session_manager": session_manager}
        self.progress_calls: list[tuple[float, float]] = []

    async def report_progress(self, done: float, total: float) -> None:
        self.progress_calls.append((done, total))


@pytest.fixture
def fake_ctx(session_manager) -> FakeContext:
    return FakeContext(session_manager)


# ---------------------------------------------------------------------------
# open_session factory
# ---------------------------------------------------------------------------


@pytest.fixture
def open_session(session_manager, inp_path):
    """Factory: ``await open_session(engine="openswmm", session_id="s")``."""

    async def _open(engine: str = "openswmm", session_id: str = "default"):
        session = await session_manager.create_session(
            session_id=session_id,
            inp_path=inp_path,
            engine=engine,
        )
        session.backend.solver.open()
        session.state = "opened"
        session.backend.solver.initialize()
        session.state = "initialized"
        return session

    return _open
