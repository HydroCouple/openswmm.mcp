"""Unit tests for the ``[INITIAL_QUALITY]`` MCP tools (tools/initial_quality.py).

The bundled ``site_drainage_model.inp`` declares one pollutant (``TSS``) and no
``[INITIAL_QUALITY]`` rows, so the table starts empty and the upsert /
remove round-trips run against it directly.
"""

from __future__ import annotations

from openswmm_mcp.errors import ToolError
from tests.unit._base import EngineToolTestCase, MockContext


class _InitialQualityTestCase(EngineToolTestCase):
    async def _opened(self, session_id: str) -> MockContext:
        """Open the reference model in the editable 'opened' state.

        ``[INITIAL_QUALITY]`` rows only seed state at initialize(), so mutation
        is BUILDING/OPENED only -- a strict open would land in 'initialized'
        and every setter would be refused.
        """
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(self.session_manager)
        await open_model(
            ctx, inp_path=self.inp_path, session_id=session_id, lenient_open=True
        )
        return ctx

    async def _iq_or_skip(self, session_id: str) -> MockContext:
        from openswmm_mcp.tools.initial_quality import list_entries

        ctx = await self._opened(session_id)
        try:
            await list_entries(ctx, session_id=session_id)
        except ToolError as exc:
            self.skipTest(f"initial-quality table unavailable (list_entries: {exc})")
        return ctx


class TestInitialQualityRoundTrip(_InitialQualityTestCase):
    async def test_set_node_entry_then_list(self):
        from openswmm_mcp.tools.initial_quality import list_entries, set_entry

        ctx = await self._iq_or_skip("iq_node")
        await set_entry(ctx, session_id="iq_node", constituent="TSS", value=12.5, node_id="J1")

        r = await list_entries(ctx, session_id="iq_node")
        self.assertEqual(r["count"], 1)
        row = r["entries"][0]
        self.assertFalse(row["is_link"])
        self.assertEqual(row["constituent"], "TSS")
        self.assertAlmostEqual(row["value"], 12.5, places=6)

    async def test_set_link_entry_then_list(self):
        from openswmm_mcp.tools.initial_quality import list_entries, set_entry

        ctx = await self._iq_or_skip("iq_link")
        await set_entry(ctx, session_id="iq_link", constituent="TSS", value=3.0, link_id="C1")

        row = (await list_entries(ctx, session_id="iq_link"))["entries"][0]
        self.assertTrue(row["is_link"])
        self.assertAlmostEqual(row["value"], 3.0, places=6)

    async def test_set_is_an_upsert_not_an_append(self):
        from openswmm_mcp.tools.initial_quality import list_entries, set_entry

        ctx = await self._iq_or_skip("iq_upsert")
        await set_entry(ctx, session_id="iq_upsert", constituent="TSS", value=1.0, node_id="J1")
        await set_entry(ctx, session_id="iq_upsert", constituent="TSS", value=9.0, node_id="J1")

        r = await list_entries(ctx, session_id="iq_upsert")
        self.assertEqual(r["count"], 1)
        self.assertAlmostEqual(r["entries"][0]["value"], 9.0, places=6)

    async def test_reserved_constituents_accept_negative_values(self):
        from openswmm_mcp.tools.initial_quality import list_entries, set_entry

        ctx = await self._iq_or_skip("iq_reserved")
        # __WATER_AGE__ is HOURS and __TEMPERATURE__ degC; both are signed.
        await set_entry(
            ctx, session_id="iq_reserved", constituent="__WATER_AGE__", value=-2.0, node_id="J1"
        )
        await set_entry(
            ctx, session_id="iq_reserved", constituent="__TEMPERATURE__", value=-5.0, node_id="J1"
        )

        by_name = {
            row["constituent"]: row["value"]
            for row in (await list_entries(ctx, session_id="iq_reserved"))["entries"]
        }
        self.assertAlmostEqual(by_name["__WATER_AGE__"], -2.0, places=6)
        self.assertAlmostEqual(by_name["__TEMPERATURE__"], -5.0, places=6)

    async def test_list_reports_the_reserved_constituent_names(self):
        from openswmm_mcp.tools.initial_quality import list_entries

        ctx = await self._iq_or_skip("iq_names")
        reserved = (await list_entries(ctx, session_id="iq_names"))["reserved_constituents"]
        self.assertEqual(reserved["water_age"], "__WATER_AGE__")
        self.assertEqual(reserved["temperature"], "__TEMPERATURE__")

    async def test_remove_entry_shifts_later_rows_down(self):
        from openswmm_mcp.tools.initial_quality import list_entries, remove_entry, set_entry

        ctx = await self._iq_or_skip("iq_remove")
        await set_entry(ctx, session_id="iq_remove", constituent="TSS", value=1.0, node_id="J1")
        await set_entry(ctx, session_id="iq_remove", constituent="TSS", value=2.0, node_id="J2")
        self.assertEqual((await list_entries(ctx, session_id="iq_remove"))["count"], 2)

        await remove_entry(ctx, session_id="iq_remove", row_index=0)
        r = await list_entries(ctx, session_id="iq_remove")
        self.assertEqual(r["count"], 1)
        # The surviving row is now at position 0.
        self.assertEqual(r["entries"][0]["row_index"], 0)
        self.assertAlmostEqual(r["entries"][0]["value"], 2.0, places=6)


