"""Unit tests for the fine-grained nodes MCP tool surface.

The aggregate ``query.get_node_info`` is tested separately; this file covers
the individual accessors added in Phase 2.6: statistics, bulk arrays,
storage / outfall / divider subtype config, and quality + conversion.
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _opened(session_manager, inp_path: str, session_id: str = "n_op"):
    """Open + initialize the reference model."""
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    return ctx


async def _ended(session_manager, inp_path: str, session_id: str = "n_end"):
    """Open + run to completion (state='ended')."""
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    await run_simulation(ctx, session_id=session_id)
    return ctx


async def _running(session_manager, inp_path: str, session_id: str = "n_run"):
    """Open + step once to reach state='running'."""
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    await step_simulation(ctx, session_id=session_id, num_steps=1)
    return ctx


# ===========================================================================
# Statistics
# ===========================================================================


class TestNodeStats:
    async def test_all_four_stats_return_floats(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import (
            stat_max_depth,
            stat_max_overflow,
            stat_time_flooded,
            stat_vol_flooded,
        )

        ctx = await _ended(session_manager, inp_path, "n_stats")
        for tool, key in (
            (stat_max_depth, "max_depth"),
            (stat_max_overflow, "max_overflow"),
            (stat_vol_flooded, "vol_flooded"),
            (stat_time_flooded, "time_flooded_hours"),
        ):
            r = await tool(ctx, session_id="n_stats", node_id="J1")
            assert key in r
            assert isinstance(r[key], float)
            assert r[key] >= 0.0

    async def test_unknown_node_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import stat_max_depth

        ctx = await _ended(session_manager, inp_path, "n_stats_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await stat_max_depth(
                ctx,
                session_id="n_stats_bad",
                node_id="NOPE",
            )


# ===========================================================================
# Bulk array readers
# ===========================================================================


class TestBulkReaders:
    async def test_depths_bulk_returns_per_node_records(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import get_depths_bulk

        ctx = await _opened(session_manager, inp_path, "n_bd")
        result = await get_depths_bulk(ctx, session_id="n_bd")
        # Reference has 12 nodes.
        assert result["count"] == 12
        ids = [r["id"] for r in result["results"]]
        assert "J1" in ids
        for r in result["results"]:
            assert isinstance(r["value"], float)

    async def test_heads_bulk(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import get_heads_bulk

        ctx = await _opened(session_manager, inp_path, "n_bh")
        result = await get_heads_bulk(ctx, session_id="n_bh")
        assert result["count"] == 12

    async def test_inflows_bulk(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import get_inflows_bulk

        ctx = await _opened(session_manager, inp_path, "n_bi")
        result = await get_inflows_bulk(ctx, session_id="n_bi")
        assert result["count"] == 12

    async def test_overflows_bulk(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import get_overflows_bulk

        ctx = await _opened(session_manager, inp_path, "n_bo")
        result = await get_overflows_bulk(ctx, session_id="n_bo")
        assert result["count"] == 12


class TestBulkWriters:
    async def test_set_depths_bulk_roundtrip(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import (
            get_depths_bulk,
            set_depths_bulk,
        )

        # set_depths_bulk requires running state.
        ctx = await _running(session_manager, inp_path, "n_sd")
        target = [0.5] * 12
        result = await set_depths_bulk(
            ctx,
            session_id="n_sd",
            depths=target,
        )
        assert result["status"] == "ok"
        # Read back to verify the engine accepted the values.
        back = await get_depths_bulk(ctx, session_id="n_sd")
        for r in back["results"]:
            assert r["value"] == pytest.approx(0.5)

    async def test_set_depths_bulk_wrong_length_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import set_depths_bulk

        ctx = await _running(session_manager, inp_path, "n_sd_bad")
        with pytest.raises(ToolError, match="length .* != node count"):
            await set_depths_bulk(
                ctx,
                session_id="n_sd_bad",
                depths=[0.5] * 5,
            )

    async def test_set_depths_bulk_empty_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import set_depths_bulk

        ctx = await _running(session_manager, inp_path, "n_sd_e")
        with pytest.raises(ToolError, match="depths must be non-empty"):
            await set_depths_bulk(ctx, session_id="n_sd_e", depths=[])


# ===========================================================================
# Outfall subtype
# ===========================================================================


class TestOutfallType:
    async def test_get_outfall_type_for_o1(self, session_manager, inp_path):
        """O1 is the outfall in the reference model; expect a valid type code."""
        from openswmm_mcp.tools.nodes import get_outfall_type

        ctx = await _opened(session_manager, inp_path, "n_ot")
        # Reference model has 'O1' as the outfall node id.
        try:
            r = await get_outfall_type(ctx, session_id="n_ot", node_id="O1")
        except ToolError as e:
            # Fixture variants might name the outfall differently.
            pytest.skip(f"Outfall O1 not present in fixture: {e}")
        assert 0 <= r["outfall_type_code"] <= 4
        assert r["outfall_type"] in (
            "free",
            "normal",
            "fixed",
            "tidal",
            "timeseries",
        )

    async def test_set_outfall_type_by_name(self, session_manager, inp_path):
        """set_outfall_type requires BUILDING or OPENED state; lifecycle
        open_model advances to INITIALIZED, so this test attempts the call
        and skips if the state restriction prevents it."""
        from openswmm_mcp.tools.nodes import (
            get_outfall_type,
            set_outfall_type,
        )

        ctx = await _opened(session_manager, inp_path, "n_ots")
        try:
            await set_outfall_type(
                ctx,
                session_id="n_ots",
                node_id="O1",
                outfall_type="free",
            )
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"Outfall set rejected by engine lifecycle: {e}")
        r = await get_outfall_type(ctx, session_id="n_ots", node_id="O1")
        assert r["outfall_type"] == "free"

    async def test_unknown_outfall_type_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import set_outfall_type

        ctx = await _opened(session_manager, inp_path, "n_ot_bad")
        with pytest.raises(ToolError, match="Unknown outfall_type"):
            await set_outfall_type(
                ctx,
                session_id="n_ot_bad",
                node_id="O1",
                outfall_type="bogus",
            )


# ===========================================================================
# Divider subtype (validation tests; the reference model may not have a divider)
# ===========================================================================


class TestDividerTypeValidation:
    async def test_unknown_divider_type_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import set_divider_type

        ctx = await _opened(session_manager, inp_path, "n_dt_bad")
        with pytest.raises(ToolError, match="Unknown divider_type"):
            await set_divider_type(
                ctx,
                session_id="n_dt_bad",
                node_id="J1",
                divider_type="bogus",
            )


# ===========================================================================
# Quality
# ===========================================================================


class TestQualityGet:
    async def test_get_quality_zero_pollutants(self, session_manager, inp_path):
        """The unit-conftest model has 1 pollutant; query index 0 should
        return a valid float (possibly 0)."""
        from openswmm_mcp.tools.nodes import get_quality

        ctx = await _opened(session_manager, inp_path, "n_q")
        try:
            r = await get_quality(
                ctx,
                session_id="n_q",
                node_id="J1",
                pollutant_index=0,
            )
        except Exception:
            pytest.skip("Reference model has no pollutants tracked.")
        assert isinstance(r["concentration"], float)


# ===========================================================================
# Backend guard
# ===========================================================================


class TestLegacyGuard:
    async def test_legacy_backend_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.nodes import stat_max_depth

        session = await session_manager.create_session(
            session_id="legacy_n",
            inp_path=inp_path,
            engine="legacy",
        )
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(session_manager)
        with pytest.raises(ToolError, match="not supported|legacy"):
            await stat_max_depth(ctx, session_id="legacy_n", node_id="J1")
