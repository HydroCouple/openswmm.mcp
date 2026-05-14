"""Unit tests for analysis tool functions against the real openswmm engine.

All tests use ``site_drainage_model.inp`` (12 nodes, 11 links, 7 subcatchments,
1 gage, 1 pollutant TSS).  Assertions check structure and sanity ranges, not
hard-coded mock values, since exact statistics depend on the solver run.
"""

from __future__ import annotations

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import (
    CapacitySummaryItem,
    FloodingSummaryItem,
    MassBalanceResult,
    TimeSeries,
)

# Shared helpers
# ---------------------------------------------------------------------------


async def _open_and_run(session_manager, inp_path, session_id="default"):
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    ctx = _Ctx(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    await run_simulation(ctx, session_id=session_id)
    return ctx


async def _open_and_step(session_manager, inp_path, session_id="default"):
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    ctx = _Ctx(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    await step_simulation(ctx, session_id=session_id, num_steps=1)
    return ctx


class _Ctx:
    def __init__(self, sm):
        self.lifespan_context = {"session_manager": sm}

    async def report_progress(self, *_):
        pass


# ---------------------------------------------------------------------------
# get_statistics
# ---------------------------------------------------------------------------


class TestGetStatistics:
    async def test_node_statistics_keys(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_node")
        result = await get_statistics(
            ctx, session_id="stat_node", element_type="node", element_id="J1"
        )
        assert result["element_type"] == "node"
        assert result["element_id"] == "J1"
        assert "max_depth" in result
        assert "max_overflow" in result
        assert "vol_flooded" in result
        assert "time_flooded" in result
        # Values must be finite non-negative floats
        assert isinstance(result["max_depth"], float) and result["max_depth"] >= 0
        assert isinstance(result["vol_flooded"], float) and result["vol_flooded"] >= 0

    async def test_node_statistics_outfall(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_outfall")
        result = await get_statistics(
            ctx, session_id="stat_outfall", element_type="node", element_id="O1"
        )

        assert result["element_id"] == "O1"
        assert result["max_depth"] >= 0

    async def test_link_statistics_keys(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_link")
        result = await get_statistics(
            ctx, session_id="stat_link", element_type="link", element_id="C1"
        )

        assert result["element_type"] == "link"
        assert result["element_id"] == "C1"
        assert "max_flow" in result
        assert "max_velocity" in result
        assert "max_filling" in result
        assert "surcharge_time" in result
        assert result["max_flow"] >= 0
        assert 0.0 <= result["max_filling"] <= 10.0  # filling ratio, should be reasonable

    async def test_subcatchment_statistics(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_sc")
        result = await get_statistics(
            ctx,
            session_id="stat_sc",
            element_type="subcatchment",
            element_id="S1",
        )

        assert result["element_type"] == "subcatchment"
        assert result["element_id"] == "S1"
        assert "max_runoff" in result
        assert result["max_runoff"] >= 0

    async def test_unknown_node_raises(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_bad_node")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_statistics(
                ctx,
                session_id="stat_bad_node",
                element_type="node",
                element_id="NOEXIST",
            )

    async def test_empty_element_id_raises(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_empty")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await get_statistics(ctx, session_id="stat_empty", element_type="node", element_id="")

    async def test_invalid_element_type_raises(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_statistics

        ctx = await _open_and_step(session_manager, tmp_inp, "stat_badtype")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await get_statistics(
                ctx,
                session_id="stat_badtype",
                element_type="bogus",
                element_id="J1",
            )


# ---------------------------------------------------------------------------
# get_mass_balance
# ---------------------------------------------------------------------------


class TestGetMassBalance:
    async def test_mass_balance_after_run(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.analysis import get_mass_balance

        ctx = await _open_and_run(session_manager, tmp_inp, "mb")
        result = await get_mass_balance(ctx, session_id="mb")

        assert isinstance(result, MassBalanceResult)
        assert isinstance(result.runoff_continuity_error, float)
        assert isinstance(result.routing_continuity_error, float)
        assert -5.0 < result.runoff_continuity_error < 5.0
        assert -5.0 < result.routing_continuity_error < 5.0

    async def test_mass_balance_has_pollutant_error(
        self, session_manager, tmp_inp, reference_model
    ):
        from openswmm_mcp.tools.analysis import get_mass_balance

        ctx = await _open_and_run(session_manager, tmp_inp, "mb_qual")
        result = await get_mass_balance(ctx, session_id="mb_qual")

        # site_drainage_model.inp has TSS -> quality error should be populated
        assert result.quality_continuity_error is not None
        assert isinstance(result.quality_continuity_error, float)

    async def test_mass_balance_runoff_total_keys(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_mass_balance

        ctx = await _open_and_run(session_manager, tmp_inp, "mb_keys")
        result = await get_mass_balance(ctx, session_id="mb_keys")

        # RunoffTotal enum members (lowercase keys in result dict)
        assert "rainfall" in result.runoff_total
        assert "evap" in result.runoff_total
        assert "infil" in result.runoff_total
        assert "runoff" in result.runoff_total

    async def test_mass_balance_routing_total_keys(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_mass_balance

        ctx = await _open_and_run(session_manager, tmp_inp, "mb_rt")
        result = await get_mass_balance(ctx, session_id="mb_rt")

        # RoutingTotal enum members
        assert "flooding" in result.routing_total
        assert "outflow" in result.routing_total
        assert "dry_weather" in result.routing_total


# ---------------------------------------------------------------------------
# get_flooding_summary
# ---------------------------------------------------------------------------


class TestGetFloodingSummary:
    async def test_flooding_summary_is_list(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_flooding_summary

        ctx = await _open_and_run(session_manager, tmp_inp, "flood")
        result = await get_flooding_summary(ctx, session_id="flood")

        assert isinstance(result, list)
        assert all(isinstance(r, FloodingSummaryItem) for r in result)

    async def test_flooding_summary_sorted_descending(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_flooding_summary

        ctx = await _open_and_run(session_manager, tmp_inp, "flood_sort")
        result = await get_flooding_summary(ctx, session_id="flood_sort")

        if len(result) > 1:
            vols = [r.total_flood_volume for r in result]
            assert vols == sorted(vols, reverse=True)

    async def test_flooding_summary_fields(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_flooding_summary

        ctx = await _open_and_run(session_manager, tmp_inp, "flood_fields")
        result = await get_flooding_summary(ctx, session_id="flood_fields")

        for item in result:
            assert item.total_flood_volume > 0
            assert item.max_overflow_rate >= 0
            assert item.time_flooded >= 0
            assert item.max_depth >= 0

    async def test_flooding_summary_threshold_excludes_all(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_flooding_summary

        ctx = await _open_and_run(session_manager, tmp_inp, "flood_thr")
        result = await get_flooding_summary(ctx, session_id="flood_thr", min_flood_volume=1e12)

        assert result == []


# ---------------------------------------------------------------------------
# get_capacity_summary
# ---------------------------------------------------------------------------


class TestGetCapacitySummary:
    async def test_capacity_summary_at_zero_threshold(self, session_manager, tmp_inp):
        """At threshold=0.0, all conduits that carried flow should appear."""
        from openswmm_mcp.tools.analysis import get_capacity_summary

        ctx = await _open_and_run(session_manager, tmp_inp, "cap_zero")
        result = await get_capacity_summary(ctx, session_id="cap_zero", max_filling_threshold=0.0)

        assert isinstance(result, list)
        assert all(isinstance(r, CapacitySummaryItem) for r in result)
        # With a 2-yr storm and all conduits active, we expect at least some results
        assert len(result) > 0

    async def test_capacity_summary_fields(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_capacity_summary

        ctx = await _open_and_run(session_manager, tmp_inp, "cap_fields")
        result = await get_capacity_summary(ctx, session_id="cap_fields", max_filling_threshold=0.0)

        for item in result:
            assert item.max_filling >= 0
            assert item.max_flow >= 0
            assert item.max_velocity >= 0
            assert item.time_above_threshold >= 0

    async def test_capacity_summary_sorted_descending(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_capacity_summary

        ctx = await _open_and_run(session_manager, tmp_inp, "cap_sort")
        result = await get_capacity_summary(ctx, session_id="cap_sort", max_filling_threshold=0.0)

        if len(result) > 1:
            fillings = [r.max_filling for r in result]
            assert fillings == sorted(fillings, reverse=True)

    async def test_capacity_summary_high_threshold_reduces_results(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_capacity_summary

        ctx = await _open_and_run(session_manager, tmp_inp, "cap_hi")
        low = await get_capacity_summary(ctx, session_id="cap_hi", max_filling_threshold=0.0)
        high = await get_capacity_summary(ctx, session_id="cap_hi", max_filling_threshold=0.9)

        assert len(high) <= len(low)


# ---------------------------------------------------------------------------
# get_time_series
# ---------------------------------------------------------------------------


class TestGetTimeSeries:
    async def test_node_depth_series(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_time_series

        ctx = await _open_and_run(session_manager, tmp_inp, "ts_node")
        result = await get_time_series(
            ctx, session_id="ts_node", element_type="node", element_id="J1", variable="depth"
        )

        assert isinstance(result, TimeSeries)
        assert result.element_type == "node"
        assert result.element_id == "J1"
        assert result.variable == "depth"
        assert len(result.values) > 0
        assert len(result.timestamps) == len(result.values)
        assert all(isinstance(v, float) for v in result.values)

    async def test_link_flow_series(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_time_series

        ctx = await _open_and_run(session_manager, tmp_inp, "ts_link")
        result = await get_time_series(
            ctx, session_id="ts_link", element_type="link", element_id="C1", variable="flow"
        )

        assert isinstance(result, TimeSeries)
        assert result.element_id == "C1"
        assert len(result.values) > 0

    async def test_subcatchment_runoff_series(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_time_series

        ctx = await _open_and_run(session_manager, tmp_inp, "ts_sc")
        result = await get_time_series(
            ctx, session_id="ts_sc", element_type="subcatchment", element_id="S1", variable="runoff"
        )

        assert isinstance(result, TimeSeries)
        assert result.element_id == "S1"
        assert len(result.values) > 0

    async def test_system_rainfall_series(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_time_series

        ctx = await _open_and_run(session_manager, tmp_inp, "ts_sys")
        result = await get_time_series(
            ctx, session_id="ts_sys", element_type="system", variable="rainfall"
        )

        assert isinstance(result, TimeSeries)
        assert result.element_type == "system"
        assert len(result.values) > 0

    async def test_downsample_reduces_length(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_time_series

        ctx = await _open_and_run(session_manager, tmp_inp, "ts_ds")
        full = await get_time_series(
            ctx,
            session_id="ts_ds",
            element_type="node",
            element_id="J1",
            variable="depth",
            downsample=1,
        )
        downsampled = await get_time_series(
            ctx,
            session_id="ts_ds",
            element_type="node",
            element_id="J1",
            variable="depth",
            downsample=10,
        )

        assert len(downsampled.values) < len(full.values)

    async def test_invalid_node_raises(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_time_series

        ctx = await _open_and_run(session_manager, tmp_inp, "ts_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_time_series(
                ctx, session_id="ts_bad", element_type="node", element_id="NOPE", variable="depth"
            )

    async def test_invalid_variable_raises(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import get_time_series

        ctx = await _open_and_run(session_manager, tmp_inp, "ts_badvar")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await get_time_series(
                ctx, session_id="ts_badvar", element_type="node", element_id="J1", variable="bogus"
            )


# ---------------------------------------------------------------------------
# export_results
# ---------------------------------------------------------------------------


class TestExportResults:
    async def test_export_csv(self, session_manager, tmp_inp, tmp_path):
        from openswmm_mcp.tools.analysis import export_results

        ctx = await _open_and_run(session_manager, tmp_inp, "exp_csv")
        out_file = str(tmp_path / "results.csv")
        result = await export_results(ctx, session_id="exp_csv", output_path=out_file, format="csv")

        import os

        assert result.status == "exported"
        assert result.format == "csv"
        assert result.record_count > 0
        assert os.path.exists(result.path)
        assert os.path.getsize(result.path) > 0

    async def test_export_json(self, session_manager, tmp_inp, tmp_path):
        import json

        from openswmm_mcp.tools.analysis import export_results

        ctx = await _open_and_run(session_manager, tmp_inp, "exp_json")
        out_file = str(tmp_path / "results.json")
        result = await export_results(
            ctx, session_id="exp_json", output_path=out_file, format="json"
        )

        assert result.status == "exported"
        assert result.format == "json"
        with open(result.path) as f:
            data = json.load(f)
        assert "elements" in data
        assert len(data["elements"]) > 0

    async def test_export_missing_path_raises(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import export_results

        ctx = await _open_and_run(session_manager, tmp_inp, "exp_nopath")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await export_results(ctx, session_id="exp_nopath", output_path="", format="csv")

    async def test_export_invalid_format_raises(self, session_manager, tmp_inp, tmp_path):
        from openswmm_mcp.tools.analysis import export_results

        ctx = await _open_and_run(session_manager, tmp_inp, "exp_badfmt")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await export_results(
                ctx, session_id="exp_badfmt", output_path=str(tmp_path / "out.xyz"), format="xyz"
            )


# ---------------------------------------------------------------------------
# compare_scenarios
# ---------------------------------------------------------------------------


class TestCompareScenarios:
    async def test_compare_identical_sessions(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import compare_scenarios
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        ctx = _Ctx(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="cmp_a")
        await open_model(ctx, inp_path=tmp_inp, session_id="cmp_b")
        await run_simulation(ctx, session_id="cmp_a")
        await run_simulation(ctx, session_id="cmp_b")

        result = await compare_scenarios(
            ctx, session_a="cmp_a", session_b="cmp_b", element_type="node", variable="depth"
        )

        assert result["elements_compared"] > 0
        # Identical runs -> all differences should be near zero
        assert abs(result["summary"]["mean_abs_diff"]) < 1e-3

    async def test_compare_missing_session_a_raises(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.analysis import compare_scenarios

        ctx = _Ctx(session_manager)
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await compare_scenarios(
                ctx, session_a="", session_b="b", element_type="node", variable="depth"
            )
