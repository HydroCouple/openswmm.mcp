"""Unit tests for the water-age source-table MCP tools (tools/water_age.py).

The bundled ``site_drainage_model.inp`` carries no ``[WATER_AGE_SOURCES]``
section, so engine-touching round-trips are guarded by a probe that skips with
an explicit message when the water-age component is not instantiated. The
validation paths run unconditionally.
"""

from __future__ import annotations

from openswmm_mcp.errors import ToolError
from tests.unit._base import EngineToolTestCase, MockContext


class _WaterAgeToolTestCase(EngineToolTestCase):
    async def _opened(self, session_id: str) -> MockContext:
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(self.session_manager)
        await open_model(
            ctx, inp_path=self.inp_path, session_id=session_id, lenient_open=True
        )
        return ctx

    async def _age_or_skip(self, session_id: str) -> MockContext:
        from openswmm_mcp.tools.water_age import get_config

        ctx = await self._opened(session_id)
        try:
            await get_config(ctx, session_id=session_id)
        except ToolError as exc:
            self.skipTest(
                "reference model carries no water-age configuration "
                f"(water_age_get_config: {exc})"
            )
        return ctx


class TestWaterAgeConfig(_WaterAgeToolTestCase):
    async def test_get_config_lists_every_source_pathway(self):
        from openswmm_mcp.tools.water_age import get_config

        ctx = await self._age_or_skip("wa_cfg")
        cfg = await get_config(ctx, session_id="wa_cfg")

        self.assertIn("enabled", cfg)
        names = {row["source"] for row in cfg["sources"]}
        self.assertEqual(
            names,
            {"rainfall", "dwf", "gw", "rdii", "external_inflow", "iface", "initial_state"},
        )
        for row in cfg["sources"]:
            self.assertIsInstance(row["hours"], float)

    async def test_set_global_source_round_trip(self):
        from openswmm_mcp.tools.water_age import get_config, set_global_source

        ctx = await self._age_or_skip("wa_set")
        await set_global_source(ctx, session_id="wa_set", source="dwf", hours=6.5)

        row = next(
            r for r in (await get_config(ctx, session_id="wa_set"))["sources"]
            if r["source"] == "dwf"
        )
        self.assertAlmostEqual(row["hours"], 6.5, places=6)

    async def test_negative_hours_are_legal(self):
        from openswmm_mcp.tools.water_age import get_config, set_global_source

        ctx = await self._age_or_skip("wa_neg")
        # D-NS1: a negative source age EXTRACTS age-volume; it is a modelling
        # device, not an error, so it must round-trip unchanged.
        await set_global_source(ctx, session_id="wa_neg", source="rainfall", hours=-3.0)

        row = next(
            r for r in (await get_config(ctx, session_id="wa_neg"))["sources"]
            if r["source"] == "rainfall"
        )
        self.assertAlmostEqual(row["hours"], -3.0, places=6)

    async def test_unknown_source_raises(self):
        from openswmm_mcp.tools.water_age import set_global_source

        ctx = await self._opened("wa_bad")
        with self.assertRaisesRegex(ToolError, "Unknown water age source"):
            await set_global_source(ctx, session_id="wa_bad", source="not_a_source", hours=1.0)


class TestWaterAgeNodeOverrides(_WaterAgeToolTestCase):
    async def test_set_and_list_round_trip(self):
        from openswmm_mcp.tools.water_age import list_node_overrides, set_node_override

        ctx = await self._age_or_skip("wa_ovr")
        await set_node_override(
            ctx, session_id="wa_ovr", source="dwf", node_id="J1", hours=4.25
        )
        rows = (await list_node_overrides(ctx, session_id="wa_ovr"))["overrides"]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["source"], "dwf")
        self.assertAlmostEqual(rows[0]["hours"], 4.25, places=6)

    async def test_remove_is_keyed_on_the_source_node_pair(self):
        from openswmm_mcp.tools.water_age import (
            list_node_overrides,
            remove_node_override,
            set_node_override,
        )

        ctx = await self._age_or_skip("wa_ovr_rm")
        await set_node_override(
            ctx, session_id="wa_ovr_rm", source="dwf", node_id="J1", hours=4.0
        )
        await remove_node_override(ctx, session_id="wa_ovr_rm", source="dwf", node_id="J1")
        self.assertEqual((await list_node_overrides(ctx, session_id="wa_ovr_rm"))["count"], 0)

    async def test_removing_a_pair_with_no_row_raises(self):
        from openswmm_mcp.tools.water_age import remove_node_override

        ctx = await self._age_or_skip("wa_ovr_missing")
        with self.assertRaises(ToolError):
            await remove_node_override(
                ctx, session_id="wa_ovr_missing", source="dwf", node_id="J1"
            )

    async def test_only_dwf_and_external_inflow_take_node_scope(self):
        from openswmm_mcp.tools.water_age import set_node_override

        ctx = await self._age_or_skip("wa_ovr_scope")
        # The A1a scope rule, refused exactly as the parser refuses it.
        for source in ("rainfall", "gw", "rdii", "iface", "initial_state"):
            with self.subTest(source=source):
                with self.assertRaises(ToolError):
                    await set_node_override(
                        ctx, session_id="wa_ovr_scope", source=source, node_id="J1", hours=2.0
                    )

    async def test_missing_node_id_raises(self):
        from openswmm_mcp.tools.water_age import set_node_override

        ctx = await self._opened("wa_ovr_bad")
        with self.assertRaisesRegex(ToolError, "Provide a node_id"):
            await set_node_override(ctx, session_id="wa_ovr_bad", source="dwf", node_id="")


class TestWaterAgeSave(_WaterAgeToolTestCase):
    async def test_save_writes_a_component_file(self):
        import os

        from openswmm_mcp.tools.water_age import save_sources, set_global_source

        ctx = await self._age_or_skip("wa_save")
        await set_global_source(ctx, session_id="wa_save", source="dwf", hours=6.0)

        # Written next to the per-test INP copy so a failure leaves the
        # artefact where it can be inspected.
        path = os.path.join(os.path.dirname(self.inp_path), "water_age_sources.txt")
        await save_sources(ctx, session_id="wa_save", path=path)
        self.assertTrue(os.path.isfile(path))

    async def test_empty_path_raises(self):
        from openswmm_mcp.tools.water_age import save_sources

        ctx = await self._opened("wa_save_bad")
        with self.assertRaisesRegex(ToolError, "Provide an output path"):
            await save_sources(ctx, session_id="wa_save_bad", path="")
