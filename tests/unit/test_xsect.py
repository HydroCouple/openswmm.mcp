"""Unit tests for the xsect MCP tool surface.

Covers the stateless cross-section geometry namespace added by
``API_GAP_FILL_PLAN_2026-08-09.md`` Phase P2: the two ways of naming a section
(``link_id`` vs an explicit shape spec), the nine query tools on both the
scalar and the batched (list) path, the tabulated-shape constructors, and the
validation errors.

Numeric expectations are either exact geometry (a 1 ft circular pipe has
``full_area == PI/4``) or relationships that must hold for any shape
(``depth_of_area`` inverts ``area_of_depth``), so nothing here is pinned to a
particular engine build.
"""

from __future__ import annotations

import math
import unittest

from openswmm_mcp.errors import ToolError
from tests.unit._base import EngineToolTestCase, MockContext


class _XSectToolTestCase(EngineToolTestCase):
    async def _opened(self, session_id: str = "xs_op"):
        """Open the reference model (C3 is a 2.25 ft CIRCULAR conduit, CFS)."""
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(self.session_manager)
        await open_model(ctx, inp_path=self.inp_path, session_id=session_id)
        return ctx


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


class TestMetadata(_XSectToolTestCase):
    async def test_list_shapes_matches_engine_enum(self):
        from openswmm.engine import XSectShape

        from openswmm_mcp.tools.xsect import list_shapes

        out = await list_shapes(self.ctx)
        by_name = {s["name"]: s["code"] for s in out["shapes"]}
        self.assertEqual(out["count"], len(XSectShape))
        for member in XSectShape:
            self.assertEqual(by_name[member.name.lower()], int(member))

    async def test_properties_from_shape_spec(self):
        from openswmm_mcp.tools.xsect import properties

        out = await properties(self.ctx, shape="circular", geom1=1.0, units="US")
        self.assertEqual(out["source"], "shape")
        self.assertEqual(out["shape"], "circular")
        self.assertEqual(out["units"], "US")
        self.assertAlmostEqual(out["full_depth"], 1.0, places=6)
        self.assertAlmostEqual(out["full_area"], math.pi / 4.0, delta=0.01)
        self.assertAlmostEqual(out["full_hyd_radius"], 0.25, delta=0.01)
        self.assertAlmostEqual(out["max_width"], 1.0, delta=0.01)
        self.assertFalse(out["is_open"])

    async def test_properties_from_link(self):
        from openswmm_mcp.tools.xsect import properties

        ctx = await self._opened("xs_props")
        out = await properties(ctx, session_id="xs_props", link_id="C3")
        self.assertEqual(out["source"], "link")
        self.assertEqual(out["link_id"], "C3")
        self.assertEqual(out["shape"], "circular")
        self.assertEqual(out["units"], "US")
        self.assertEqual(out["flow_units"], "CFS")
        self.assertAlmostEqual(out["full_depth"], 2.25, places=6)
        self.assertFalse(out["is_open"])

    async def test_open_channel_link_reports_open(self):
        from openswmm_mcp.tools.xsect import properties

        ctx = await self._opened("xs_open")
        # C1 is TRAPEZOIDAL -- an open channel.
        out = await properties(ctx, session_id="xs_open", link_id="C1")
        self.assertTrue(out["is_open"])


# ---------------------------------------------------------------------------
# Queries -- scalar and batched
# ---------------------------------------------------------------------------


