"""Unit tests for lifecycle tool functions."""

from __future__ import annotations

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import ModelSummary, SimulationResult, StepResult

# ---------------------------------------------------------------------------
# Mock MCP Context
# ---------------------------------------------------------------------------


class MockContext:
    """Minimal stand-in for ``fastmcp.Context`` used by tool handlers."""

    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}
        self._progress = []

    async def report_progress(self, current, total):
        self._progress.append((current, total))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOpenModel:
    async def test_open_model(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        result = await open_model(ctx, inp_path=tmp_inp, session_id="test")

        assert isinstance(result, ModelSummary)
        assert result.session_id == "test"
        assert result.state == "initialized"

    async def test_open_model_returns_correct_counts(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        result = await open_model(ctx, inp_path=tmp_inp, session_id="counts")

        assert result.node_count == 12
        assert result.link_count == 11
        assert result.subcatchment_count == 8
        assert result.gage_count == 2
        assert result.pollutant_count == 0

    async def test_open_model_flow_units(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        result = await open_model(ctx, inp_path=tmp_inp, session_id="opts")

        assert result.flow_units == "CFS"
        assert result.route_model == "DYNWAVE"

    async def test_open_model_timing(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        result = await open_model(ctx, inp_path=tmp_inp, session_id="timing")

        assert result.start_time == 45000.0
        assert result.end_time == 45000.25
        assert result.routing_step == 30.0

    async def test_open_model_duplicate(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="dup")

        with pytest.raises(ToolError, match="already exists"):
            await open_model(ctx, inp_path=tmp_inp, session_id="dup")


class TestRunSimulation:
    async def test_run_simulation(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="run")

        result = await run_simulation(ctx, session_id="run")

        assert isinstance(result, SimulationResult)
        assert result.session_id == "run"
        assert result.steps_completed == 100
        assert result.runoff_continuity_error == -0.01
        assert result.routing_continuity_error == -0.02

    async def test_run_simulation_reports_progress(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="prog")

        await run_simulation(ctx, session_id="prog")

        # Must have called report_progress at least once + the 100% call
        assert len(ctx._progress) > 0
        assert ctx._progress[-1] == (100, 100)

    async def test_run_simulation_wall_time(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="wall")

        result = await run_simulation(ctx, session_id="wall")

        assert result.elapsed_wall_time >= 0.0


class TestStepSimulation:
    async def test_step_simulation_single(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="step1")

        result = await step_simulation(ctx, session_id="step1", num_steps=1)

        assert isinstance(result, StepResult)
        assert result.steps_taken == 1
        assert result.completed is False

    async def test_step_simulation_multiple(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="step5")

        result = await step_simulation(ctx, session_id="step5", num_steps=5)

        assert result.steps_taken == 5
        assert result.completed is False

    async def test_step_simulation_to_completion(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="stepall")

        # Step far beyond the 100-step limit to ensure completion
        result = await step_simulation(ctx, session_id="stepall", num_steps=200)

        assert result.completed is True
        assert result.steps_taken == 100

    async def test_step_auto_starts(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="autostart")

        # Session is in "initialized" state; step should auto-start
        session = await session_manager.get_session("autostart")
        assert session.state == "initialized"

        await step_simulation(ctx, session_id="autostart", num_steps=1)

        session = await session_manager.get_session("autostart")
        assert session.state == "running"


class TestCloseModel:
    async def test_close_model(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import close_model, open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="close")

        result = await close_model(ctx, session_id="close")

        assert result["status"] == "closed"
        assert result["session_id"] == "close"

        with pytest.raises(ToolError):
            await session_manager.get_session("close")

    async def test_close_nonexistent(self, session_manager):
        from openswmm_mcp.tools.lifecycle import close_model

        ctx = MockContext(session_manager)
        with pytest.raises(ToolError, match="SESSION_NOT_FOUND"):
            await close_model(ctx, session_id="nope")


class TestListSessions:
    async def test_list_sessions_empty(self, session_manager):
        from openswmm_mcp.tools.lifecycle import list_sessions

        ctx = MockContext(session_manager)
        result = await list_sessions(ctx)

        assert result == []

    async def test_list_sessions_after_open(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import list_sessions, open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="listed")

        result = await list_sessions(ctx)

        assert len(result) == 1
        assert result[0]["id"] == "listed"
        assert result[0]["state"] == "initialized"