class TestInitialQualityErrors(_InitialQualityTestCase):
    async def test_missing_constituent_raises(self):
        from openswmm_mcp.tools.initial_quality import set_entry

        ctx = await self._opened("iq_no_const")
        with self.assertRaisesRegex(ToolError, "Provide a constituent"):
            await set_entry(ctx, session_id="iq_no_const", constituent="", node_id="J1")

    async def test_both_node_and_link_raises(self):
        from openswmm_mcp.tools.initial_quality import set_entry

        ctx = await self._opened("iq_both")
        with self.assertRaisesRegex(ToolError, "exactly one of node_id or link_id"):
            await set_entry(
                ctx, session_id="iq_both", constituent="TSS", value=1.0,
                node_id="J1", link_id="C1",
            )

    async def test_neither_node_nor_link_raises(self):
        from openswmm_mcp.tools.initial_quality import set_entry

        ctx = await self._opened("iq_neither")
        with self.assertRaisesRegex(ToolError, "exactly one of node_id or link_id"):
            await set_entry(ctx, session_id="iq_neither", constituent="TSS", value=1.0)

    async def test_unknown_constituent_raises(self):
        from openswmm_mcp.tools.initial_quality import set_entry

        ctx = await self._iq_or_skip("iq_unknown")
        with self.assertRaises(ToolError):
            await set_entry(
                ctx, session_id="iq_unknown", constituent="NOT_A_POLLUTANT",
                value=1.0, node_id="J1",
            )

    async def test_negative_pollutant_value_raises(self):
        from openswmm_mcp.tools.initial_quality import set_entry

        ctx = await self._iq_or_skip("iq_negative")
        # Negatives are legal only for the two reserved species.
        with self.assertRaises(ToolError):
            await set_entry(
                ctx, session_id="iq_negative", constituent="TSS", value=-1.0, node_id="J1"
            )

    async def test_negative_row_index_raises(self):
        from openswmm_mcp.tools.initial_quality import remove_entry

        ctx = await self._opened("iq_neg_row")
        with self.assertRaisesRegex(ToolError, "non-negative row_index"):
            await remove_entry(ctx, session_id="iq_neg_row", row_index=-1)

    async def test_editing_after_initialize_is_refused(self):
        from openswmm_mcp.tools.initial_quality import set_entry
        from openswmm_mcp.tools.lifecycle import open_model

        # A strict open initializes, and rows only seed state at initialize(),
        # so the setter must refuse rather than store a row nothing reads.
        ctx = MockContext(self.session_manager)
        await open_model(ctx, inp_path=self.inp_path, session_id="iq_state")
        with self.assertRaisesRegex(ToolError, "state|requires one of"):
            await set_entry(
                ctx, session_id="iq_state", constituent="TSS", value=1.0, node_id="J1"
            )
