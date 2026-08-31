"""Unit tests for the heat-transport configuration MCP tools (tools/heat.py).

The bundled ``site_drainage_model.inp`` carries no ``[HEAT_FLUXES]`` /
``[HEAT_SOURCES]`` sections, so the engine-touching round-trips are guarded by
a probe that skips with an explicit message when the heat component is not
instantiated. The validation paths -- unknown enum tokens, missing arguments,
contradictory arguments -- are resolved before any engine call and therefore
always run.
"""

from __future__ import annotations

from openswmm_mcp.errors import ToolError
from tests.unit._base import EngineToolTestCase, MockContext


class _HeatToolTestCase(EngineToolTestCase):
    async def _opened(self, session_id: str) -> MockContext:
        """Open the reference model, landing in the editable 'opened' state."""
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(self.session_manager)
        await open_model(
            ctx, inp_path=self.inp_path, session_id=session_id, lenient_open=True
        )
        return ctx

    async def _heat_or_skip(self, session_id: str) -> MockContext:
        """Open a session, skipping when the model has no heat component."""
        from openswmm_mcp.tools.heat import get_config

        ctx = await self._opened(session_id)
        try:
            await get_config(ctx, session_id=session_id)
        except ToolError as exc:
            self.skipTest(
                "reference model carries no heat configuration "
                f"(heat_get_config: {exc})"
            )
        return ctx


class TestHeatConfig(_HeatToolTestCase):
    async def test_get_config_reports_modules_and_shortwave_mode(self):
        from openswmm_mcp.tools.heat import get_config

        ctx = await self._heat_or_skip("h_cfg")
        cfg = await get_config(ctx, session_id="h_cfg")

        for key in (
            "enabled",
            "modules",
            "shortwave_mode",
            "shortwave_mode_code",
            "solar_sited",
            "cloud_configured",
        ):
            self.assertIn(key, cfg)
        # Every flux module is reported, keyed by token name.
        self.assertIn("surface_exchange", cfg["modules"])
        self.assertIn("radiative_exchange", cfg["modules"])
        self.assertIn("layer_conduction", cfg["modules"])
        self.assertIsInstance(cfg["shortwave_mode"], str)
        self.assertIsInstance(cfg["shortwave_mode_code"], int)

    async def test_set_module_round_trip(self):
        from openswmm_mcp.tools.heat import get_config, set_module

        ctx = await self._heat_or_skip("h_mod")
        await set_module(ctx, session_id="h_mod", module="radiative_exchange", on=True)
        self.assertTrue(
            (await get_config(ctx, session_id="h_mod"))["modules"]["radiative_exchange"]
        )

        await set_module(ctx, session_id="h_mod", module="radiative_exchange", on=False)
        self.assertFalse(
            (await get_config(ctx, session_id="h_mod"))["modules"]["radiative_exchange"]
        )

    async def test_unknown_module_raises(self):
        from openswmm_mcp.tools.heat import set_module

        ctx = await self._opened("h_mod_bad")
        with self.assertRaisesRegex(ToolError, "Unknown heat flux module"):
            await set_module(ctx, session_id="h_mod_bad", module="not_a_module")


class TestHeatRadiative(_HeatToolTestCase):
    async def test_set_get_round_trip(self):
        from openswmm_mcp.tools.heat import get_radiative, set_radiative

        ctx = await self._heat_or_skip("h_rad")
        await set_radiative(ctx, session_id="h_rad", param="albedo", value=0.15)
        r = await get_radiative(ctx, session_id="h_rad", param="albedo")
        self.assertAlmostEqual(r["value"], 0.15, places=6)
        self.assertEqual(r["param"], "albedo")
        self.assertIsInstance(r["param_code"], int)

    async def test_get_all_returns_every_parameter(self):
        from openswmm_mcp.tools.heat import get_radiative

        ctx = await self._heat_or_skip("h_rad_all")
        r = await get_radiative(ctx, session_id="h_rad_all")
        for name in ("shortwave", "albedo", "shade_factor", "sky_view", "emiss_water"):
            self.assertIn(name, r["values"])

    async def test_fraction_out_of_range_is_refused_not_clamped(self):
        from openswmm_mcp.tools.heat import get_radiative, set_radiative

        ctx = await self._heat_or_skip("h_rad_rng")
        before = (await get_radiative(ctx, session_id="h_rad_rng", param="albedo"))["value"]
        with self.assertRaises(ToolError):
            await set_radiative(ctx, session_id="h_rad_rng", param="albedo", value=5.0)
        after = (await get_radiative(ctx, session_id="h_rad_rng", param="albedo"))["value"]
        # Refused, not clamped: the refused write left the value untouched.
        self.assertAlmostEqual(before, after, places=9)

    async def test_unknown_param_raises(self):
        from openswmm_mcp.tools.heat import set_radiative

        ctx = await self._opened("h_rad_bad")
        with self.assertRaisesRegex(ToolError, "Unknown heat radiative parameter"):
            await set_radiative(ctx, session_id="h_rad_bad", param="not_a_param", value=0.5)


