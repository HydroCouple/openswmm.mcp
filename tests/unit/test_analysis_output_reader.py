"""Unit tests for the Phase 2.5 output reader tools in ``analysis``.

These cover the per-period metadata getters, the snapshot ("attribute")
readers, and the single-period system getter that complement the existing
``analysis.get_time_series`` tool.

All tools require ``state='ended'``, so each test runs the reference
simulation to completion via ``lifecycle.run_simulation`` before reading.
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


async def _run_to_ended(session_manager, inp_path: str, session_id: str = "out"):
    """Open + run the reference .inp to completion. Returns ctx + session."""
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    await run_simulation(ctx, session_id=session_id)
    session = await session_manager.get_session(session_id)
    assert session.state == "ended"
    return ctx


# ===========================================================================
# Metadata / counts
# ===========================================================================


class TestOutputMetadata:
    async def test_metadata_fields_present(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_metadata

        ctx = await _run_to_ended(session_manager, inp_path, "om_meta")
        result = await output_metadata(ctx, session_id="om_meta")
        # Reference site_drainage_example.inp has 12 nodes, 11 links, 7 subcatchments.
        assert result["node_count"] == 12
        assert result["link_count"] == 11
        assert result["subcatchment_count"] == 7
        assert result["period_count"] > 0
        assert result["report_step_seconds"] > 0
        assert result["start_date"] > 0
        # Version + units codes are non-negative integers.
        assert result["version"] >= 0
        assert result["flow_units_code"] >= 0


class TestOutputPeriodCount:
    async def test_period_count_matches_metadata(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_metadata, output_period_count

        ctx = await _run_to_ended(session_manager, inp_path, "om_pc")
        meta = await output_metadata(ctx, session_id="om_pc")
        pc = await output_period_count(ctx, session_id="om_pc")
        assert pc["period_count"] == meta["period_count"]
        assert pc["period_count"] > 0


class TestOutputPollutantCount:
    async def test_pollutant_count_matches_reference(self, session_manager, inp_path):
        """The unit-test reference (``site_drainage_model.inp``) has 1
        pollutant (TSS); the top-level conftest fixture
        (``site_drainage_example.inp``) has 0. Both share this test path
        — assert it's a non-negative integer and matches what metadata
        reports."""
        from openswmm_mcp.tools.analysis import (
            output_metadata,
            output_pollutant_count,
        )

        ctx = await _run_to_ended(session_manager, inp_path, "om_poll")
        result = await output_pollutant_count(ctx, session_id="om_poll")
        meta = await output_metadata(ctx, session_id="om_poll")
        assert isinstance(result["pollutant_count"], int)
        assert result["pollutant_count"] >= 0
        assert result["pollutant_count"] == meta["pollutant_count"]


# ===========================================================================
# Period time
# ===========================================================================


class TestOutputPeriodTime:
    async def test_period_time_monotonic(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_period_time

        ctx = await _run_to_ended(session_manager, inp_path, "om_pt")
        r0 = await output_period_time(ctx, session_id="om_pt", period=0)
        r1 = await output_period_time(ctx, session_id="om_pt", period=1)
        assert r0["elapsed_time"] < r1["elapsed_time"]

    async def test_period_time_out_of_range_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_period_count, output_period_time

        ctx = await _run_to_ended(session_manager, inp_path, "om_pt_bad")
        n = (await output_period_count(ctx, session_id="om_pt_bad"))["period_count"]
        with pytest.raises(ToolError, match="period must be in"):
            await output_period_time(ctx, session_id="om_pt_bad", period=n)
        with pytest.raises(ToolError, match="period must be in"):
            await output_period_time(ctx, session_id="om_pt_bad", period=-1)


# ===========================================================================
# Per-object attribute readers (single object, all variables, one period)
# ===========================================================================


class TestNodeAttribute:
    async def test_node_attribute_returns_base_variables(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_node_attribute

        ctx = await _run_to_ended(session_manager, inp_path, "om_na")
        result = await output_node_attribute(
            ctx,
            session_id="om_na",
            node_id="J1",
            period=0,
        )
        attrs = result["attributes"]
        for k in ("depth", "head", "volume", "lateral_inflow", "total_inflow", "overflow"):
            assert k in attrs
            assert isinstance(attrs[k], float)

    async def test_node_attribute_unknown_node_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_node_attribute

        ctx = await _run_to_ended(session_manager, inp_path, "om_na_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await output_node_attribute(
                ctx,
                session_id="om_na_bad",
                node_id="NOPE",
                period=0,
            )

    async def test_node_attribute_empty_id_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_node_attribute

        ctx = await _run_to_ended(session_manager, inp_path, "om_na_e")
        with pytest.raises(ToolError, match="node_id must not be empty"):
            await output_node_attribute(
                ctx,
                session_id="om_na_e",
                node_id="",
                period=0,
            )


class TestLinkAttribute:
    async def test_link_attribute_returns_base_variables(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_link_attribute

        ctx = await _run_to_ended(session_manager, inp_path, "om_la")
        result = await output_link_attribute(
            ctx,
            session_id="om_la",
            link_id="C1",
            period=0,
        )
        attrs = result["attributes"]
        for k in ("flow", "depth", "velocity", "volume", "capacity"):
            assert k in attrs
            assert isinstance(attrs[k], float)


class TestSubcatchAttribute:
    async def test_subcatch_attribute_returns_base_variables(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_subcatch_attribute

        ctx = await _run_to_ended(session_manager, inp_path, "om_sa")
        result = await output_subcatch_attribute(
            ctx,
            session_id="om_sa",
            subcatch_id="S1",
            period=0,
        )
        attrs = result["attributes"]
        for k in (
            "rainfall",
            "snow_depth",
            "evap",
            "infil",
            "runoff",
            "gw_flow",
            "gw_elev",
            "soil_moist",
        ):
            assert k in attrs


# ===========================================================================
# System single-period result
# ===========================================================================


class TestOutputSystemResult:
    async def test_system_rainfall_at_first_period(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_system_result

        ctx = await _run_to_ended(session_manager, inp_path, "om_sr")
        result = await output_system_result(
            ctx,
            session_id="om_sr",
            variable="rainfall",
            period=0,
        )
        assert "value" in result
        assert isinstance(result["value"], float)

    async def test_unknown_variable_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_system_result

        ctx = await _run_to_ended(session_manager, inp_path, "om_sr_bad")
        with pytest.raises(ToolError, match="Unknown system variable"):
            await output_system_result(
                ctx,
                session_id="om_sr_bad",
                variable="bogus",
                period=0,
            )


# ===========================================================================
# State + backend guards
# ===========================================================================


class TestStateGuard:
    async def test_attribute_outside_ended_rejected(self, session_manager, inp_path):
        """The output reader tools all require state='ended'."""
        from openswmm_mcp.tools.analysis import output_period_count
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=inp_path, session_id="om_state")
        # Session is now 'initialized', not 'ended'.
        with pytest.raises(ToolError, match="state.*ended|requires.*ended"):
            await output_period_count(ctx, session_id="om_state")


# ===========================================================================
# Per-period array readers (Phase 2.5 follow-up)
# ===========================================================================


class TestOutputNodeResults:
    async def test_node_results_returns_all_nodes(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_node_results

        ctx = await _run_to_ended(session_manager, inp_path, "om_nr")
        result = await output_node_results(
            ctx,
            session_id="om_nr",
            variable="depth",
            period=0,
        )
        # Reference model has 12 nodes.
        assert result["count"] == 12
        ids = [r["id"] for r in result["results"]]
        assert "J1" in ids
        for r in result["results"]:
            assert isinstance(r["value"], float)

    async def test_unknown_variable_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_node_results

        ctx = await _run_to_ended(session_manager, inp_path, "om_nr_bad")
        with pytest.raises(ToolError, match="Unknown node variable"):
            await output_node_results(
                ctx,
                session_id="om_nr_bad",
                variable="bogus",
                period=0,
            )

    async def test_period_out_of_range_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import (
            output_node_results,
            output_period_count,
        )

        ctx = await _run_to_ended(session_manager, inp_path, "om_nr_p")
        n = (await output_period_count(ctx, session_id="om_nr_p"))["period_count"]
        with pytest.raises(ToolError, match="period must be in"):
            await output_node_results(
                ctx,
                session_id="om_nr_p",
                variable="depth",
                period=n,
            )


class TestOutputLinkResults:
    async def test_link_results_returns_all_links(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_link_results

        ctx = await _run_to_ended(session_manager, inp_path, "om_lr")
        result = await output_link_results(
            ctx,
            session_id="om_lr",
            variable="flow",
            period=0,
        )
        assert result["count"] == 11
        ids = [r["id"] for r in result["results"]]
        assert "C1" in ids


class TestOutputSubcatchResults:
    async def test_subcatch_results_returns_all_subcatchments(self, session_manager, inp_path):
        from openswmm_mcp.tools.analysis import output_subcatch_results

        ctx = await _run_to_ended(session_manager, inp_path, "om_sr_all")
        result = await output_subcatch_results(
            ctx,
            session_id="om_sr_all",
            variable="runoff",
            period=0,
        )
        assert result["count"] == 7
        ids = [r["id"] for r in result["results"]]
        assert "S1" in ids
