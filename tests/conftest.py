"""Shared test fixtures for the openswmm-mcp test suite.

Provides:

- ``mock_openswmm_engine`` (autouse, session-scoped) -- patches
  ``openswmm.engine`` classes with mocks so no compiled C library is needed.
  The patching works by injecting a synthetic ``openswmm`` package and
  ``openswmm.engine`` module into ``sys.modules`` *before* any application
  code has a chance to import the real (C-extension) package.

- ``tmp_inp(tmp_path)`` -- writes a minimal ``.inp`` file and returns its
  path as a string.

- ``session_manager(tmp_path)`` -- a ready-to-use :class:`SessionManager`
  whose working directory is the pytest-managed temporary directory.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

# ---------------------------------------------------------------------------
# Engine patching -- MUST happen at module level (before collection imports)
# ---------------------------------------------------------------------------
# pytest collects test modules by importing them, which transitively imports
# ``openswmm_mcp.session`` which does ``from openswmm.engine import ...``.
# A session-scoped fixture would run too late.  Instead we inject the mock
# module here, at conftest load time.
from tests.mocks.engine import (  # noqa: E402
    MockControls,
    MockForcing,
    MockGages,
    MockHotStart,
    MockInflows,
    MockInfrastructure,
    MockLinks,
    MockMassBalance,
    MockModelBuilder,
    MockNodes,
    MockOutputReader,
    MockPollutants,
    MockQuality,
    MockSolver,
    MockSpatial,
    MockStatistics,
    MockSubcatchments,
    MockTables,
    OutLinkVar,
    OutNodeVar,
    OutSubcatchVar,
    OutSystemVar,
    RoutingTotal,
    RunoffTotal,
)

_engine_mod = ModuleType("openswmm.engine")

# Domain classes
_engine_mod.Solver = MockSolver  # type: ignore[attr-defined]
_engine_mod.Nodes = MockNodes  # type: ignore[attr-defined]
_engine_mod.Links = MockLinks  # type: ignore[attr-defined]
_engine_mod.Subcatchments = MockSubcatchments  # type: ignore[attr-defined]
_engine_mod.Gages = MockGages  # type: ignore[attr-defined]
_engine_mod.Pollutants = MockPollutants  # type: ignore[attr-defined]
_engine_mod.MassBalance = MockMassBalance  # type: ignore[attr-defined]
_engine_mod.Statistics = MockStatistics  # type: ignore[attr-defined]
_engine_mod.Forcing = MockForcing  # type: ignore[attr-defined]
_engine_mod.Controls = MockControls  # type: ignore[attr-defined]
_engine_mod.Inflows = MockInflows  # type: ignore[attr-defined]
_engine_mod.Infrastructure = MockInfrastructure  # type: ignore[attr-defined]
_engine_mod.Quality = MockQuality  # type: ignore[attr-defined]
_engine_mod.HotStart = MockHotStart  # type: ignore[attr-defined]
_engine_mod.Spatial = MockSpatial  # type: ignore[attr-defined]
_engine_mod.Tables = MockTables  # type: ignore[attr-defined]
_engine_mod.ModelBuilder = MockModelBuilder  # type: ignore[attr-defined]
_engine_mod.OutputReader = MockOutputReader  # type: ignore[attr-defined]

# Enum stubs used by analysis/query tools
_engine_mod.RunoffTotal = RunoffTotal  # type: ignore[attr-defined]
_engine_mod.RoutingTotal = RoutingTotal  # type: ignore[attr-defined]
_engine_mod.OutNodeVar = OutNodeVar  # type: ignore[attr-defined]
_engine_mod.OutLinkVar = OutLinkVar  # type: ignore[attr-defined]
_engine_mod.OutSubcatchVar = OutSubcatchVar  # type: ignore[attr-defined]
_engine_mod.OutSystemVar = OutSystemVar  # type: ignore[attr-defined]

# Parent package
_openswmm_mod = ModuleType("openswmm")
_openswmm_mod.engine = _engine_mod  # type: ignore[attr-defined]

sys.modules["openswmm"] = _openswmm_mod
sys.modules["openswmm.engine"] = _engine_mod


# ---------------------------------------------------------------------------
# Convenience fixtures
# ---------------------------------------------------------------------------


_MINIMAL_INP = """\
[TITLE]
Test Model

[OPTIONS]
FLOW_UNITS           CFS
INFILTRATION         HORTON
FLOW_ROUTING         DYNWAVE
START_DATE           01/01/2020
START_TIME           00:00:00
END_DATE             01/01/2020
END_TIME             06:00:00
REPORT_STEP          00:05:00
ROUTING_STEP         0:00:30

[JUNCTIONS]
;;Name  Elev  MaxDepth
J1      100   10

[OUTFALLS]
;;Name  Elev  Type
O1      90    FREE

[CONDUITS]
;;Name  FromNode  ToNode  Length  Roughness
C1      J1        O1      400     0.013

[XSECTIONS]
;;Link  Shape     Geom1  Geom2  Geom3  Geom4
C1      CIRCULAR  1.0    0      0      0
"""


@pytest.fixture
def tmp_inp(tmp_path: Path) -> str:
    """Write a minimal ``.inp`` file and return its path as a string.

    The file is a valid (if trivial) SWMM input containing one junction,
    one outfall, one conduit, a 6-hour simulation window, and dynamic-wave
    routing -- enough for the mock engine to accept without complaint.
    """
    inp = tmp_path / "test_model.inp"
    inp.write_text(_MINIMAL_INP)
    return str(inp)


@pytest.fixture
def session_manager(tmp_path: Path):
    """Return a :class:`SessionManager` whose working directory is *tmp_path*.

    The manager is configured for up to 5 concurrent sessions.  Because the
    ``mock_openswmm_engine`` fixture is autouse/session-scoped, all
    ``Solver`` instances created by this manager will be :class:`MockSolver`.
    """
    from openswmm_mcp.session import SessionManager

    return SessionManager(max_sessions=5, working_dir=str(tmp_path))
