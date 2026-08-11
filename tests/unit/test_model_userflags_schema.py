"""model.userflag_* schema/value tools and model.file_path_* slot tools.

Runs against the real ``openswmm.engine`` (no mocks), per
``openswmm.engine/docs/API_GAP_CLOSURE_PLAN_2026-06-10.md`` Phase B.3.
Skips cleanly when the installed engine build predates the user-flag
schema bindings.
"""

from __future__ import annotations

import pytest

eng = pytest.importorskip("openswmm.engine")
if not hasattr(eng.ModelBuilder, "define_userflag"):
    pytest.skip(
        "openswmm.engine build predates the user-flag schema bindings (rebuild the engine wheel)",
        allow_module_level=True,
    )

from openswmm_mcp.errors import ToolError  # noqa: E402
from openswmm_mcp.tools.model import (  # noqa: E402
    file_path_get,
    file_path_set,
    userflag_clear_value,
    userflag_define,
    userflag_get_value,
    userflag_list_defs,
    userflag_set_value,
    userflag_undefine,
)


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):
        pass


async def _building_session(session_manager, session_id="bld_flags"):
    """Create a BUILDING session with one node so values have an owner."""
    from openswmm_mcp.tools.building import add_node, create_model

    ctx = MockContext(session_manager)
    await create_model(ctx, session_id=session_id)
    await add_node(ctx, session_id=session_id, node_id="J1", node_type="junction")
    return ctx


async def _opened_session(session_manager, inp_path, session_id="opened_flags"):
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    return ctx


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------


class TestUserflagSchema:
    async def test_define_list_undefine_building(self, session_manager):
        ctx = await _building_session(session_manager)
        out = await userflag_define(
            ctx,
            session_id="bld_flags",
            name="priority",
            flag_type="INTEGER",
            description="Asset priority",
        )
        assert out["status"] == "ok"
        assert out["name"] == "PRIORITY"
        assert out["flag_type"] == "INTEGER"

        listed = await userflag_list_defs(ctx, session_id="bld_flags")
        assert listed["count"] == 1
        entry = listed["definitions"][0]
        assert entry == {
            "name": "PRIORITY",
            "flag_type": "INTEGER",
            "description": "Asset priority",
        }

        await userflag_undefine(ctx, session_id="bld_flags", name="priority")
        listed = await userflag_list_defs(ctx, session_id="bld_flags")
        assert listed["count"] == 0

    async def test_define_on_opened_solver(self, session_manager, inp_path):
        ctx = await _opened_session(session_manager, inp_path)
        await userflag_define(ctx, session_id="opened_flags", name="reviewed", flag_type="BOOLEAN")
        listed = await userflag_list_defs(ctx, session_id="opened_flags")
        names = [d["name"] for d in listed["definitions"]]
        assert "REVIEWED" in names

    async def test_invalid_type_raises(self, session_manager):
        ctx = await _building_session(session_manager, session_id="bld_badtype")
        with pytest.raises(ToolError):
            await userflag_define(ctx, session_id="bld_badtype", name="x", flag_type="COMPLEX")

    async def test_empty_name_raises(self, session_manager):
        ctx = await _building_session(session_manager, session_id="bld_noname")
        with pytest.raises(ToolError):
            await userflag_define(ctx, session_id="bld_noname", name="", flag_type="REAL")


# ---------------------------------------------------------------------------
# Per-object values
# ---------------------------------------------------------------------------


class TestUserflagValues:
    async def test_value_roundtrip(self, session_manager):
        ctx = await _building_session(session_manager, session_id="bld_vals")
        await userflag_define(ctx, session_id="bld_vals", name="rank", flag_type="INTEGER")
        await userflag_set_value(
            ctx,
            session_id="bld_vals",
            obj_type="NODE",
            obj_name="J1",
            flag_name="rank",
            value="3",
        )
        got = await userflag_get_value(
            ctx,
            session_id="bld_vals",
            obj_type="NODE",
            obj_name="J1",
            flag_name="rank",
        )
        assert got["assigned"] is True
        assert got["value"] == "3"

        await userflag_clear_value(
            ctx,
            session_id="bld_vals",
            obj_type="NODE",
            obj_name="J1",
            flag_name="rank",
        )
        got = await userflag_get_value(
            ctx,
            session_id="bld_vals",
            obj_type="NODE",
            obj_name="J1",
            flag_name="rank",
        )
        assert got["assigned"] is False
        assert got["value"] is None

    async def test_set_undefined_flag_raises(self, session_manager):
        ctx = await _building_session(session_manager, session_id="bld_undef")
        with pytest.raises(Exception):
            await userflag_set_value(
                ctx,
                session_id="bld_undef",
                obj_type="NODE",
                obj_name="J1",
                flag_name="ghost",
                value="1",
            )

    async def test_missing_args_raise(self, session_manager):
        ctx = await _building_session(session_manager, session_id="bld_args")
        with pytest.raises(ToolError):
            await userflag_get_value(
                ctx, session_id="bld_args", obj_type="", obj_name="J1", flag_name="x"
            )


# ---------------------------------------------------------------------------
# Typed external-file path slots
# ---------------------------------------------------------------------------


class TestFilePathSlots:
    async def test_scalar_roundtrip(self, session_manager):
        ctx = await _building_session(session_manager, session_id="bld_paths")
        out = await file_path_set(ctx, session_id="bld_paths", role="rainfall", new_path="rain.dat")
        assert out["status"] == "ok"
        got = await file_path_get(ctx, session_id="bld_paths", role="RAINFALL")
        assert got["original"] == "rain.dat"
        assert got["role"] == "RAINFALL"

        # Empty path clears the slot.
        await file_path_set(ctx, session_id="bld_paths", role="RAINFALL", new_path="")
        got = await file_path_get(ctx, session_id="bld_paths", role="RAINFALL")
        assert got["original"] == ""

    async def test_unknown_role_raises(self, session_manager):
        ctx = await _building_session(session_manager, session_id="bld_badrole")
        with pytest.raises(ToolError):
            await file_path_get(ctx, session_id="bld_badrole", role="NOT_A_ROLE")

    async def test_vector_role_requires_owner(self, session_manager):
        ctx = await _building_session(session_manager, session_id="bld_vec")
        with pytest.raises(ToolError):
            await file_path_get(ctx, session_id="bld_vec", role="RAINGAGE_DATA")
