"""Unit tests for analysis tool functions."""

from __future__ import annotations

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import FloodingSummaryItem, MassBalanceResult

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


async def _open_and_complete(session_manager, tmp_inp, session_id="default"):
    """Open, run to completion, and return context (session in 'ended' state)."""
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=tmp_inp, session_id=session_id)
    await run_simulation(ctx, session_id=session_id)
    return ctx


async def _open_and_step(session_manager, tmp_inp, session_id="default"):
    """Open and step once to reach 'running' state, and return context."""
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=tmp_inp, session_id=session_id)
    await step_simulation(ctx, session_id=session_id, num_steps=1)
    return ctx


# ---------------------------------------------------------------------------
# Tests: get_statistics
# ---------------------------------------------------------------------------


class TestGetStatistics:
    async def test_get_statistics_node(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_node")
        result = await get_statistics(
            ctx,
            session_id="stat_node",
            element_type="node",
            element_id="J1",
        )

        assert result["element_type"] == "node"
        assert result["element_id"] == "J1"
        assert result["max_depth"] == 5.0
        assert result["max_head"] == 105.0
        assert result["max_lat_inflow"] == 10.0
        assert result["vol_flooded"] == 100.0
        assert result["time_flooded"] == 2.0

    async def test_get_statistics_link(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_link")
        result = await get_statistics(
            ctx,
            session_id="stat_link",
            element_type="link",
            element_id="C1",
        )

        assert result["element_type"] == "link"
        assert result["element_id"] == "C1"
        assert result["max_flow"] == 15.0
        assert result["max_velocity"] == 5.0
        assert result["max_depth"] == 3.0
        assert result["time_above_normal"] == 1.0

    async def test_get_statistics_subcatchment(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_sc")
        result = await get_statistics(
            ctx,
            session_id="stat_sc",
            element_type="subcatchment",
            element_id="S1",
        )

        assert result["element_type"] == "subcatchment"
        assert result["max_runoff"] == 8.0

    async def test_get_statistics_missing_element_id(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_miss")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await get_statistics(ctx, session_id="stat_miss", element_type="node", element_id="")

    async def test_get_statistics_invalid_type(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_bad")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await get_statistics(
                ctx,
                session_id="stat_bad",
                element_type="bogus",
                element_id="J1",
            )


# ---------------------------------------------------------------------------
# Tests: get_mass_balance
# ---------------------------------------------------------------------------


class TestGetMassBalance:
    async def test_get_mass_balance(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_mass_balance

        ctx = await _open_and_step(session_manager, tmp_inp, "mb")
        result = await get_mass_balance(ctx, session_id="mb")

        assert isinstance(result, MassBalanceResult)
        assert result.runoff_continuity_error == -0.01
        assert result.routing_continuity_error == -0.02
        # No pollutants in mock -> quality error is None
        assert result.quality_continuity_error is None
        assert isinstance(result.runoff_total, dict)
        assert isinstance(result.routing_total, dict)
        assert len(result.runoff_total) > 0
        assert len(result.routing_total) > 0

    async def test_get_mass_balance_totals_keys(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_mass_balance

        ctx = await _open_and_step(session_manager, tmp_inp, "mb_keys")
        result = await get_mass_balance(ctx, session_id="mb_keys")

        # RunoffTotal members: RAINFALL, EVAP, INFIL, RUNOFF
        assert "rainfall" in result.runoff_total
        assert "evap" in result.runoff_total

        # RoutingTotal members: DW_INFLOW, GW_INFLOW, FLOODING, OUTFLOW, STORAGE
        assert "dw_inflow" in result.routing_total
        assert "flooding" in result.routing_total


# ---------------------------------------------------------------------------
# Tests: get_flooding_summary
# ---------------------------------------------------------------------------


class TestGetFloodingSummary:
    async def test_get_flooding_summary(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_flooding_summary

        ctx = await _open_and_complete(session_manager, tmp_inp, "flood")
        result = await get_flooding_summary(ctx, session_id="flood")

        assert isinstance(result, list)
        # Nodes J1, J2, J3 (indices 0-2) have flooding
        assert len(result) == 3
        assert all(isinstance(r, FloodingSummaryItem) for r in result)

        # Should be sorted by total_flood_volume descending
        vols = [r.total_flood_volume for r in result]
        assert vols == sorted(vols, reverse=True)

    async def test_get_flooding_summary_values(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_flooding_summary

        ctx = await _open_and_complete(session_manager, tmp_inp, "flood_val")
        result = await get_flooding_summary(ctx, session_id="flood_val")

        first = result[0]
        assert first.total_flood_volume == 100.0
        assert first.max_overflow_rate == 0.5
        assert first.time_flooded == 2.0
        assert first.max_depth == 5.0

    async def test_get_flooding_summary_with_threshold(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_flooding_summary

        ctx = await _open_and_complete(session_manager, tmp_inp, "flood_thr")
        # Set a threshold above the mock flood volume (100.0)
        result = await get_flooding_summary(ctx, session_id="flood_thr", min_flood_volume=200.0)

        assert len(result) == 0

    async def test_get_flooding_summary_node_ids(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_flooding_summary

        ctx = await _open_and_complete(session_manager, tmp_inp, "flood_ids")
        result = await get_flooding_summary(ctx, session_id="flood_ids")

        flooded_ids = {r.node_id for r in result}
        assert flooded_ids == {"J1", "J2", "J3"}


# ---------------------------------------------------------------------------
# Tests: get_capacity_summary
# ---------------------------------------------------------------------------


class TestGetCapacitySummary:
    async def test_get_capacity_summary(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_capacity_summary

        ctx = await _open_and_complete(session_manager, tmp_inp, "cap")

        # mock: stats.link_max_depth=3.0, links.get_max_depth=3.0
        # filling = 3.0/3.0 = 1.0 which is NOT > 1.0 (default threshold)
        # So no links should appear
        result = await get_capacity_summary(ctx, session_id="cap")
        assert isinstance(result, list)
        assert len(result) == 0

    async def test_get_capacity_summary_low_threshold(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_capacity_summary

        ctx = await _open_and_complete(session_manager, tmp_inp, "cap_low")

        # Set threshold to 0.5 -- filling = 3.0/3.0 = 1.0 > 0.5
        result = await get_capacity_summary(ctx, session_id="cap_low", max_filling_threshold=0.5)

        assert len(result) == 11  # all links exceed threshold
        for item in result:
            assert item.max_filling > 0.5
            assert item.max_flow == 15.0
            assert item.max_velocity == 5.0
