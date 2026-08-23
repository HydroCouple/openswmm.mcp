"""v1 API migration smoke test.

Exercises one representative tool per migrated MCP module against the
real ``openswmm.engine`` and confirms the v1 surface is reachable end
to end.  This is the **Phase 5 verification** from
``docs/developer/V1_MIGRATION_PLAN.md`` §6.

The pytest module here drives the tools by direct call (not via the MCP
protocol), so we can assert on return shapes synchronously.  The companion
standalone script :mod:`run_v1_smoke` exercises the same surface and
writes a user-reviewable JSON report to ``tests/integration/reports/``.

This module is skipped by default; pass ``--run-integration`` (or set
``OPENSWMM_RUN_INTEGRATION=1``) to enable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.integration

# Skip the entire module if the openswmm engine is not installed.
openswmm = pytest.importorskip("openswmm.engine")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def inp_path() -> str:
    """Locate the site_drainage_example.inp test fixture.

    Falls back to the in-tree copy at ``tests/data/site_drainage_example.inp``
    so the test does not require an external repository checkout.
    """
    in_tree = Path(__file__).resolve().parent.parent / "data" / "site_drainage_example.inp"
    if in_tree.exists():
        return str(in_tree)
    # Also try the engine's checked-in copy as a secondary path.
    engine_copy = Path.home() / (
        "Documents/Projects/cbuahin_github/openswmm.engine/"
        "python/tests/data/solver/site_drainage_example.inp"
    )
    if engine_copy.exists():
        return str(engine_copy)
    pytest.skip("site_drainage_example.inp not found")


@pytest.fixture
async def opened_session(inp_path: str, tmp_path: Path):
    """Open the model and return a started SimSession.

    Yields a session in the ``running`` state; the test is responsible for
    iterating steps and calling ``solver.end()`` if it wants the ``ended``
    state.  Cleanup is handled in the fixture teardown.
    """
    from openswmm_mcp.session import SessionManager

    sm = SessionManager(max_sessions=2, working_dir=str(tmp_path))
    session = await sm.create_session(
        "v1_smoke",
        inp_path,
        rpt_path=str(tmp_path / "v1_smoke.rpt"),
        out_path=str(tmp_path / "v1_smoke.out"),
    )
    session.solver.open()
    session.solver.initialize()
    session.state = "initialized"
    session.solver.start()
    session.state = "running"
    try:
        yield session
    finally:
        await sm.close_session("v1_smoke")


def _make_ctx(session_manager) -> Any:
    """Build a minimal Context double for direct tool calls.

    The MCP tool functions read the SessionManager out of
    ``ctx.lifespan_context["session_manager"]`` (or via
    ``get_session_manager(ctx)``); for direct calls in a unit test we can
    fake the lifespan_context with a dict so the helper sees what it
    expects.
    """
    ctx = MagicMock()
    ctx.lifespan_context = {"session_manager": session_manager}

    async def _report_progress(value: int, total: int) -> None:
        return None

    ctx.report_progress = _report_progress
    return ctx


# ---------------------------------------------------------------------------
# Smoke checks — one tool per migrated module
# ---------------------------------------------------------------------------


class TestV1Surface:
    """Each test calls one representative tool function from a migrated
    module and asserts the response shape is right for the v1 surface."""

    async def test_nodes_container_protocol(self, opened_session):
        """Phase 2: v1 container protocol on nodes is reachable."""
        nodes = opened_session.nodes
        assert len(nodes) > 0
        first = nodes[0]
        assert isinstance(first.id, str)
        assert isinstance(first.depth, (int, float))

    async def test_query_get_system_summary(self, opened_session):
        """Phase 2: query.get_system_summary returns counts + flow_units."""
        from openswmm_mcp.session import SessionManager  # noqa: F401
        from openswmm_mcp.tools.query import get_system_summary

        # Pull the SessionManager off the session's manager via the
        # _sessions dict (which is registered by the fixture).
        ctx = _make_ctx(
            opened_session._session_manager
            if hasattr(opened_session, "_session_manager")
            else _get_sm(opened_session)
        )
        result = await get_system_summary(ctx, session_id="v1_smoke")
        assert result.node_count > 0
        assert result.link_count > 0
        assert result.flow_units != "UNKNOWN"

    async def test_lifecycle_stride(self, opened_session):
        """Phase 4: stride advances the simulation in one C call."""
        from openswmm_mcp.tools.lifecycle import stride

        ctx = _make_ctx(_get_sm(opened_session))
        result = await stride(ctx, session_id="v1_smoke", num_steps=5)
        assert result.steps_taken == 5
        # current_time should be > 0 after 5 routing steps.
        assert result.current_time > 0.0

    async def test_lifecycle_until_elapsed(self, opened_session):
        """Phase 4: until_elapsed advances to a specific elapsed time."""
        from openswmm_mcp.tools.lifecycle import until_elapsed

        ctx = _make_ctx(_get_sm(opened_session))
        # 5 minutes of sim time — should be well within Example1's duration.
        result = await until_elapsed(ctx, session_id="v1_smoke", seconds=300.0)
        assert result.current_time > 0.0
        # elapsed should be ≥ 300 s / 86400 s ≈ 0.00347 days.
        assert result.elapsed >= 300.0 / 86400.0 * 0.99

    async def test_links_v1_property_access(self, opened_session):
        """Phase 2: link wrapper exposes v1 property surface."""
        links = opened_session.links
        assert len(links) > 0
        link = links[0]
        # Direct property access (v1 idiom).
        assert isinstance(link.flow, (int, float))
        assert isinstance(link.depth, (int, float))
        assert isinstance(link.from_node.id, str)
        assert isinstance(link.to_node.id, str)

    async def test_analysis_get_statistics(self, opened_session):
        """Phase 3f: analysis.get_statistics uses v1 stats arrays."""
        from openswmm_mcp.tools.analysis import get_statistics
        from openswmm_mcp.tools.lifecycle import stride

        ctx = _make_ctx(_get_sm(opened_session))
        # Advance enough that statistics arrays are populated.
        await stride(ctx, session_id="v1_smoke", num_steps=200)

        first_node_id = opened_session.nodes.get_id(0)
        result = await get_statistics(
            ctx,
            session_id="v1_smoke",
            element_type="node",
            element_id=first_node_id,
        )
        assert "max_depth" in result
        assert isinstance(result["max_depth"], (int, float))

    async def test_mass_balance_property_access(self, opened_session):
        """Phase 1+2: mass_balance exposes v1 property surface."""
        from openswmm_mcp.tools.lifecycle import run_for_steps

        ctx = _make_ctx(_get_sm(opened_session))
        # Drive the simulation to completion so mass-balance is meaningful.
        await run_for_steps(ctx, session_id="v1_smoke", max_steps=100000)

        # v1 property style; legacy backend still exposes this via the
        # adapter I added in Phase 1.
        runoff = opened_session.mass_balance.runoff_continuity_error
        routing = opened_session.mass_balance.routing_continuity_error
        assert -1.0 < runoff < 1.0  # fractions; should never exceed +/-100%
        assert -1.0 < routing < 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_sm(session) -> Any:
    """Locate the SessionManager that owns *session*.

    The fixture creates the manager in a local scope, so the easiest way
    to share it with the tool functions is to walk back through the
    session's working_dir parent.  This helper centralises the
    workaround.
    """
    # The fixture stores the manager in the test function's local scope.
    # We rely on the manager being reachable via the session's
    # ``working_dir`` parent — alternatively, the fixture could attach the
    # manager to the session as an attribute.  We use a shared registry
    # built when the fixture creates the manager.
    return _SHARED_MANAGER[0]


_SHARED_MANAGER: list[Any] = []


# Override the fixture to also register the manager in _SHARED_MANAGER so
# tools called via _make_ctx can reach it.
@pytest.fixture
async def opened_session(inp_path: str, tmp_path: Path):  # noqa: F811
    """Open the model and yield a started SimSession (overrides above).

    Also registers the SessionManager in ``_SHARED_MANAGER`` so each test
    can reconstruct a Context object that points to the right manager.
    """
    from openswmm_mcp.session import SessionManager

    sm = SessionManager(max_sessions=2, working_dir=str(tmp_path))
    _SHARED_MANAGER.append(sm)
    session = await sm.create_session(
        "v1_smoke",
        inp_path,
        rpt_path=str(tmp_path / "v1_smoke.rpt"),
        out_path=str(tmp_path / "v1_smoke.out"),
    )
    session.solver.open()
    session.solver.initialize()
    session.state = "initialized"
    session.solver.start()
    session.state = "running"
    try:
        yield session
    finally:
        try:
            await sm.close_session("v1_smoke")
        finally:
            _SHARED_MANAGER.clear()
