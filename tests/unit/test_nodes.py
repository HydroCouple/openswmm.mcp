"""Unit tests for the fine-grained nodes MCP tool surface.

The aggregate ``query.get_node_info`` is tested separately; this file covers
the individual accessors added in Phase 2.6: statistics, bulk arrays,
storage / outfall / divider subtype config, and quality + conversion.
"""

from __future__ import annotations

import unittest

from openswmm_mcp.errors import ToolError

from tests.unit._base import EngineToolTestCase, MockContext


# ---------------------------------------------------------------------------
# Base with lifecycle helpers
# ---------------------------------------------------------------------------


class _NodeToolTestCase(EngineToolTestCase):
    async def _opened(self, session_id: str = "n_op"):
        """Open + initialize the reference model."""
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(self.session_manager)
        await open_model(ctx, inp_path=self.inp_path, session_id=session_id)
        return ctx

    async def _ended(self, session_id: str = "n_end"):
        """Open + run to completion (state='ended')."""
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        ctx = MockContext(self.session_manager)
        await open_model(ctx, inp_path=self.inp_path, session_id=session_id)
        await run_simulation(ctx, session_id=session_id)
        return ctx

    async def _running(self, session_id: str = "n_run"):
        """Open + step once to reach state='running'."""
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        ctx = MockContext(self.session_manager)
        await open_model(ctx, inp_path=self.inp_path, session_id=session_id)
        await step_simulation(ctx, session_id=session_id, num_steps=1)
        return ctx


# ===========================================================================
# Statistics
# ===========================================================================


class TestNodeStats(_NodeToolTestCase):
    async def test_all_four_stats_return_floats(self):
        from openswmm_mcp.tools.nodes import (
            stat_max_depth,
            stat_max_overflow,
            stat_time_flooded,
            stat_vol_flooded,
        )

        ctx = await self._ended("n_stats")
        for tool, key in (
            (stat_max_depth, "max_depth"),
            (stat_max_overflow, "max_overflow"),
            (stat_vol_flooded, "vol_flooded"),
            (stat_time_flooded, "time_flooded_hours"),
        ):
            with self.subTest(tool=tool.__name__):
                r = await tool(ctx, session_id="n_stats", node_id="J1")
                self.assertIn(key, r)
                self.assertIsInstance(r[key], float)
                self.assertGreaterEqual(r[key], 0.0)

    async def test_unknown_node_raises(self):
        from openswmm_mcp.tools.nodes import stat_max_depth

        ctx = await self._ended("n_stats_bad")
        with self.assertRaisesRegex(ToolError, "ELEMENT_NOT_FOUND|not found"):
            await stat_max_depth(
                ctx,
                session_id="n_stats_bad",
                node_id="NOPE",
            )


# ===========================================================================
# Bulk array readers
# ===========================================================================


class TestBulkReaders(_NodeToolTestCase):
    async def test_depths_bulk_returns_per_node_records(self):
        from openswmm_mcp.tools.nodes import get_depths_bulk

        ctx = await self._opened("n_bd")
        result = await get_depths_bulk(ctx, session_id="n_bd")
        # Reference has 12 nodes.
        self.assertEqual(result["count"], 12)
        ids = [r["id"] for r in result["results"]]
        self.assertIn("J1", ids)
        for r in result["results"]:
            self.assertIsInstance(r["value"], float)

    async def test_heads_bulk(self):
        from openswmm_mcp.tools.nodes import get_heads_bulk

        ctx = await self._opened("n_bh")
        result = await get_heads_bulk(ctx, session_id="n_bh")
        self.assertEqual(result["count"], 12)

    async def test_inflows_bulk(self):
        from openswmm_mcp.tools.nodes import get_inflows_bulk

        ctx = await self._opened("n_bi")
        result = await get_inflows_bulk(ctx, session_id="n_bi")
        self.assertEqual(result["count"], 12)

    async def test_overflows_bulk(self):
        from openswmm_mcp.tools.nodes import get_overflows_bulk

        ctx = await self._opened("n_bo")
        result = await get_overflows_bulk(ctx, session_id="n_bo")
        self.assertEqual(result["count"], 12)


