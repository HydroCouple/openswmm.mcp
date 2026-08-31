"""Unit tests for the ``[PROCESS_COMPONENTS]`` MCP tools.

The bundled ``site_drainage_model.inp`` registers no process components, so the
table starts empty and the register / find / remove round-trip runs against it
directly. The "config path need not exist yet" contract is asserted explicitly:
registering against a path that has never been written must succeed, because
that is the intended order for the create-component-then-write-config flow.
"""

from __future__ import annotations

from openswmm_mcp.errors import ToolError
from tests.unit._base import EngineToolTestCase, MockContext


class _ProcessComponentsTestCase(EngineToolTestCase):
    async def _opened(self, session_id: str) -> MockContext:
        """Open the reference model in the editable 'opened' state.

        Registration and removal are BUILDING/OPENED only.
        """
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(self.session_manager)
        await open_model(
            ctx, inp_path=self.inp_path, session_id=session_id, lenient_open=True
        )
        return ctx

    async def _pc_or_skip(self, session_id: str) -> MockContext:
        from openswmm_mcp.tools.process_components import list_components

        ctx = await self._opened(session_id)
        try:
            await list_components(ctx, session_id=session_id)
        except ToolError as exc:
            self.skipTest(f"process-component registry unavailable (list_components: {exc})")
        return ctx


class TestProcessComponentsRoundTrip(_ProcessComponentsTestCase):
    async def test_register_then_find_then_list(self):
        from openswmm_mcp.tools.process_components import (
            find_component,
            list_components,
            register_component,
        )

        ctx = await self._pc_or_skip("pc_reg")
        await register_component(
            ctx, session_id="pc_reg", component_id="heat", config_path="heat.cfg"
        )

        found = await find_component(ctx, session_id="pc_reg", component_id="heat")
        self.assertTrue(found["found"])
        self.assertEqual(found["id"], "heat")
        self.assertEqual(found["config"], "heat.cfg")

        listed = await list_components(ctx, session_id="pc_reg")
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["components"][0]["id"], "heat")

    async def test_config_path_need_not_exist_yet(self):
        from openswmm_mcp.tools.process_components import find_component, register_component

        ctx = await self._pc_or_skip("pc_missing_cfg")
        # Registering against a file that has never been written is legal --
        # register first, write the config second, resolve at the next open.
        await register_component(
            ctx,
            session_id="pc_missing_cfg",
            component_id="reactions",
            config_path="does/not/exist/yet.rxn",
        )

        found = await find_component(
            ctx, session_id="pc_missing_cfg", component_id="reactions"
        )
        self.assertTrue(found["found"])
        self.assertEqual(found["config"], "does/not/exist/yet.rxn")
        # Nothing has resolved it, so `resolved` is empty rather than a guess.
        self.assertEqual(found["resolved"], "")

    async def test_find_missing_component_reports_not_found(self):
        from openswmm_mcp.tools.process_components import find_component

        ctx = await self._pc_or_skip("pc_absent")
        r = await find_component(ctx, session_id="pc_absent", component_id="nope")
        self.assertFalse(r["found"])
        self.assertEqual(r["index"], -1)

    async def test_remove_component(self):
        from openswmm_mcp.tools.process_components import (
            find_component,
            register_component,
            remove_component,
        )

        ctx = await self._pc_or_skip("pc_rm")
        await register_component(
            ctx, session_id="pc_rm", component_id="water_age", config_path="age.cfg"
        )
        await remove_component(ctx, session_id="pc_rm", component_id="water_age")

        self.assertFalse(
            (await find_component(ctx, session_id="pc_rm", component_id="water_age"))["found"]
        )


class TestProcessComponentsErrors(_ProcessComponentsTestCase):
    async def test_duplicate_id_is_refused(self):
        from openswmm_mcp.tools.process_components import register_component

        ctx = await self._pc_or_skip("pc_dup")
        await register_component(
            ctx, session_id="pc_dup", component_id="heat", config_path="heat.cfg"
        )
        with self.assertRaises(ToolError):
            await register_component(
                ctx, session_id="pc_dup", component_id="heat", config_path="other.cfg"
            )

    async def test_empty_component_id_raises(self):
        from openswmm_mcp.tools.process_components import (
            find_component,
            register_component,
            remove_component,
        )

        ctx = await self._opened("pc_empty")
        with self.assertRaisesRegex(ToolError, "Provide a component_id"):
            await register_component(ctx, session_id="pc_empty", component_id="")
        with self.assertRaisesRegex(ToolError, "Provide a component_id"):
            await find_component(ctx, session_id="pc_empty", component_id="")
        with self.assertRaisesRegex(ToolError, "Provide a component_id"):
            await remove_component(ctx, session_id="pc_empty", component_id="")

    async def test_removing_an_unknown_component_raises(self):
        from openswmm_mcp.tools.process_components import remove_component

        ctx = await self._pc_or_skip("pc_rm_bad")
        with self.assertRaises(ToolError):
            await remove_component(ctx, session_id="pc_rm_bad", component_id="not_registered")

    async def test_registering_after_initialize_is_refused(self):
        from openswmm_mcp.tools.lifecycle import open_model
        from openswmm_mcp.tools.process_components import register_component

        ctx = MockContext(self.session_manager)
        await open_model(ctx, inp_path=self.inp_path, session_id="pc_state")
        with self.assertRaisesRegex(ToolError, "state|requires one of"):
            await register_component(
                ctx, session_id="pc_state", component_id="heat", config_path="heat.cfg"
            )