class TestDepthQueries(_XSectToolTestCase):
    async def test_area_of_depth_half_full_circle(self):
        from openswmm_mcp.tools.xsect import area_of_depth

        out = await area_of_depth(self.ctx, shape="circular", geom1=1.0, units="US", depth=0.5)
        self.assertEqual(out["depth"], 0.5)
        self.assertAlmostEqual(out["area"], math.pi / 8.0, delta=0.01)

    async def test_area_of_depth_list_uses_batched_path(self):
        from openswmm_mcp.tools.xsect import area_of_depth

        depths = [0.0, 0.25, 0.5, 0.75, 1.0]
        batched = await area_of_depth(
            self.ctx, shape="circular", geom1=1.0, units="US", depth=depths
        )
        self.assertIsInstance(batched["area"], list)
        self.assertEqual(len(batched["area"]), len(depths))
        for d, a in zip(depths, batched["area"]):
            one = await area_of_depth(self.ctx, shape="circular", geom1=1.0, units="US", depth=d)
            self.assertAlmostEqual(one["area"], a, places=9)

    async def test_width_of_depth_peaks_at_mid_depth(self):
        from openswmm_mcp.tools.xsect import width_of_depth

        out = await width_of_depth(
            self.ctx, shape="circular", geom1=1.0, units="US", depth=[0.1, 0.5, 0.9]
        )
        w_low, w_mid, w_high = out["width"]
        self.assertGreater(w_mid, w_low)
        self.assertGreater(w_mid, w_high)
        self.assertAlmostEqual(w_mid, 1.0, delta=0.01)

    async def test_hydrad_of_depth_increases_then_is_positive(self):
        from openswmm_mcp.tools.xsect import hydrad_of_depth

        out = await hydrad_of_depth(
            self.ctx, shape="circular", geom1=1.0, units="US", depth=[0.25, 0.5]
        )
        r_quarter, r_half = out["hyd_radius"]
        self.assertGreater(r_half, r_quarter)
        self.assertAlmostEqual(r_half, 0.25, delta=0.01)


class TestAreaQueries(_XSectToolTestCase):
    async def test_depth_of_area_inverts_area_of_depth(self):
        from openswmm_mcp.tools.xsect import area_of_depth, depth_of_area

        fwd = await area_of_depth(self.ctx, shape="circular", geom1=2.0, units="US", depth=0.8)
        back = await depth_of_area(
            self.ctx, shape="circular", geom1=2.0, units="US", area=fwd["area"]
        )
        self.assertAlmostEqual(back["depth"], 0.8, delta=0.02)

    async def test_hydrad_of_area_agrees_with_hydrad_of_depth(self):
        from openswmm_mcp.tools.xsect import area_of_depth, hydrad_of_area, hydrad_of_depth

        spec = dict(shape="circular", geom1=2.0, units="US")
        a = await area_of_depth(self.ctx, depth=0.8, **spec)
        by_area = await hydrad_of_area(self.ctx, area=a["area"], **spec)
        by_depth = await hydrad_of_depth(self.ctx, depth=0.8, **spec)
        self.assertAlmostEqual(by_area["hyd_radius"], by_depth["hyd_radius"], delta=0.02)

    async def test_sectfactor_round_trips_through_area(self):
        from openswmm_mcp.tools.xsect import area_of_sectfactor, sectfactor_of_area

        spec = dict(shape="circular", geom1=2.0, units="US")
        sf = await sectfactor_of_area(self.ctx, area=1.5, **spec)
        self.assertGreater(sf["section_factor"], 0.0)
        back = await area_of_sectfactor(self.ctx, section_factor=sf["section_factor"], **spec)
        self.assertAlmostEqual(back["area"], 1.5, delta=0.05)

    async def test_dsda_is_positive_below_max_area(self):
        from openswmm_mcp.tools.xsect import dsda

        out = await dsda(self.ctx, shape="circular", geom1=2.0, units="US", area=[0.5, 1.0])
        self.assertEqual(len(out["dsda"]), 2)
        for v in out["dsda"]:
            self.assertGreater(v, 0.0)


class TestCriticalDepth(_XSectToolTestCase):
    async def test_critical_depth_grows_with_flow(self):
        from openswmm_mcp.tools.xsect import critical_depth

        out = await critical_depth(
            self.ctx, shape="circular", geom1=3.0, units="US", flow=[1.0, 5.0, 10.0]
        )
        self.assertEqual(out["flow_units"], "CFS")
        depths = out["critical_depth"]
        self.assertEqual(len(depths), 3)
        self.assertLess(depths[0], depths[1])
        self.assertLess(depths[1], depths[2])
        for d in depths:
            self.assertGreater(d, 0.0)
            self.assertLessEqual(d, 3.0)

    async def test_critical_depth_of_link(self):
        from openswmm_mcp.tools.xsect import critical_depth

        ctx = await self._opened("xs_crit")
        out = await critical_depth(ctx, session_id="xs_crit", link_id="C3", flow=5.0)
        self.assertEqual(out["source"], "link")
        self.assertGreater(out["critical_depth"], 0.0)
        self.assertLessEqual(out["critical_depth"], 2.25)


