"""Unit tests for the pollutants MCP tool surface.

Covers the dry-weather-flow concentration accessor pair against the real
``openswmm.engine`` in a building-state session. Mirrors the ``MockContext``
+ ``SessionManager`` pattern used by ``tests/unit/test_tables.py``.
"""

from __future__ import annotations

import unittest

from openswmm_mcp.errors import ToolError

from tests.unit._base import EngineToolTestCase, MockContext


class _PollutantToolTestCase(EngineToolTestCase):
    async def _building_with_pollutant(self, session_id: str = "pol", pollutant_id: str = "TSS"):
        """Create a building session with one pollutant and return the ctx."""
        from openswmm_mcp.tools.building import create_model
        from openswmm_mcp.tools.pollutants import add

        ctx = MockContext(self.session_manager)
        await create_model(ctx, session_id=session_id)
        await add(ctx, session_id=session_id, pollutant_id=pollutant_id, units="mg_per_l")
        return ctx


class TestDwfConc(_PollutantToolTestCase):
    async def test_set_then_get_roundtrip(self):
        from openswmm_mcp.tools.pollutants import get_dwf_conc, set_dwf_conc

        ctx = await self._building_with_pollutant("pol_dwf")
        set_result = await set_dwf_conc(
            ctx, session_id="pol_dwf", pollutant_id="TSS", dwf_conc=12.5
        )
        self.assertEqual(set_result["status"], "ok")
        self.assertEqual(set_result["dwf_conc"], 12.5)

        get_result = await get_dwf_conc(ctx, session_id="pol_dwf", pollutant_id="TSS")
        self.assertEqual(get_result["pollutant_id"], "TSS")
        self.assertAlmostEqual(get_result["dwf_conc"], 12.5)

    async def test_unknown_pollutant_rejected(self):
        from openswmm_mcp.tools.pollutants import get_dwf_conc

        ctx = await self._building_with_pollutant("pol_dwf_bad")
        with self.assertRaisesRegex(ToolError, "not found"):
            await get_dwf_conc(ctx, session_id="pol_dwf_bad", pollutant_id="__nope__")


if __name__ == "__main__":
    unittest.main()
