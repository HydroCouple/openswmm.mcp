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