# ---------------------------------------------------------------------------
# Tabulated-shape constructors
# ---------------------------------------------------------------------------


class TestTabulatedShapes(_XSectToolTestCase):
    async def test_properties_from_transect(self):
        from openswmm_mcp.tools.xsect import properties_from_transect

        out = await properties_from_transect(
            self.ctx,
            stations=[0.0, 10.0, 20.0, 30.0, 40.0],
            elevations=[10.0, 5.0, 0.0, 5.0, 10.0],
            left_bank=10.0,
            right_bank=30.0,
            n_channel=0.03,
            units="US",
        )
        self.assertEqual(out["source"], "transect")
        self.assertEqual(out["station_count"], 5)
        self.assertEqual(out["shape"], "irregular")
        self.assertTrue(out["is_open"])
        self.assertAlmostEqual(out["full_depth"], 10.0, delta=0.01)

    async def test_properties_from_curve(self):
        from openswmm_mcp.tools.xsect import properties_from_curve

        out = await properties_from_curve(
            self.ctx,
            full_depth=2.0,
            curve_depths=[0.0, 0.5, 1.0],
            curve_widths=[0.0, 1.0, 0.5],
            units="US",
        )
        self.assertEqual(out["source"], "curve")
        self.assertEqual(out["point_count"], 3)
        self.assertEqual(out["shape"], "custom")
        self.assertAlmostEqual(out["full_depth"], 2.0, delta=0.01)

    async def test_properties_from_street(self):
        from openswmm_mcp.tools.xsect import properties_from_street

        out = await properties_from_street(
            self.ctx,
            width=20.0,
            curb_height=0.5,
            slope=2.0,
            roughness=0.016,
            units="US",
        )
        self.assertEqual(out["source"], "street")
        self.assertEqual(out["shape"], "street_xsect")
        # xsect::isOpen whitelists only RECT_OPEN, TRAPEZOIDAL, TRIANGULAR,
        # PARABOLIC, POWERFUNC and IRREGULAR -- STREET_XSECT reports closed,
        # matching legacy xsect_isOpen.
        self.assertFalse(out["is_open"])
        self.assertGreater(out["full_area"], 0.0)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation(_XSectToolTestCase):
    async def test_unknown_shape_lists_valid_options(self):
        from openswmm_mcp.tools.xsect import properties

        with self.assertRaises(ToolError) as cm:
            await properties(self.ctx, shape="banana", geom1=1.0, units="US")
        self.assertIn("circular", str(cm.exception))

    async def test_missing_units_is_rejected(self):
        from openswmm_mcp.tools.xsect import properties

        with self.assertRaises(ToolError) as cm:
            await properties(self.ctx, shape="circular", geom1=1.0)
        self.assertIn("US", str(cm.exception))

    async def test_no_section_named(self):
        from openswmm_mcp.tools.xsect import area_of_depth

        with self.assertRaises(ToolError):
            await area_of_depth(self.ctx, depth=0.5)

    async def test_link_and_shape_are_mutually_exclusive(self):
        from openswmm_mcp.tools.xsect import properties

        ctx = await self._opened("xs_both")
        with self.assertRaises(ToolError):
            await properties(
                ctx, session_id="xs_both", link_id="C3", shape="circular", geom1=1.0, units="US"
            )

    async def test_tabulated_shape_cannot_be_built_inline(self):
        from openswmm_mcp.tools.xsect import properties

        with self.assertRaises(ToolError):
            await properties(self.ctx, shape="irregular", geom1=1.0, units="US")

    async def test_empty_transect_is_rejected(self):
        from openswmm_mcp.tools.xsect import properties_from_transect

        with self.assertRaises(ToolError):
            await properties_from_transect(
                self.ctx, stations=[], elevations=[], n_channel=0.03, units="US"
            )

    async def test_unknown_link_is_rejected(self):
        from openswmm_mcp.tools.xsect import properties

        ctx = await self._opened("xs_bad_link")
        with self.assertRaises(ToolError):
            await properties(ctx, session_id="xs_bad_link", link_id="NOPE")


if __name__ == "__main__":
    unittest.main()