class TestHeatSolar(_HeatToolTestCase):
    async def test_set_get_round_trip(self):
        from openswmm_mcp.tools.heat import get_solar, set_solar

        ctx = await self._heat_or_skip("h_sol")
        await set_solar(ctx, session_id="h_sol", param="latitude", value=41.5)
        await set_solar(ctx, session_id="h_sol", param="longitude", value=-111.8)

        self.assertAlmostEqual(
            (await get_solar(ctx, session_id="h_sol", param="latitude"))["value"], 41.5, places=6
        )
        self.assertAlmostEqual(
            (await get_solar(ctx, session_id="h_sol", param="longitude"))["value"],
            -111.8,
            places=6,
        )

    async def test_setting_latitude_and_longitude_makes_the_model_sited(self):
        from openswmm_mcp.tools.heat import get_config, set_solar

        ctx = await self._heat_or_skip("h_sited")
        await set_solar(ctx, session_id="h_sited", param="latitude", value=41.5)
        await set_solar(ctx, session_id="h_sited", param="longitude", value=-111.8)
        # solar_sited is the documented precondition for COMPUTED shortwave.
        self.assertTrue((await get_config(ctx, session_id="h_sited"))["solar_sited"])

    async def test_unknown_param_raises(self):
        from openswmm_mcp.tools.heat import set_solar

        ctx = await self._opened("h_sol_bad")
        with self.assertRaisesRegex(ToolError, "Unknown heat solar parameter"):
            await set_solar(ctx, session_id="h_sol_bad", param="not_a_param", value=1.0)


class TestHeatCloud(_HeatToolTestCase):
    async def test_set_get_round_trip_and_marks_configured(self):
        from openswmm_mcp.tools.heat import get_cloud, set_cloud

        ctx = await self._heat_or_skip("h_cloud")
        await set_cloud(ctx, session_id="h_cloud", param="fraction", value=0.4)
        r = await get_cloud(ctx, session_id="h_cloud", param="fraction")
        self.assertAlmostEqual(r["value"], 0.4, places=6)
        # Writing any cloud parameter marks the section configured.
        self.assertTrue(r["configured"])

    async def test_clear_cloud_unconfigures(self):
        from openswmm_mcp.tools.heat import clear_cloud, get_cloud, set_cloud

        ctx = await self._heat_or_skip("h_cloud_clr")
        await set_cloud(ctx, session_id="h_cloud_clr", param="fraction", value=0.4)
        await clear_cloud(ctx, session_id="h_cloud_clr")
        self.assertFalse((await get_cloud(ctx, session_id="h_cloud_clr"))["configured"])

    async def test_fraction_is_a_fraction_not_a_percent(self):
        from openswmm_mcp.tools.heat import set_cloud

        ctx = await self._heat_or_skip("h_cloud_pct")
        with self.assertRaises(ToolError):
            await set_cloud(ctx, session_id="h_cloud_pct", param="fraction", value=40.0)

    async def test_unknown_timeseries_raises(self):
        from openswmm_mcp.tools.heat import set_cloud_timeseries

        ctx = await self._heat_or_skip("h_cloud_ts")
        with self.assertRaises(ToolError):
            await set_cloud_timeseries(
                ctx, session_id="h_cloud_ts", timeseries="NO_SUCH_SERIES"
            )

    async def test_empty_timeseries_name_raises(self):
        from openswmm_mcp.tools.heat import set_cloud_timeseries

        ctx = await self._opened("h_cloud_ts_empty")
        with self.assertRaisesRegex(ToolError, "Provide a timeseries name"):
            await set_cloud_timeseries(ctx, session_id="h_cloud_ts_empty", timeseries="")