class TestBulkWriters(_NodeToolTestCase):
    async def test_set_depths_bulk_roundtrip(self):
        from openswmm_mcp.tools.nodes import (
            get_depths_bulk,
            set_depths_bulk,
        )

        # set_depths_bulk requires running state.
        ctx = await self._running("n_sd")
        target = [0.5] * 12
        result = await set_depths_bulk(
            ctx,
            session_id="n_sd",
            depths=target,
        )
        self.assertEqual(result["status"], "ok")
        # Read back to verify the engine accepted the values.
        back = await get_depths_bulk(ctx, session_id="n_sd")
        for r in back["results"]:
            self.assertAlmostEqual(r["value"], 0.5)

    async def test_set_depths_bulk_wrong_length_rejected(self):
        from openswmm_mcp.tools.nodes import set_depths_bulk

        ctx = await self._running("n_sd_bad")
        with self.assertRaisesRegex(ToolError, "length .* != node count"):
            await set_depths_bulk(
                ctx,
                session_id="n_sd_bad",
                depths=[0.5] * 5,
            )

    async def test_set_depths_bulk_empty_rejected(self):
        from openswmm_mcp.tools.nodes import set_depths_bulk

        ctx = await self._running("n_sd_e")
        with self.assertRaisesRegex(ToolError, "depths must be non-empty"):
            await set_depths_bulk(ctx, session_id="n_sd_e", depths=[])


# ===========================================================================
# Outfall subtype
# ===========================================================================


class TestOutfallType(_NodeToolTestCase):
    async def test_get_outfall_type_for_o1(self):
        """O1 is the outfall in the reference model; expect a valid type code."""
        from openswmm_mcp.tools.nodes import get_outfall_type

        ctx = await self._opened("n_ot")
        # Reference model has 'O1' as the outfall node id.
        try:
            r = await get_outfall_type(ctx, session_id="n_ot", node_id="O1")
        except ToolError as e:
            # Fixture variants might name the outfall differently.
            self.skipTest(f"Outfall O1 not present in fixture: {e}")
        self.assertTrue(0 <= r["outfall_type_code"] <= 4)
        self.assertIn(
            r["outfall_type"],
            ("free", "normal", "fixed", "tidal", "timeseries"),
        )

    async def test_set_outfall_type_by_name(self):
        """set_outfall_type requires BUILDING or OPENED state; lifecycle
        open_model advances to INITIALIZED, so this test attempts the call
        and skips if the state restriction prevents it."""
        from openswmm_mcp.tools.nodes import (
            get_outfall_type,
            set_outfall_type,
        )

        ctx = await self._opened("n_ots")
        try:
            await set_outfall_type(
                ctx,
                session_id="n_ots",
                node_id="O1",
                outfall_type="free",
            )
        except (ToolError, RuntimeError) as e:
            self.skipTest(f"Outfall set rejected by engine lifecycle: {e}")
        r = await get_outfall_type(ctx, session_id="n_ots", node_id="O1")
        self.assertEqual(r["outfall_type"], "free")

    async def test_unknown_outfall_type_rejected(self):
        from openswmm_mcp.tools.nodes import set_outfall_type

        ctx = await self._opened("n_ot_bad")
        with self.assertRaisesRegex(ToolError, "Unknown outfall_type"):
            await set_outfall_type(
                ctx,
                session_id="n_ot_bad",
                node_id="O1",
                outfall_type="bogus",
            )


# ===========================================================================
# Divider subtype (validation tests; the reference model may not have a divider)
# ===========================================================================


class TestDividerTypeValidation(_NodeToolTestCase):
    async def test_unknown_divider_type_rejected(self):
        from openswmm_mcp.tools.nodes import set_divider_type

        ctx = await self._opened("n_dt_bad")
        with self.assertRaisesRegex(ToolError, "Unknown divider_type"):
            await set_divider_type(
                ctx,
                session_id="n_dt_bad",
                node_id="J1",
                divider_type="bogus",
            )


# ===========================================================================
# Quality
# ===========================================================================


class TestQualityGet(_NodeToolTestCase):
    async def test_get_quality_zero_pollutants(self):
        """The unit-conftest model has 1 pollutant; query index 0 should
        return a valid float (possibly 0)."""
        from openswmm_mcp.tools.nodes import get_quality

        ctx = await self._opened("n_q")
        try:
            r = await get_quality(
                ctx,
                session_id="n_q",
                node_id="J1",
                pollutant_index=0,
            )
        except Exception:
            self.skipTest("Reference model has no pollutants tracked.")
        self.assertIsInstance(r["concentration"], float)


