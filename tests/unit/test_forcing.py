"""Unit tests for forcing and control tool functions."""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import ForcingResult

# ---------------------------------------------------------------------------
# Mock MCP Context
# ---------------------------------------------------------------------------


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _open_and_run(session_manager, tmp_inp, session_id="default"):
    """Open a model, step once to reach 'running' state, and return context."""
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=tmp_inp, session_id=session_id)
    await step_simulation(ctx, session_id=session_id, num_steps=1)
    return ctx


# ---------------------------------------------------------------------------
# Tests: set_forcing
# ---------------------------------------------------------------------------


class TestSetForcing:
    async def test_set_forcing_node_inflow(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_node")
        result = await set_forcing(
            ctx,
            session_id="f_node",
            target_type="node",
            element_id="J1",
            variable="lateral_inflow",
            value=5.0,
        )

        assert isinstance(result, ForcingResult)
        assert result.status == "applied"
        assert result.target_type == "node"
        assert result.element_id == "J1"
        assert result.variable == "lateral_inflow"
        assert result.value == 5.0

    async def test_set_forcing_gage_rainfall(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.forcing import set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_gage")
        result = await set_forcing(
            ctx,
            session_id="f_gage",
            target_type="gage",
            element_id=reference_model.GAGE_ID,
            variable="rainfall",
            value=2.0,
        )

        assert result.target_type == "gage"
        assert result.variable == "rainfall"
        assert result.value == 2.0

    async def test_set_forcing_link_setting(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_link")
        result = await set_forcing(
            ctx,
            session_id="f_link",
            target_type="link",
            element_id="C1",
            variable="setting",
            value=0.5,
            mode="replace",
            persist=True,
        )

        assert result.mode == "replace"
        assert result.persist is True

    async def test_set_forcing_subcatchment_snowfall(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_snow")
        result = await set_forcing(
            ctx,
            session_id="f_snow",
            target_type="subcatchment",
            element_id="S1",
            variable="snowfall",
            value=0.3,
            persist=True,
        )

        assert isinstance(result, ForcingResult)
        assert result.status == "applied"
        assert result.target_type == "subcatchment"
        assert result.variable == "snowfall"
        assert result.value == 0.3

    async def test_set_forcing_requires_running_state(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import set_forcing
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="f_state")

        # Session is in "initialized" state, not "running"
        with pytest.raises(Exception, match="running"):
            await set_forcing(
                ctx,
                session_id="f_state",
                target_type="node",
                element_id="J1",
                variable="lateral_inflow",
                value=1.0,
            )

    async def test_set_forcing_invalid_target_type(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_badtype")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await set_forcing(
                ctx,
                session_id="f_badtype",
                target_type="bogus",
                element_id="J1",
                variable="depth",
                value=1.0,
            )

    async def test_set_forcing_invalid_variable(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_badvar")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await set_forcing(
                ctx,
                session_id="f_badvar",
                target_type="node",
                element_id="J1",
                variable="bogus",
                value=1.0,
            )

    async def test_set_forcing_add_mode(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_add")
        result = await set_forcing(
            ctx,
            session_id="f_add",
            target_type="node",
            element_id="J1",
            variable="lateral_inflow",
            value=3.0,
            mode="add",
        )

        assert result.mode == "add"


# ---------------------------------------------------------------------------
# Tests: clear_forcing
# ---------------------------------------------------------------------------


class TestClearForcing:
    async def test_clear_forcing_all(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import clear_forcing, set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_clear")

        # Apply a forcing first
        await set_forcing(
            ctx,
            session_id="f_clear",
            target_type="node",
            element_id="J1",
            variable="lateral_inflow",
            value=5.0,
        )

        result = await clear_forcing(ctx, session_id="f_clear")

        assert result["status"] == "cleared"
        assert result["scope"] == "all"

    async def test_clear_forcing_specific(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import clear_forcing, set_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_clear1")

        await set_forcing(
            ctx,
            session_id="f_clear1",
            target_type="node",
            element_id="J1",
            variable="lateral_inflow",
            value=5.0,
        )

        result = await clear_forcing(
            ctx,
            session_id="f_clear1",
            target_type="node",
            element_id="J1",
        )

        assert result["status"] == "cleared"
        assert result["scope"] == "element"
        assert result["element_id"] == "J1"

    async def test_clear_forcing_partial_args_error(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import clear_forcing

        ctx = await _open_and_run(session_manager, tmp_inp, "f_partial")

        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await clear_forcing(ctx, session_id="f_partial", target_type="node")


# ---------------------------------------------------------------------------
# Tests: set_link_control
# ---------------------------------------------------------------------------


class TestSetLinkControl:
    async def test_set_link_control(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import set_link_control

        ctx = await _open_and_run(session_manager, tmp_inp, "f_lctrl")
        result = await set_link_control(ctx, session_id="f_lctrl", link_id="C1", setting=0.75)

        assert result["status"] == "applied"
        assert result["link_id"] == "C1"
        assert result["setting"] == 0.75


# ---------------------------------------------------------------------------
# Tests: set_link_quality
# ---------------------------------------------------------------------------


class TestSetLinkQuality:
    async def test_set_link_quality(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.forcing import set_link_quality

        ctx = await _open_and_run(session_manager, tmp_inp, "f_lqual")
        result = await set_link_quality(
            ctx,
            session_id="f_lqual",
            link_id=reference_model.FIRST_LINK_ID,
            pollutant=reference_model.POLLUTANT_ID,
            value=12.5,
            persist=True,
        )
        assert result["status"] == "applied"
        assert result["link_id"] == reference_model.FIRST_LINK_ID
        assert result["pollutant"] == reference_model.POLLUTANT_ID
        assert result["value"] == 12.5
        assert result["persist"] is True

    async def test_set_link_quality_requires_running(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.forcing import set_link_quality
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="f_lqual_state")
        with pytest.raises(ToolError, match="running"):
            await set_link_quality(
                ctx,
                session_id="f_lqual_state",
                link_id=reference_model.FIRST_LINK_ID,
                pollutant=reference_model.POLLUTANT_ID,
                value=1.0,
            )

    async def test_set_link_quality_unknown_link(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.forcing import set_link_quality

        ctx = await _open_and_run(session_manager, tmp_inp, "f_lqual_bad")
        with pytest.raises(ToolError, match="not found"):
            await set_link_quality(
                ctx,
                session_id="f_lqual_bad",
                link_id="NOPE",
                pollutant=reference_model.POLLUTANT_ID,
                value=1.0,
            )

    async def test_set_link_quality_unknown_pollutant(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.forcing import set_link_quality

        ctx = await _open_and_run(session_manager, tmp_inp, "f_lqual_badp")
        with pytest.raises(ToolError, match="not found"):
            await set_link_quality(
                ctx,
                session_id="f_lqual_badp",
                link_id=reference_model.FIRST_LINK_ID,
                pollutant="GLORP",
                value=1.0,
            )

    async def test_set_link_quality_invalid_mode(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.forcing import set_link_quality

        ctx = await _open_and_run(session_manager, tmp_inp, "f_lqual_mode")
        with pytest.raises(ToolError, match="Invalid forcing mode"):
            await set_link_quality(
                ctx,
                session_id="f_lqual_mode",
                link_id=reference_model.FIRST_LINK_ID,
                pollutant=reference_model.POLLUTANT_ID,
                value=1.0,
                mode="multiply",
            )


# ---------------------------------------------------------------------------
# Tests: add_control_rule
# ---------------------------------------------------------------------------


class TestAddControlRule:
    async def test_add_control_rule(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.forcing import add_control_rule

        ctx = await _open_and_run(session_manager, tmp_inp, "f_rule")
        rule_text = "RULE R1\nIF NODE J1 DEPTH > 5\nTHEN PUMP P1 STATUS = ON"

        result = await add_control_rule(ctx, session_id="f_rule", rule_text=rule_text)

        assert result["status"] == "added"
        assert result["rule_index"] == 1
        assert result["total_rules"] == 1


# ---------------------------------------------------------------------------
# Tests: set_rainfall_override
# ---------------------------------------------------------------------------


class TestSetRainfallOverride:
    async def test_set_rainfall_override(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.forcing import set_rainfall_override

        ctx = await _open_and_run(session_manager, tmp_inp, "f_rain")
        result = await set_rainfall_override(
            ctx, session_id="f_rain", gage_id=reference_model.GAGE_ID, rainfall=1.5
        )

        assert result["status"] == "applied"
        assert result["gage_id"] == reference_model.GAGE_ID
        assert result["rainfall"] == 1.5
        assert result["mode"] == "replace"
        assert result["persist"] is True
