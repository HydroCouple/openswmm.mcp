"""Unit tests for the tables MCP tool surface.

Covers creation (timeseries / curves / patterns), point operations, lookup,
and the state/backend guards. Mirrors the pattern used by
``tests/unit/test_building.py``: a ``MockContext`` plus the ``session_manager``
fixture, with a building-state session set up via ``building.create_model``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError


# ---------------------------------------------------------------------------
# Mock MCP Context
# ---------------------------------------------------------------------------


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):  # noqa: ARG002
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_building_session(session_manager, session_id: str = "tbl"):
    """Create a building session and return the ctx."""
    from openswmm_mcp.tools.building import create_model

    ctx = MockContext(session_manager)
    await create_model(ctx, session_id=session_id)
    return ctx


async def _opened_session(fake_ctx, inp_path: str, session_id: str = "opened"):
    """Open a real .inp session via lifecycle.open_model."""
    from openswmm_mcp.tools.lifecycle import open_model

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)


# ===========================================================================
# Read-only counts (building state, empty model)
# ===========================================================================


class TestCounts:
    async def test_count_empty_building(self, session_manager):
        from openswmm_mcp.tools.tables import count

        ctx = await _create_building_session(session_manager, "tbl_count")
        result = await count(ctx, session_id="tbl_count")
        assert result["session_id"] == "tbl_count"
        assert result["count"] == 0

    async def test_pattern_count_empty_building(self, session_manager):
        from openswmm_mcp.tools.tables import pattern_count

        ctx = await _create_building_session(session_manager, "tbl_pcount")
        result = await pattern_count(ctx, session_id="tbl_pcount")
        assert result["count"] == 0


# ===========================================================================
# Time series creation + population
# ===========================================================================


class TestAddTimeseries:
    async def test_creates_and_populates(self, session_manager):
        from openswmm_mcp.tools.tables import add_timeseries, count, get_point_count

        ctx = await _create_building_session(session_manager, "tbl_ts")
        result = await add_timeseries(
            ctx,
            session_id="tbl_ts",
            ts_id="RainTS",
            times=[0.0, 0.5, 1.0, 1.5],
            values=[0.1, 0.4, 0.8, 0.2],
        )
        assert result["status"] == "ok"
        assert result["id"] == "RainTS"
        assert result["points"] == 4
        assert result["index"] >= 0

        assert (await count(ctx, session_id="tbl_ts"))["count"] == 1
        assert (
            await get_point_count(ctx, session_id="tbl_ts", table_id="RainTS")
        )["count"] == 4

    async def test_empty_id_rejected(self, session_manager):
        from openswmm_mcp.tools.tables import add_timeseries

        ctx = await _create_building_session(session_manager, "tbl_ts_empty")
        with pytest.raises(ToolError, match="ts_id must not be empty"):
            await add_timeseries(
                ctx, session_id="tbl_ts_empty", ts_id="", times=[0.0], values=[1.0],
            )

    async def test_mismatched_lengths_rejected(self, session_manager):
        from openswmm_mcp.tools.tables import add_timeseries

        ctx = await _create_building_session(session_manager, "tbl_ts_mis")
        with pytest.raises(ToolError, match="len\\(times\\)"):
            await add_timeseries(
                ctx,
                session_id="tbl_ts_mis",
                ts_id="TS1",
                times=[0.0, 1.0],
                values=[0.1, 0.2, 0.3],
            )

    async def test_empty_data_rejected(self, session_manager):
        from openswmm_mcp.tools.tables import add_timeseries

        ctx = await _create_building_session(session_manager, "tbl_ts_emp")
        with pytest.raises(ToolError, match="must both be non-empty"):
            await add_timeseries(
                ctx, session_id="tbl_ts_emp", ts_id="TS1", times=[], values=[],
            )


# ===========================================================================
# Curve creation + population + lookup
# ===========================================================================


class TestAddCurve:
    async def test_creates_storage_curve(self, session_manager):
        from openswmm_mcp.tools.tables import add_curve, count

        ctx = await _create_building_session(session_manager, "tbl_curve")
        result = await add_curve(
            ctx,
            session_id="tbl_curve",
            curve_id="StorageCurve",
            curve_type="storage",
            x_values=[0.0, 1.0, 2.0],
            y_values=[0.0, 1.0, 4.0],
        )
        assert result["status"] == "ok"
        assert result["points"] == 3
        assert (await count(ctx, session_id="tbl_curve"))["count"] == 1

    async def test_invalid_curve_type_rejected(self, session_manager):
        from openswmm_mcp.tools.tables import add_curve

        ctx = await _create_building_session(session_manager, "tbl_bad_ct")
        with pytest.raises(ToolError, match="Unknown curve_type"):
            await add_curve(
                ctx,
                session_id="tbl_bad_ct",
                curve_id="X",
                curve_type="bogus",
                x_values=[0.0],
                y_values=[0.0],
            )

    async def test_curve_type_accepts_int(self, session_manager):
        from openswmm_mcp.tools.tables import add_curve

        ctx = await _create_building_session(session_manager, "tbl_int_ct")
        result = await add_curve(
            ctx,
            session_id="tbl_int_ct",
            curve_id="C",
            curve_type=0,  # STORAGE
            x_values=[0.0, 1.0],
            y_values=[0.0, 2.0],
        )
        assert result["status"] == "ok"

    async def test_curve_lookup_linear_interp(self, session_manager):
        from openswmm_mcp.tools.tables import add_curve, lookup

        ctx = await _create_building_session(session_manager, "tbl_lookup")
        await add_curve(
            ctx,
            session_id="tbl_lookup",
            curve_id="LinearCurve",
            curve_type="storage",
            x_values=[0.0, 1.0, 2.0],
            y_values=[0.0, 1.0, 2.0],
        )
        result = await lookup(
            ctx, session_id="tbl_lookup", table_id="LinearCurve", x=0.5,
        )
        assert result["x"] == 0.5
        assert result["y"] == pytest.approx(0.5)


# ===========================================================================
# Point operations
# ===========================================================================


class TestPointOps:
    async def test_get_points_returns_all(self, session_manager):
        from openswmm_mcp.tools.tables import add_curve, get_points

        ctx = await _create_building_session(session_manager, "tbl_gp")
        await add_curve(
            ctx,
            session_id="tbl_gp",
            curve_id="C",
            curve_type="storage",
            x_values=[0.0, 1.0, 2.0],
            y_values=[5.0, 6.0, 7.0],
        )
        result = await get_points(ctx, session_id="tbl_gp", table_id="C")
        assert result["count"] == 3
        assert result["points"] == [[0.0, 5.0], [1.0, 6.0], [2.0, 7.0]]

    async def test_get_point_single(self, session_manager):
        from openswmm_mcp.tools.tables import add_curve, get_point

        ctx = await _create_building_session(session_manager, "tbl_gp1")
        await add_curve(
            ctx,
            session_id="tbl_gp1",
            curve_id="C",
            curve_type="storage",
            x_values=[0.0, 1.0],
            y_values=[10.0, 20.0],
        )
        result = await get_point(ctx, session_id="tbl_gp1", table_id="C", point_index=1)
        assert result["x"] == pytest.approx(1.0)
        assert result["y"] == pytest.approx(20.0)

    async def test_add_point_appends(self, session_manager):
        from openswmm_mcp.tools.tables import (
            add_curve,
            add_point,
            get_point_count,
        )

        ctx = await _create_building_session(session_manager, "tbl_ap")
        await add_curve(
            ctx,
            session_id="tbl_ap",
            curve_id="C",
            curve_type="storage",
            x_values=[0.0],
            y_values=[0.0],
        )
        await add_point(ctx, session_id="tbl_ap", table_id="C", x=1.0, y=2.0)
        result = await get_point_count(ctx, session_id="tbl_ap", table_id="C")
        assert result["count"] == 2

    async def test_clear_points_empties_table(self, session_manager):
        from openswmm_mcp.tools.tables import (
            add_curve,
            clear_points,
            get_point_count,
        )

        ctx = await _create_building_session(session_manager, "tbl_clr")
        await add_curve(
            ctx,
            session_id="tbl_clr",
            curve_id="C",
            curve_type="storage",
            x_values=[0.0, 1.0, 2.0],
            y_values=[0.0, 1.0, 2.0],
        )
        await clear_points(ctx, session_id="tbl_clr", table_id="C")
        result = await get_point_count(ctx, session_id="tbl_clr", table_id="C")
        assert result["count"] == 0


# ===========================================================================
# Patterns
# ===========================================================================


class TestPatterns:
    async def test_pattern_add_monthly_with_factors(self, session_manager):
        from openswmm_mcp.tools.tables import pattern_add, pattern_count

        ctx = await _create_building_session(session_manager, "tbl_pat")
        factors = [1.0, 1.1, 0.9, 1.0, 1.0, 1.2, 1.3, 1.2, 1.0, 0.95, 0.9, 1.0]
        result = await pattern_add(
            ctx,
            session_id="tbl_pat",
            pattern_id="Monthly1",
            pattern_type="monthly",
            factors=factors,
        )
        assert result["status"] == "ok"
        assert result["factors"] == 12
        assert (await pattern_count(ctx, session_id="tbl_pat"))["count"] == 1

    async def test_pattern_type_invalid(self, session_manager):
        from openswmm_mcp.tools.tables import pattern_add

        ctx = await _create_building_session(session_manager, "tbl_pat_bad")
        with pytest.raises(ToolError, match="Unknown pattern_type"):
            await pattern_add(
                ctx,
                session_id="tbl_pat_bad",
                pattern_id="P",
                pattern_type="annually",
            )

    async def test_pattern_set_factors_replaces(self, session_manager):
        from openswmm_mcp.tools.tables import pattern_add, pattern_set_factors

        ctx = await _create_building_session(session_manager, "tbl_psf")
        added = await pattern_add(
            ctx,
            session_id="tbl_psf",
            pattern_id="P",
            pattern_type="monthly",
            factors=[1.0] * 12,
        )
        # Replace with non-trivial factors
        new = [0.5] * 12
        result = await pattern_set_factors(
            ctx, session_id="tbl_psf", pattern_index=added["index"], factors=new,
        )
        assert result["status"] == "ok"
        assert result["factors"] == 12


# ===========================================================================
# State / backend guards
# ===========================================================================


class TestGuards:
    async def test_legacy_backend_rejected(self, session_manager, inp_path):
        """The Tables tools require the openswmm backend; legacy is NOT_SUPPORTED."""
        from openswmm_mcp.tools.tables import count

        session = await session_manager.create_session(
            session_id="legacy_tables",
            inp_path=inp_path,
            engine="legacy",
        )
        # Bring the legacy session to opened state so the guard sees a fully-built session.
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(session_manager)
        with pytest.raises(ToolError, match="not supported|legacy"):
            await count(ctx, session_id="legacy_tables")

    async def test_creation_outside_building_state_rejected(
        self, session_manager, inp_path
    ):
        """add_timeseries / add_curve require the building state."""
        from openswmm_mcp.tools.lifecycle import open_model
        from openswmm_mcp.tools.tables import add_timeseries

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=inp_path, session_id="opened_ts")

        with pytest.raises(ToolError, match="building"):
            await add_timeseries(
                ctx,
                session_id="opened_ts",
                ts_id="X",
                times=[0.0],
                values=[1.0],
            )