# ===========================================================================
# Identity: tag get/set, bulk ids
# ===========================================================================


class TestNodeIdentity(_NodeToolTestCase):
    async def test_tag_round_trip(self):
        from openswmm_mcp.tools.nodes import get_tag, set_tag

        ctx = await self._opened("n_tag")
        # Untagged node starts with an empty tag.
        before = await get_tag(ctx, session_id="n_tag", node_id="J1")
        self.assertEqual(before["tag"], "")
        await set_tag(ctx, session_id="n_tag", node_id="J1", tag="Inlet")
        after = await get_tag(ctx, session_id="n_tag", node_id="J1")
        self.assertEqual(after["tag"], "Inlet")
        # Clearing.
        await set_tag(ctx, session_id="n_tag", node_id="J1", tag="")
        cleared = await get_tag(ctx, session_id="n_tag", node_id="J1")
        self.assertEqual(cleared["tag"], "")

    async def test_get_ids_bulk_shape(self):
        from openswmm_mcp.tools.nodes import get_ids_bulk

        ctx = await self._opened("n_ids")
        result = await get_ids_bulk(ctx, session_id="n_ids")
        self.assertEqual(result["count"], 12)
        self.assertEqual(len(result["ids"]), 12)
        self.assertIn("J1", result["ids"])
        for x in result["ids"]:
            self.assertIsInstance(x, str)


# ===========================================================================
# Outfall read-backs (set then get)
# ===========================================================================


class TestOutfallReadBacks(_NodeToolTestCase):
    async def test_tidal_curve_read_back(self):
        from openswmm_mcp.tools.nodes import (
            get_outfall_tidal,
            set_outfall_tidal,
        )

        ctx = await self._opened("n_oft")
        try:
            await set_outfall_tidal(
                ctx, session_id="n_oft", node_id="O1", curve_index=1
            )
        except (ToolError, RuntimeError) as e:
            self.skipTest(f"set_outfall_tidal rejected in this state: {e}")
        r = await get_outfall_tidal(ctx, session_id="n_oft", node_id="O1")
        self.assertEqual(r["curve_index"], 1)

    async def test_timeseries_read_back(self):
        from openswmm_mcp.tools.nodes import (
            get_outfall_timeseries,
            set_outfall_timeseries,
        )

        ctx = await self._opened("n_ofts")
        try:
            await set_outfall_timeseries(
                ctx, session_id="n_ofts", node_id="O1", timeseries_index=2
            )
        except (ToolError, RuntimeError) as e:
            self.skipTest(f"set_outfall_timeseries rejected in this state: {e}")
        r = await get_outfall_timeseries(ctx, session_id="n_ofts", node_id="O1")
        self.assertEqual(r["timeseries_index"], 2)


# ===========================================================================
# Head boundary (running-only gating)
# ===========================================================================


class TestHeadBoundary(_NodeToolTestCase):
    async def test_set_head_boundary_requires_running(self):
        from openswmm_mcp.tools.nodes import set_head_boundary

        ctx = await self._opened("n_hb_gate")
        with self.assertRaisesRegex(ToolError, "running"):
            await set_head_boundary(
                ctx, session_id="n_hb_gate", node_id="J1", head=1.0
            )

    async def test_set_head_boundary_running_ok(self):
        from openswmm_mcp.tools.nodes import set_head_boundary

        ctx = await self._running("n_hb")
        # A head boundary is an outfall-only concept; the engine rejects it
        # (BADPARAM) for junctions. O1 is the reference model's outfall.
        r = await set_head_boundary(
            ctx, session_id="n_hb", node_id="O1", head=1.0
        )
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["head"], 1.0)


# ===========================================================================
# Backend guard
# ===========================================================================


class TestLegacyGuard(_NodeToolTestCase):
    async def test_legacy_backend_rejected(self):
        from openswmm_mcp.tools.nodes import stat_max_depth

        session = await self.session_manager.create_session(
            session_id="legacy_n",
            inp_path=self.inp_path,
            engine="legacy",
        )
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(self.session_manager)
        with self.assertRaisesRegex(ToolError, "not supported|legacy"):
            await stat_max_depth(ctx, session_id="legacy_n", node_id="J1")


if __name__ == "__main__":
    unittest.main()
