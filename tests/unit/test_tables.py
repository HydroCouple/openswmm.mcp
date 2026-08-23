"""Unit tests for the tables MCP tool surface.

Covers creation (timeseries / curves / patterns), point operations, lookup,
and the state/backend guards. Mirrors the pattern used by
``tests/unit/test_building.py``: a ``MockContext`` plus a ``SessionManager``,
with a building-state session set up via ``building.create_model``.
"""

from __future__ import annotations

import unittest

from openswmm_mcp.errors import ToolError
from tests.unit._base import EngineToolTestCase, MockContext

# ---------------------------------------------------------------------------
# Base with building-session helper
# ---------------------------------------------------------------------------


class _TableToolTestCase(EngineToolTestCase):
    async def _building(self, session_id: str = "tbl"):
        """Create a building session and return the ctx."""
        from openswmm_mcp.tools.building import create_model

        ctx = MockContext(self.session_manager)
        await create_model(ctx, session_id=session_id)
        return ctx


# ===========================================================================
# Read-only counts (building state, empty model)
# ===========================================================================


class TestCounts(_TableToolTestCase):
    async def test_count_empty_building(self):
        from openswmm_mcp.tools.tables import count

        ctx = await self._building("tbl_count")
        result = await count(ctx, session_id="tbl_count")
        self.assertEqual(result["session_id"], "tbl_count")
        self.assertEqual(result["count"], 0)

    async def test_pattern_count_empty_building(self):
        from openswmm_mcp.tools.tables import pattern_count

        ctx = await self._building("tbl_pcount")
        result = await pattern_count(ctx, session_id="tbl_pcount")
        self.assertEqual(result["count"], 0)


# ===========================================================================
# Time series creation + population
# ===========================================================================


class TestAddTimeseries(_TableToolTestCase):
    async def test_creates_and_populates(self):
        from openswmm_mcp.tools.tables import add_timeseries, count, get_point_count

        ctx = await self._building("tbl_ts")
        result = await add_timeseries(
            ctx,
            session_id="tbl_ts",
            ts_id="RainTS",
            times=[0.0, 0.5, 1.0, 1.5],
            values=[0.1, 0.4, 0.8, 0.2],
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["id"], "RainTS")
        self.assertEqual(result["points"], 4)
        self.assertGreaterEqual(result["index"], 0)

        self.assertEqual((await count(ctx, session_id="tbl_ts"))["count"], 1)
        self.assertEqual(
            (await get_point_count(ctx, session_id="tbl_ts", table_id="RainTS"))["count"],
            4,
        )

    async def test_empty_id_rejected(self):
        from openswmm_mcp.tools.tables import add_timeseries

        ctx = await self._building("tbl_ts_empty")
        with self.assertRaisesRegex(ToolError, "ts_id must not be empty"):
            await add_timeseries(
                ctx,
                session_id="tbl_ts_empty",
                ts_id="",
                times=[0.0],
                values=[1.0],
            )

    async def test_mismatched_lengths_rejected(self):
        from openswmm_mcp.tools.tables import add_timeseries

        ctx = await self._building("tbl_ts_mis")
        with self.assertRaisesRegex(ToolError, r"len\(times\)"):
            await add_timeseries(
                ctx,
                session_id="tbl_ts_mis",
                ts_id="TS1",
                times=[0.0, 1.0],
                values=[0.1, 0.2, 0.3],
            )

    async def test_empty_data_rejected(self):
        from openswmm_mcp.tools.tables import add_timeseries

        ctx = await self._building("tbl_ts_emp")
        with self.assertRaisesRegex(ToolError, "must both be non-empty"):
            await add_timeseries(
                ctx,
                session_id="tbl_ts_emp",
                ts_id="TS1",
                times=[],
                values=[],
            )


# ===========================================================================
# Curve creation + population + lookup
# ===========================================================================