class TestHeatShortwaveMode(_HeatToolTestCase):
    async def test_computed_requires_latitude_and_longitude(self):
        from openswmm_mcp.tools.heat import set_shortwave_mode

        ctx = await self._heat_or_skip("h_sw")
        # No latitude/longitude set on the reference model, so COMPUTED must be
        # refused rather than silently borrowing the SNOWMELT latitude.
        with self.assertRaises(ToolError):
            await set_shortwave_mode(ctx, session_id="h_sw", mode="computed")

    async def test_mode_and_contradicting_timeseries_raises(self):
        from openswmm_mcp.tools.heat import set_shortwave_mode

        ctx = await self._heat_or_skip("h_sw_conflict")
        with self.assertRaises(ToolError):
            await set_shortwave_mode(
                ctx, session_id="h_sw_conflict", mode="constant", timeseries="SOME_SERIES"
            )

    async def test_missing_mode_and_timeseries_raises(self):
        from openswmm_mcp.tools.heat import set_shortwave_mode

        ctx = await self._opened("h_sw_empty")
        with self.assertRaisesRegex(ToolError, "Provide a mode"):
            await set_shortwave_mode(ctx, session_id="h_sw_empty")


class TestHeatSources(_HeatToolTestCase):
    async def test_list_sources_covers_the_whole_enum(self):
        from openswmm_mcp.tools.heat import list_sources

        ctx = await self._heat_or_skip("h_src")
        r = await list_sources(ctx, session_id="h_src")
        names = {row["source"] for row in r["sources"]}
        self.assertEqual(
            names,
            {"rainfall", "dwf", "gw", "rdii", "external_inflow", "iface", "initial_state"},
        )
        for row in r["sources"]:
            self.assertIn("temp_c", row)
            self.assertIn("configured", row)

    async def test_set_source_temp_round_trip_and_marks_configured(self):
        from openswmm_mcp.tools.heat import list_sources, set_source_temp

        ctx = await self._heat_or_skip("h_src_set")
        await set_source_temp(ctx, session_id="h_src_set", source="dwf", temp_c=14.5)
        row = next(
            r for r in (await list_sources(ctx, session_id="h_src_set"))["sources"]
            if r["source"] == "dwf"
        )
        self.assertAlmostEqual(row["temp_c"], 14.5, places=6)
        self.assertTrue(row["configured"])

    async def test_clear_source_temp_returns_to_the_default(self):
        from openswmm_mcp.tools.heat import clear_source_temp, list_sources, set_source_temp

        ctx = await self._heat_or_skip("h_src_clr")
        await set_source_temp(ctx, session_id="h_src_clr", source="dwf", temp_c=14.5)
        await clear_source_temp(ctx, session_id="h_src_clr", source="dwf")
        row = next(
            r for r in (await list_sources(ctx, session_id="h_src_clr"))["sources"]
            if r["source"] == "dwf"
        )
        self.assertFalse(row["configured"])
        self.assertAlmostEqual(row["temp_c"], 20.0, places=6)

    async def test_temperature_out_of_range_is_refused(self):
        from openswmm_mcp.tools.heat import set_source_temp

        ctx = await self._heat_or_skip("h_src_rng")
        for bad in (-60.0, 150.0):
            with self.subTest(temp_c=bad):
                with self.assertRaises(ToolError):
                    await set_source_temp(ctx, session_id="h_src_rng", source="dwf", temp_c=bad)

    async def test_unknown_source_raises(self):
        from openswmm_mcp.tools.heat import set_source_temp

        ctx = await self._opened("h_src_bad")
        with self.assertRaisesRegex(ToolError, "Unknown heat source kind"):
            await set_source_temp(ctx, session_id="h_src_bad", source="not_a_source", temp_c=20.0)


