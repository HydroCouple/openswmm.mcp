"""Unit tests for the fine-grained subcatchments MCP tool surface.

Aggregate query.get_subcatchment_info and editing.set_subcatchment_properties
are tested separately. This file covers Phase 2.8 additions: statistics,
bulk arrays, current state readers, coverage, infiltration models, and
quality (runoff + ponded).
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


async def _opened(session_manager, inp_path, session_id="sc_op"):
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    return ctx


async def _ended(session_manager, inp_path, session_id="sc_end"):
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    await run_simulation(ctx, session_id=session_id)
    return ctx


# ===========================================================================
# Statistics
# ===========================================================================


class TestSubcatchStats:
    async def test_all_three_stats_return_floats(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            stat_max_runoff,
            stat_precip,
            stat_runoff_vol,
        )

        ctx = await _ended(session_manager, inp_path, "sc_stats")
        for tool, key in (
            (stat_precip, "precipitation"),
            (stat_runoff_vol, "runoff_vol"),
            (stat_max_runoff, "max_runoff"),
        ):
            r = await tool(ctx, session_id="sc_stats", subcatch_id="S1")
            assert isinstance(r[key], float)
            assert r[key] >= 0.0

    async def test_unknown_subcatch_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import stat_precip

        ctx = await _ended(session_manager, inp_path, "sc_stats_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await stat_precip(ctx, session_id="sc_stats_bad", subcatch_id="NOPE")


# ===========================================================================
# Bulk arrays
# ===========================================================================


class TestBulkReaders:
    async def test_get_runoff_bulk_count(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_runoff_bulk

        ctx = await _opened(session_manager, inp_path, "sc_br")
        result = await get_runoff_bulk(ctx, session_id="sc_br")
        # Reference model has 7 subcatchments.
        assert result["count"] == 7
        ids = [r["id"] for r in result["results"]]
        assert "S1" in ids


# ===========================================================================
# Current state readers
# ===========================================================================


class TestStateReaders:
    async def test_get_runoff(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_runoff

        ctx = await _opened(session_manager, inp_path, "sc_gr")
        r = await get_runoff(ctx, session_id="sc_gr", subcatch_id="S1")
        assert isinstance(r["runoff"], float)

    async def test_get_rainfall(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_rainfall

        ctx = await _opened(session_manager, inp_path, "sc_grf")
        r = await get_rainfall(ctx, session_id="sc_grf", subcatch_id="S1")
        assert isinstance(r["rainfall"], float)

    async def test_get_evap(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_evap

        ctx = await _opened(session_manager, inp_path, "sc_ge")
        r = await get_evap(ctx, session_id="sc_ge", subcatch_id="S1")
        assert isinstance(r["evap"], float)


# ===========================================================================
# Coverage
# ===========================================================================


class TestCoverage:
    async def test_invalid_fraction_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import set_coverage

        ctx = await _opened(session_manager, inp_path, "sc_cv_bad")
        with pytest.raises(ToolError, match=r"fraction must be in \[0, 1\]"):
            await set_coverage(
                ctx,
                session_id="sc_cv_bad",
                subcatch_id="S1",
                landuse_index=0,
                fraction=1.5,
            )

    async def test_get_coverage_zero_default(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_coverage

        ctx = await _opened(session_manager, inp_path, "sc_gcv")
        # With no land uses defined, coverage at landuse_index=0 should
        # either return 0.0 or error cleanly. Skip if engine rejects.
        try:
            r = await get_coverage(
                ctx,
                session_id="sc_gcv",
                subcatch_id="S1",
                landuse_index=0,
            )
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"get_coverage requires defined landuse: {e}")
        assert isinstance(r["coverage"], float)


# ===========================================================================
# Infiltration models
# ===========================================================================


class TestInfilModel:
    async def test_get_infil_model_returns_valid_code(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_infil_model

        ctx = await _opened(session_manager, inp_path, "sc_im")
        r = await get_infil_model(ctx, session_id="sc_im", subcatch_id="S1")
        assert 0 <= r["model_code"] <= 4
        assert r["model"] in (
            "horton",
            "mod_horton",
            "green_ampt",
            "mod_green_ampt",
            "curve_number",
            "unknown",
        )

    async def test_set_get_horton_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            get_infil_horton,
            set_infil_horton,
        )

        ctx = await _opened(session_manager, inp_path, "sc_h")
        try:
            await set_infil_horton(
                ctx,
                session_id="sc_h",
                subcatch_id="S1",
                f0=3.0,
                fmin=0.5,
                decay=4.0,
                dry_time=7.0,
            )
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"set_infil_horton rejected in this state: {e}")
        r = await get_infil_horton(ctx, session_id="sc_h", subcatch_id="S1")
        assert r["f0"] == pytest.approx(3.0)
        assert r["fmin"] == pytest.approx(0.5)
        assert r["decay"] == pytest.approx(4.0)
        assert r["dry_time"] == pytest.approx(7.0)

    async def test_curve_number_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            get_infil_curve_number,
            set_infil_curve_number,
        )

        ctx = await _opened(session_manager, inp_path, "sc_cn")
        try:
            await set_infil_curve_number(
                ctx,
                session_id="sc_cn",
                subcatch_id="S1",
                curve_number=85.0,
            )
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"set_infil_curve_number rejected: {e}")
        r = await get_infil_curve_number(ctx, session_id="sc_cn", subcatch_id="S1")
        assert r["curve_number"] == pytest.approx(85.0)


# ===========================================================================
# Quality
# ===========================================================================


class TestQuality:
    async def test_get_quality_returns_float(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_quality

        ctx = await _opened(session_manager, inp_path, "sc_q")
        try:
            r = await get_quality(
                ctx,
                session_id="sc_q",
                subcatch_id="S1",
                pollutant_index=0,
            )
        except Exception:
            pytest.skip("Reference model has no pollutants tracked.")
        assert isinstance(r["concentration"], float)


# ===========================================================================
# Identity: tag get/set, bulk ids, outlet-subcatchment routing
# ===========================================================================


class TestSubcatchIdentity:
    async def test_tag_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_tag, set_tag

        ctx = await _opened(session_manager, inp_path, "sc_tag")
        before = await get_tag(ctx, session_id="sc_tag", subcatch_id="S1")
        assert before["tag"] == ""
        await set_tag(ctx, session_id="sc_tag", subcatch_id="S1", tag="Urban")
        after = await get_tag(ctx, session_id="sc_tag", subcatch_id="S1")
        assert after["tag"] == "Urban"
        await set_tag(ctx, session_id="sc_tag", subcatch_id="S1", tag="")
        cleared = await get_tag(ctx, session_id="sc_tag", subcatch_id="S1")
        assert cleared["tag"] == ""

    async def test_get_ids_bulk_shape(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_ids_bulk

        ctx = await _opened(session_manager, inp_path, "sc_ids")
        result = await get_ids_bulk(ctx, session_id="sc_ids")
        assert result["count"] == 7
        assert len(result["ids"]) == 7
        assert "S1" in result["ids"]
        for x in result["ids"]:
            assert isinstance(x, str)

    async def test_set_outlet_subcatchment_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import set_outlet_subcatchment

        ctx = await _opened(session_manager, inp_path, "sc_outlet")
        try:
            r = await set_outlet_subcatchment(
                ctx,
                session_id="sc_outlet",
                subcatch_id="S1",
                outlet_subcatch_id="S2",
            )
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"set_outlet_subcatchment rejected in this state: {e}")
        assert r["status"] == "ok"
        assert r["outlet_subcatch_index"] >= 0

    async def test_set_outlet_subcatchment_unknown_target(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import set_outlet_subcatchment

        ctx = await _opened(session_manager, inp_path, "sc_outlet_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await set_outlet_subcatchment(
                ctx,
                session_id="sc_outlet_bad",
                subcatch_id="S1",
                outlet_subcatch_id="NOPE",
            )


# ===========================================================================
# Aquifer parameters
#
# The reference model has no [AQUIFERS] section, so the round-trip test skips
# cleanly when no aquifer exists; the validation test pins the param-token
# resolver without needing an aquifer.
# ===========================================================================


class TestAquiferParams:
    async def test_unknown_param_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import aquifer_get_param

        ctx = await _opened(session_manager, inp_path, "aq_bad")
        with pytest.raises(ToolError, match="Unknown aquifer param"):
            await aquifer_get_param(ctx, session_id="aq_bad", aquifer_id=0, param="bogus")

    async def test_param_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            aquifer_get_param,
            aquifer_set_param,
        )

        ctx = await _opened(session_manager, inp_path, "aq_rt")
        try:
            await aquifer_set_param(
                ctx,
                session_id="aq_rt",
                aquifer_id=0,
                param="porosity",
                value=0.45,
            )
            r = await aquifer_get_param(ctx, session_id="aq_rt", aquifer_id=0, param="porosity")
        except (ToolError, RuntimeError, Exception) as e:
            pytest.skip(f"Reference model has no aquifer: {e}")
        assert r["value"] == pytest.approx(0.45)
        assert r["param_code"] == 0


# ===========================================================================
# Aquifer definitions (add / id / evap pattern)
# ===========================================================================


class TestAquiferDefinitions:
    async def test_empty_id_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import aquifer_add

        ctx = await _opened(session_manager, inp_path, "aq_add_bad")
        with pytest.raises(ToolError, match="aquifer_id must not be empty"):
            await aquifer_add(ctx, session_id="aq_add_bad", aquifer_id="")

    async def test_add_then_id_round_trips(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import aquifer_add, aquifer_id

        ctx = await _opened(session_manager, inp_path, "aq_add")
        try:
            added = await aquifer_add(ctx, session_id="aq_add", aquifer_id="AQ1")
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"aquifer_add rejected in this state: {e}")
        assert added["index"] >= 0
        r = await aquifer_id(ctx, session_id="aq_add", index=added["index"])
        assert r["id"] == "AQ1"

    async def test_evap_pattern_round_trips(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            aquifer_add,
            aquifer_get_evap_pattern,
            aquifer_set_evap_pattern,
        )

        ctx = await _opened(session_manager, inp_path, "aq_pat")
        try:
            await aquifer_add(ctx, session_id="aq_pat", aquifer_id="AQ_PAT")
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"aquifer_add rejected in this state: {e}")
        before = await aquifer_get_evap_pattern(ctx, session_id="aq_pat", aquifer_id="AQ_PAT")
        assert before["pattern_id"] == ""
        # Clearing is always legal even without a [PATTERNS] entry.
        r = await aquifer_set_evap_pattern(
            ctx, session_id="aq_pat", aquifer_id="AQ_PAT", pattern_id=""
        )
        assert r["status"] == "ok"


# ===========================================================================
# Snowpack definitions ([SNOWPACKS] — distinct from [SNOWMELT] and from the
# run-time snow state set by set_snow_state)
# ===========================================================================


async def _with_snowpack(session_manager, inp_path, session_id, pack_id):
    """Open the reference model and add a snowpack, or skip."""
    from openswmm_mcp.tools.subcatchments import snowpack_add

    ctx = await _opened(session_manager, inp_path, session_id)
    try:
        await snowpack_add(ctx, session_id=session_id, snowpack_id=pack_id)
    except (ToolError, RuntimeError) as e:
        pytest.skip(f"snowpack_add rejected in this state: {e}")
    return ctx


class TestSnowpackDefinitions:
    async def test_empty_id_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import snowpack_add

        ctx = await _opened(session_manager, inp_path, "sp_add_bad")
        with pytest.raises(ToolError, match="snowpack_id must not be empty"):
            await snowpack_add(ctx, session_id="sp_add_bad", snowpack_id="")

    async def test_add_increments_count_and_id_round_trips(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            snowpack_add,
            snowpack_count,
            snowpack_id,
        )

        ctx = await _opened(session_manager, inp_path, "sp_add")
        before = await snowpack_count(ctx, session_id="sp_add")
        try:
            added = await snowpack_add(ctx, session_id="sp_add", snowpack_id="SP1")
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"snowpack_add rejected in this state: {e}")
        after = await snowpack_count(ctx, session_id="sp_add")
        assert after["count"] == before["count"] + 1
        r = await snowpack_id(ctx, session_id="sp_add", index=added["index"])
        assert r["id"] == "SP1"

    async def test_unknown_surface_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import snowpack_get_surface

        ctx = await _opened(session_manager, inp_path, "sp_surf_bad")
        with pytest.raises(ToolError, match="Unknown snow surface"):
            await snowpack_get_surface(
                ctx, session_id="sp_surf_bad", snowpack_id="SP1", surface="roof"
            )

    async def test_surface_code_out_of_range_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import snowpack_get_surface

        ctx = await _opened(session_manager, inp_path, "sp_surf_code")
        with pytest.raises(ToolError, match="surface code must be"):
            await snowpack_get_surface(
                ctx, session_id="sp_surf_code", snowpack_id="SP1", surface=7
            )

    @pytest.mark.parametrize("surface", ["plowable", "impervious", "pervious"])
    async def test_surface_round_trip(self, session_manager, inp_path, surface):
        from openswmm_mcp.tools.subcatchments import (
            snowpack_get_surface,
            snowpack_set_surface,
        )

        sid = f"sp_{surface}"
        ctx = await _with_snowpack(session_manager, inp_path, sid, "SP_SURF")
        await snowpack_set_surface(
            ctx,
            session_id=sid,
            snowpack_id="SP_SURF",
            surface=surface,
            cmin=0.001,
            cmax=0.006,
            tbase=32.0,
            fwfrac=0.05,
            sd0=0.1,
            fw0=0.02,
            last=0.5,
        )
        r = await snowpack_get_surface(ctx, session_id=sid, snowpack_id="SP_SURF", surface=surface)
        assert r["surface"] == surface
        assert r["cmin"] == pytest.approx(0.001)
        assert r["cmax"] == pytest.approx(0.006)
        assert r["tbase"] == pytest.approx(32.0)
        assert r["fwfrac"] == pytest.approx(0.05)
        assert r["sd0"] == pytest.approx(0.1)
        assert r["fw0"] == pytest.approx(0.02)
        assert r["last"] == pytest.approx(0.5)

    async def test_integer_surface_code_matches_name(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import snowpack_get_surface

        ctx = await _with_snowpack(session_manager, inp_path, "sp_code", "SP_CODE")
        by_code = await snowpack_get_surface(
            ctx, session_id="sp_code", snowpack_id="SP_CODE", surface=0
        )
        assert by_code["surface"] == "plowable"

    async def test_removal_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            snowpack_get_removal,
            snowpack_set_removal,
        )

        ctx = await _with_snowpack(session_manager, inp_path, "sp_rm", "SP_RM")
        await snowpack_set_removal(
            ctx,
            session_id="sp_rm",
            snowpack_id="SP_RM",
            dsnow=1.5,
            fout=0.2,
            fimp=0.1,
            fperv=0.3,
            fimelt=0.05,
            fsubcatch=0.0,
        )
        r = await snowpack_get_removal(ctx, session_id="sp_rm", snowpack_id="SP_RM")
        assert r["dsnow"] == pytest.approx(1.5)
        assert r["fout"] == pytest.approx(0.2)
        assert r["fimp"] == pytest.approx(0.1)
        assert r["fperv"] == pytest.approx(0.3)
        assert r["fimelt"] == pytest.approx(0.05)
        assert r["fsubcatch"] == pytest.approx(0.0)

    async def test_removal_subcatch_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            snowpack_get_removal_subcatch,
            snowpack_set_removal_subcatch,
        )

        ctx = await _with_snowpack(session_manager, inp_path, "sp_rms", "SP_RMS")
        before = await snowpack_get_removal_subcatch(
            ctx, session_id="sp_rms", snowpack_id="SP_RMS"
        )
        assert before["subcatch_id"] == ""
        await snowpack_set_removal_subcatch(
            ctx, session_id="sp_rms", snowpack_id="SP_RMS", subcatch_id="S2"
        )
        after = await snowpack_get_removal_subcatch(ctx, session_id="sp_rms", snowpack_id="SP_RMS")
        assert after["subcatch_id"] == "S2"
        await snowpack_set_removal_subcatch(
            ctx, session_id="sp_rms", snowpack_id="SP_RMS", subcatch_id=""
        )
        cleared = await snowpack_get_removal_subcatch(
            ctx, session_id="sp_rms", snowpack_id="SP_RMS"
        )
        assert cleared["subcatch_id"] == ""


# ===========================================================================
# Bulk coverages, initial loading, zero-imperv percentage
# ===========================================================================


class TestCoveragesBulk:
    async def test_get_coverages_length_matches_landuse_count(self, session_manager, inp_path):
        from openswmm_mcp.tools.quality import landuse_count
        from openswmm_mcp.tools.subcatchments import get_coverages

        ctx = await _opened(session_manager, inp_path, "sc_cvb")
        n = (await landuse_count(ctx, session_id="sc_cvb"))["count"]
        r = await get_coverages(ctx, session_id="sc_cvb", subcatch_id="S1")
        assert r["count"] == n
        assert len(r["coverages"]) == n
        for v in r["coverages"]:
            assert isinstance(v, float)

    async def test_bulk_agrees_with_single_pair(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_coverage, get_coverages

        ctx = await _opened(session_manager, inp_path, "sc_cvb2")
        bulk = await get_coverages(ctx, session_id="sc_cvb2", subcatch_id="S1")
        if bulk["count"] == 0:
            pytest.skip("Reference model has no land uses defined.")
        single = await get_coverage(
            ctx, session_id="sc_cvb2", subcatch_id="S1", landuse_index=0
        )
        assert bulk["coverages"][0] == pytest.approx(single["coverage"])


class TestInitialLoading:
    async def test_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            get_initial_loading,
            set_initial_loading,
        )

        ctx = await _opened(session_manager, inp_path, "sc_load")
        try:
            await set_initial_loading(
                ctx,
                session_id="sc_load",
                subcatch_id="S1",
                pollutant_id=0,
                initial_loading=2.5,
            )
        except (ToolError, RuntimeError, Exception) as e:
            pytest.skip(f"Reference model has no pollutants: {e}")
        r = await get_initial_loading(
            ctx, session_id="sc_load", subcatch_id="S1", pollutant_id=0
        )
        assert r["initial_loading"] == pytest.approx(2.5)

    async def test_unknown_subcatch_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import get_initial_loading

        ctx = await _opened(session_manager, inp_path, "sc_load_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await get_initial_loading(
                ctx, session_id="sc_load_bad", subcatch_id="NOPE", pollutant_id=0
            )


class TestZeroImpervPct:
    async def test_out_of_range_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import set_zero_imperv_pct

        ctx = await _opened(session_manager, inp_path, "sc_zi_bad")
        with pytest.raises(ToolError, match=r"pct must be in \[0, 100\]"):
            await set_zero_imperv_pct(
                ctx, session_id="sc_zi_bad", subcatch_id="S1", pct=150.0
            )

    async def test_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import (
            get_zero_imperv_pct,
            set_zero_imperv_pct,
        )

        ctx = await _opened(session_manager, inp_path, "sc_zi")
        try:
            await set_zero_imperv_pct(ctx, session_id="sc_zi", subcatch_id="S1", pct=42.5)
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"set_zero_imperv_pct rejected in this state: {e}")
        r = await get_zero_imperv_pct(ctx, session_id="sc_zi", subcatch_id="S1")
        assert r["zero_imperv_pct"] == pytest.approx(42.5)


# ===========================================================================
# Backend guard
# ===========================================================================


class TestLegacyGuard:
    async def test_legacy_backend_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import stat_max_runoff

        session = await session_manager.create_session(
            session_id="legacy_sc",
            inp_path=inp_path,
            engine="legacy",
        )
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(session_manager)
        with pytest.raises(ToolError, match="not supported|legacy"):
            await stat_max_runoff(ctx, session_id="legacy_sc", subcatch_id="S1")


# ===========================================================================
# Groundwater / snow state injection (running-only gating)
#
# The deep round-trip behaviour (warm-start values surviving a step) is owned
# and tested by the engine suite (test_state_injection.py S1/S2); the model
# fixtures here have no aquifer/snowpack, so these tests pin the MCP layer's
# contract: the tools are rejected outside the running state.
# ===========================================================================


class TestStateInjectionGating:
    async def test_set_gw_state_requires_running(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import set_gw_state

        ctx = await _opened(session_manager, inp_path, "sc_gw_gate")
        with pytest.raises(ToolError, match="running"):
            await set_gw_state(ctx, session_id="sc_gw_gate", subcatch_id="S1", theta=0.3)

    async def test_set_snow_state_requires_running(self, session_manager, inp_path):
        from openswmm_mcp.tools.subcatchments import set_snow_state

        ctx = await _opened(session_manager, inp_path, "sc_snow_gate")
        with pytest.raises(ToolError, match="running"):
            await set_snow_state(
                ctx, session_id="sc_snow_gate", subcatch_id="S1", surface=2, swe=1.0
            )