class TestAddCurve(_TableToolTestCase):
    async def test_creates_storage_curve(self):
        from openswmm_mcp.tools.tables import add_curve, count

        ctx = await self._building("tbl_curve")
        result = await add_curve(
            ctx,
            session_id="tbl_curve",
            curve_id="StorageCurve",
            curve_type="storage",
            x_values=[0.0, 1.0, 2.0],
            y_values=[0.0, 1.0, 4.0],
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["points"], 3)
        self.assertEqual((await count(ctx, session_id="tbl_curve"))["count"], 1)

    async def test_invalid_curve_type_rejected(self):
        from openswmm_mcp.tools.tables import add_curve

        ctx = await self._building("tbl_bad_ct")
        with self.assertRaisesRegex(ToolError, "Unknown curve_type"):
            await add_curve(
                ctx,
                session_id="tbl_bad_ct",
                curve_id="X",
                curve_type="bogus",
                x_values=[0.0],
                y_values=[0.0],
            )

    async def test_curve_type_accepts_int(self):
        from openswmm_mcp.tools.tables import add_curve

        ctx = await self._building("tbl_int_ct")
        result = await add_curve(
            ctx,
            session_id="tbl_int_ct",
            curve_id="C",
            curve_type=0,  # STORAGE
            x_values=[0.0, 1.0],
            y_values=[0.0, 2.0],
        )
        self.assertEqual(result["status"], "ok")

    async def test_curve_lookup_linear_interp(self):
        from openswmm_mcp.tools.tables import add_curve, lookup

        ctx = await self._building("tbl_lookup")
        await add_curve(
            ctx,
            session_id="tbl_lookup",
            curve_id="LinearCurve",
            curve_type="storage",
            x_values=[0.0, 1.0, 2.0],
            y_values=[0.0, 1.0, 2.0],
        )
        result = await lookup(
            ctx,
            session_id="tbl_lookup",
            table_id="LinearCurve",
            x=0.5,
        )
        self.assertEqual(result["x"], 0.5)
        self.assertAlmostEqual(result["y"], 0.5)


# ===========================================================================
# Table type
# ===========================================================================


class TestGetType(_TableToolTestCase):
    async def test_storage_curve_type(self):
        from openswmm_mcp.tools.tables import add_curve, get_type

        ctx = await self._building("tbl_type")
        await add_curve(
            ctx,
            session_id="tbl_type",
            curve_id="StorageCurve",
            curve_type="storage",
            x_values=[0.0, 1.0, 2.0],
            y_values=[0.0, 1.0, 4.0],
        )
        result = await get_type(ctx, session_id="tbl_type", table_id="StorageCurve")
        self.assertEqual(result["id"], "StorageCurve")
        self.assertIn("type", result)
        self.assertIsInstance(result["type_code"], int)

    async def test_empty_id_rejected(self):
        from openswmm_mcp.tools.tables import get_type

        ctx = await self._building("tbl_type_empty")
        with self.assertRaisesRegex(ToolError, "table_id must not be empty"):
            await get_type(ctx, session_id="tbl_type_empty", table_id="")


# ===========================================================================
# Point operations
# ===========================================================================


class TestPointOps(_TableToolTestCase):
    async def test_get_points_returns_all(self):
        from openswmm_mcp.tools.tables import add_curve, get_points

        ctx = await self._building("tbl_gp")
        await add_curve(
            ctx,
            session_id="tbl_gp",
            curve_id="C",
            curve_type="storage",
            x_values=[0.0, 1.0, 2.0],
            y_values=[5.0, 6.0, 7.0],
        )
        result = await get_points(ctx, session_id="tbl_gp", table_id="C")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["points"], [[0.0, 5.0], [1.0, 6.0], [2.0, 7.0]])

    async def test_get_point_single(self):
        from openswmm_mcp.tools.tables import add_curve, get_point

        ctx = await self._building("tbl_gp1")
        await add_curve(
            ctx,
            session_id="tbl_gp1",
            curve_id="C",
            curve_type="storage",
            x_values=[0.0, 1.0],
            y_values=[10.0, 20.0],
        )
        result = await get_point(ctx, session_id="tbl_gp1", table_id="C", point_index=1)
        self.assertAlmostEqual(result["x"], 1.0)
        self.assertAlmostEqual(result["y"], 20.0)

    async def test_add_point_appends(self):
        from openswmm_mcp.tools.tables import (
            add_curve,
            add_point,
            get_point_count,
        )

        ctx = await self._building("tbl_ap")
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
        self.assertEqual(result["count"], 2)

    async def test_clear_points_empties_table(self):
        from openswmm_mcp.tools.tables import (
            add_curve,
            clear_points,
            get_point_count,
        )

        ctx = await self._building("tbl_clr")
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
        self.assertEqual(result["count"], 0)