class TestHeatNodeOverrides(_HeatToolTestCase):
    async def test_set_and_list_round_trip(self):
        from openswmm_mcp.tools.heat import list_node_overrides, set_node_override

        ctx = await self._heat_or_skip("h_ovr")
        await set_node_override(
            ctx, session_id="h_ovr", source="dwf", node_id="J1", temp_c=17.25
        )
        rows = (await list_node_overrides(ctx, session_id="h_ovr"))["overrides"]
        self.assertTrue(rows)
        self.assertEqual(rows[0]["source"], "dwf")
        self.assertAlmostEqual(rows[0]["temp_c"], 17.25, places=6)

    async def test_setting_the_same_pair_twice_updates_rather_than_duplicates(self):
        from openswmm_mcp.tools.heat import list_node_overrides, set_node_override

        ctx = await self._heat_or_skip("h_ovr_dup")
        await set_node_override(
            ctx, session_id="h_ovr_dup", source="dwf", node_id="J1", temp_c=10.0
        )
        await set_node_override(
            ctx, session_id="h_ovr_dup", source="dwf", node_id="J1", temp_c=12.0
        )
        r = await list_node_overrides(ctx, session_id="h_ovr_dup")
        self.assertEqual(r["count"], 1)
        self.assertAlmostEqual(r["overrides"][0]["temp_c"], 12.0, places=6)

    async def test_remove_by_row_index(self):
        from openswmm_mcp.tools.heat import (
            list_node_overrides,
            remove_node_override,
            set_node_override,
        )

        ctx = await self._heat_or_skip("h_ovr_rm")
        await set_node_override(ctx, session_id="h_ovr_rm", source="dwf", node_id="J1", temp_c=10.0)
        await remove_node_override(ctx, session_id="h_ovr_rm", row_index=0)
        self.assertEqual((await list_node_overrides(ctx, session_id="h_ovr_rm"))["count"], 0)

    async def test_only_dwf_and_external_inflow_take_node_scope(self):
        from openswmm_mcp.tools.heat import set_node_override

        ctx = await self._heat_or_skip("h_ovr_scope")
        # The H1 scope rule: every other source is refused, not deferred.
        for source in ("rainfall", "gw", "rdii", "iface", "initial_state"):
            with self.subTest(source=source):
                with self.assertRaises(ToolError):
                    await set_node_override(
                        ctx, session_id="h_ovr_scope", source=source, node_id="J1", temp_c=15.0
                    )

    async def test_missing_node_id_raises(self):
        from openswmm_mcp.tools.heat import set_node_override

        ctx = await self._opened("h_ovr_bad")
        with self.assertRaisesRegex(ToolError, "Provide a node_id"):
            await set_node_override(ctx, session_id="h_ovr_bad", source="dwf", node_id="")

    async def test_negative_row_index_raises(self):
        from openswmm_mcp.tools.heat import remove_node_override

        ctx = await self._opened("h_ovr_neg")
        with self.assertRaisesRegex(ToolError, "non-negative row_index"):
            await remove_node_override(ctx, session_id="h_ovr_neg", row_index=-1)


class TestHeatEffectiveSourceTemp(_HeatToolTestCase):
    async def test_override_wins_over_the_global(self):
        from openswmm_mcp.tools.heat import (
            get_effective_source_temp,
            set_node_override,
            set_source_temp,
        )

        ctx = await self._heat_or_skip("h_eff")
        await set_source_temp(ctx, session_id="h_eff", source="dwf", temp_c=20.0)
        await set_node_override(ctx, session_id="h_eff", source="dwf", node_id="J1", temp_c=8.0)

        r = await get_effective_source_temp(
            ctx, session_id="h_eff", source="dwf", node_id="J1"
        )
        self.assertAlmostEqual(r["temp_c"], 8.0, places=6)

    async def test_missing_node_id_raises(self):
        from openswmm_mcp.tools.heat import get_effective_source_temp

        ctx = await self._opened("h_eff_bad")
        with self.assertRaisesRegex(ToolError, "Provide a node_id"):
            await get_effective_source_temp(ctx, session_id="h_eff_bad", source="dwf", node_id="")
