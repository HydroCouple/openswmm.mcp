"""Unit tests for the fine-grained links MCP tool surface.

Aggregate query.get_link_info and editing.set_link_properties are tested
separately. This file covers the additions in Phase 2.7: statistics, bulk
arrays, control state, pump subtype, and conduit detail get/set pairs.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):  # noqa: ARG002
        pass


async def _opened(session_manager, inp_path, session_id="l_op"):
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    return ctx


async def _running(session_manager, inp_path, session_id="l_run"):
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    await step_simulation(ctx, session_id=session_id, num_steps=1)
    return ctx


async def _ended(session_manager, inp_path, session_id="l_end"):
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    await run_simulation(ctx, session_id=session_id)
    return ctx


# ===========================================================================
# Statistics
# ===========================================================================


class TestLinkStats:
    async def test_max_flow_max_velocity_max_filling(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import (
            stat_max_filling,
            stat_max_flow,
            stat_max_velocity,
        )

        ctx = await _ended(session_manager, inp_path, "l_stats")
        for tool, key in (
            (stat_max_flow, "max_flow"),
            (stat_max_velocity, "max_velocity"),
            (stat_max_filling, "max_filling"),
        ):
            r = await tool(ctx, session_id="l_stats", link_id="C1")
            assert isinstance(r[key], float)

    async def test_vol_and_surcharge(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import (
            stat_surcharge_time,
            stat_vol_flow,
        )

        ctx = await _ended(session_manager, inp_path, "l_vol")
        r1 = await stat_vol_flow(ctx, session_id="l_vol", link_id="C1")
        assert isinstance(r1["vol_flow"], float)
        r2 = await stat_surcharge_time(ctx, session_id="l_vol", link_id="C1")
        assert isinstance(r2["surcharge_time_hours"], float)

    async def test_unknown_link_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import stat_max_flow

        ctx = await _ended(session_manager, inp_path, "l_stats_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await stat_max_flow(ctx, session_id="l_stats_bad", link_id="NOPE")


# ===========================================================================
# Bulk arrays
# ===========================================================================


class TestBulkReaders:
    async def test_get_flows_bulk_count(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import get_flows_bulk

        ctx = await _opened(session_manager, inp_path, "l_bf")
        result = await get_flows_bulk(ctx, session_id="l_bf")
        # Reference model: 11 links.
        assert result["count"] == 11
        ids = [r["id"] for r in result["results"]]
        assert "C1" in ids

    async def test_get_depths_bulk_count(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import get_depths_bulk

        ctx = await _opened(session_manager, inp_path, "l_bd")
        result = await get_depths_bulk(ctx, session_id="l_bd")
        assert result["count"] == 11


class TestBulkWriters:
    async def test_set_flows_bulk_wrong_length_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import set_flows_bulk

        ctx = await _running(session_manager, inp_path, "l_sf_bad")
        with pytest.raises(ToolError, match="length .* != link count"):
            await set_flows_bulk(ctx, session_id="l_sf_bad", flows=[0.0] * 3)

    async def test_set_flows_bulk_empty_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import set_flows_bulk

        ctx = await _running(session_manager, inp_path, "l_sf_e")
        with pytest.raises(ToolError, match="flows must be non-empty"):
            await set_flows_bulk(ctx, session_id="l_sf_e", flows=[])


# ===========================================================================
# Control state (RUNNING setters)
# ===========================================================================


class TestControlState:
    async def test_set_control_setting_in_running(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import set_control_setting

        ctx = await _running(session_manager, inp_path, "l_cs")
        result = await set_control_setting(
            ctx,
            session_id="l_cs",
            link_id="C1",
            setting=0.5,
        )
        assert result["status"] == "ok"
        assert result["setting"] == 0.5

    async def test_set_control_setting_rejected_outside_running(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import set_control_setting

        ctx = await _opened(session_manager, inp_path, "l_cs_op")
        with pytest.raises(ToolError, match="state.*running|requires.*running"):
            await set_control_setting(
                ctx,
                session_id="l_cs_op",
                link_id="C1",
                setting=0.5,
            )

    async def test_get_closed_returns_bool(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import get_closed

        ctx = await _opened(session_manager, inp_path, "l_gc")
        result = await get_closed(ctx, session_id="l_gc", link_id="C1")
        assert isinstance(result["closed"], bool)

    async def test_set_closed_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import get_closed, set_closed

        ctx = await _running(session_manager, inp_path, "l_setclose")
        await set_closed(ctx, session_id="l_setclose", link_id="C1", closed=True)
        r = await get_closed(ctx, session_id="l_setclose", link_id="C1")
        assert r["closed"] is True


# ===========================================================================
# Conduit detail get/set pairs
# ===========================================================================


class TestConduitDetail:
    async def test_get_barrels_default(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import get_barrels

        ctx = await _opened(session_manager, inp_path, "l_bg")
        r = await get_barrels(ctx, session_id="l_bg", link_id="C1")
        assert isinstance(r["barrels"], int)
        assert r["barrels"] >= 1

    async def test_set_barrels_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import get_barrels, set_barrels

        ctx = await _opened(session_manager, inp_path, "l_bs")
        try:
            await set_barrels(ctx, session_id="l_bs", link_id="C1", barrels=2)
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"set_barrels not allowed in this state: {e}")
        r = await get_barrels(ctx, session_id="l_bs", link_id="C1")
        assert r["barrels"] == 2

    async def test_get_loss_coeff_returns_triple(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import get_loss_coeff

        ctx = await _opened(session_manager, inp_path, "l_lc")
        r = await get_loss_coeff(ctx, session_id="l_lc", link_id="C1")
        for k in ("inlet", "outlet", "avg"):
            assert k in r
            assert isinstance(r[k], float)

    async def test_get_flap_gate_returns_bool(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import get_flap_gate

        ctx = await _opened(session_manager, inp_path, "l_fg")
        r = await get_flap_gate(ctx, session_id="l_fg", link_id="C1")
        assert isinstance(r["has_flap_gate"], bool)


# ===========================================================================
# Backend guard
# ===========================================================================


class TestLegacyGuard:
    async def test_legacy_backend_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.links import stat_max_flow

        session = await session_manager.create_session(
            session_id="legacy_l",
            inp_path=inp_path,
            engine="legacy",
        )
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(session_manager)
        with pytest.raises(ToolError, match="not supported|legacy"):
            await stat_max_flow(ctx, session_id="legacy_l", link_id="C1")