# ===========================================================================
# Patterns
# ===========================================================================


class TestPatterns(_TableToolTestCase):
    async def test_pattern_add_monthly_with_factors(self):
        from openswmm_mcp.tools.tables import pattern_add, pattern_count

        ctx = await self._building("tbl_pat")
        factors = [1.0, 1.1, 0.9, 1.0, 1.0, 1.2, 1.3, 1.2, 1.0, 0.95, 0.9, 1.0]
        result = await pattern_add(
            ctx,
            session_id="tbl_pat",
            pattern_id="Monthly1",
            pattern_type="monthly",
            factors=factors,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["factors"], 12)
        self.assertEqual((await pattern_count(ctx, session_id="tbl_pat"))["count"], 1)

    async def test_pattern_type_invalid(self):
        from openswmm_mcp.tools.tables import pattern_add

        ctx = await self._building("tbl_pat_bad")
        with self.assertRaisesRegex(ToolError, "Unknown pattern_type"):
            await pattern_add(
                ctx,
                session_id="tbl_pat_bad",
                pattern_id="P",
                pattern_type="annually",
            )

    async def test_pattern_remove(self):
        from openswmm_mcp.tools.tables import pattern_add, pattern_count, pattern_remove

        ctx = await self._building("tbl_prm")
        await pattern_add(
            ctx,
            session_id="tbl_prm",
            pattern_id="ToRemove",
            pattern_type="monthly",
            factors=[1.0] * 12,
        )
        self.assertEqual((await pattern_count(ctx, session_id="tbl_prm"))["count"], 1)
        result = await pattern_remove(ctx, session_id="tbl_prm", pattern_id="ToRemove")
        self.assertEqual(result["status"], "ok")
        self.assertEqual((await pattern_count(ctx, session_id="tbl_prm"))["count"], 0)

    async def test_pattern_remove_empty_id_rejected(self):
        from openswmm_mcp.tools.tables import pattern_remove

        ctx = await self._building("tbl_prm_empty")
        with self.assertRaisesRegex(ToolError, "pattern_id must not be empty"):
            await pattern_remove(ctx, session_id="tbl_prm_empty", pattern_id="")

    async def test_pattern_set_factors_replaces(self):
        from openswmm_mcp.tools.tables import pattern_add, pattern_set_factors

        ctx = await self._building("tbl_psf")
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
            ctx,
            session_id="tbl_psf",
            pattern_index=added["index"],
            factors=new,
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["factors"], 12)


# ===========================================================================
# State / backend guards
# ===========================================================================


class TestGuards(_TableToolTestCase):
    async def test_legacy_backend_rejected(self):
        """The Tables tools require the openswmm backend; legacy is NOT_SUPPORTED."""
        from openswmm_mcp.tools.tables import count

        session = await self.session_manager.create_session(
            session_id="legacy_tables",
            inp_path=self.inp_path,
            engine="legacy",
        )
        # Bring the legacy session to opened state so the guard sees a fully-built session.
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(self.session_manager)
        with self.assertRaisesRegex(ToolError, "not supported|legacy"):
            await count(ctx, session_id="legacy_tables")

    async def test_creation_outside_building_state_rejected(self):
        """add_timeseries / add_curve require the building state."""
        from openswmm_mcp.tools.lifecycle import open_model
        from openswmm_mcp.tools.tables import add_timeseries

        ctx = MockContext(self.session_manager)
        await open_model(ctx, inp_path=self.inp_path, session_id="opened_ts")

        with self.assertRaisesRegex(ToolError, "building"):
            await add_timeseries(
                ctx,
                session_id="opened_ts",
                ts_id="X",
                times=[0.0],
                values=[1.0],
            )


if __name__ == "__main__":
    unittest.main()
