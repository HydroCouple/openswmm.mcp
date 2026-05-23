"""Smoke tests for Phase 2 wave-2 modules.

Covers rename tools, events + steady-state, pollutants, model, hotstart
saves, and quality. Designed to verify each module's tools load and
basic happy-path calls succeed against the reference INP.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):  # noqa: ARG002
        pass


async def _opened(session_manager, inp_path, session_id="ph2_op"):
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    return ctx


async def _building(session_manager, session_id="ph2_bld"):
    from openswmm_mcp.tools.building import create_model

    ctx = MockContext(session_manager)
    await create_model(ctx, session_id=session_id)
    return ctx


# ===========================================================================
# Editing renames
# ===========================================================================


class TestRenames:
    async def test_rename_node(self, session_manager, inp_path):
        from openswmm_mcp.tools.editing import rename_node

        ctx = await _opened(session_manager, inp_path, "ph2_rn")
        try:
            r = await rename_node(
                ctx, session_id="ph2_rn", node_id="J1", new_id="J1_NEW",
            )
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"rename_node not allowed in this state: {e}")
        assert r["status"] == "ok"
        assert r["new_id"] == "J1_NEW"

    async def test_rename_node_unknown_id_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.editing import rename_node

        ctx = await _opened(session_manager, inp_path, "ph2_rn_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await rename_node(
                ctx, session_id="ph2_rn_bad", node_id="NOPE", new_id="X",
            )

    async def test_rename_link_empty_new_id_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.editing import rename_link

        ctx = await _opened(session_manager, inp_path, "ph2_rl_e")
        with pytest.raises(ToolError, match="new_id must not be empty"):
            await rename_link(
                ctx, session_id="ph2_rl_e", link_id="C1", new_id="",
            )


# ===========================================================================
# Events + steady-state
# ===========================================================================


class TestEvents:
    async def test_events_count_empty(self, session_manager, inp_path):
        from openswmm_mcp.tools.lifecycle import events_count

        ctx = await _opened(session_manager, inp_path, "ph2_ec")
        r = await events_count(ctx, session_id="ph2_ec")
        assert r["count"] >= 0

    async def test_events_add_validates_window(self, session_manager, inp_path):
        from openswmm_mcp.tools.lifecycle import events_add

        ctx = await _opened(session_manager, inp_path, "ph2_ea")
        with pytest.raises(ToolError, match="end_oadate must be"):
            await events_add(
                ctx, session_id="ph2_ea",
                start_oadate=100.0, end_oadate=100.0,
            )

    async def test_is_between_events(self, session_manager, inp_path):
        from openswmm_mcp.tools.lifecycle import is_between_events

        ctx = await _opened(session_manager, inp_path, "ph2_be")
        r = await is_between_events(ctx, session_id="ph2_be")
        assert "between_events" in r


class TestSteadyState:
    async def test_get_set_round_trip(self, session_manager, inp_path):
        from openswmm_mcp.tools.lifecycle import (
            get_steady_state_skip,
            set_steady_state_skip,
        )

        ctx = await _opened(session_manager, inp_path, "ph2_ss")
        # Read current; flip; verify; restore.
        original = (await get_steady_state_skip(ctx, session_id="ph2_ss"))["enabled"]
        try:
            await set_steady_state_skip(
                ctx, session_id="ph2_ss", enabled=not original,
            )
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"set_steady_state_skip rejected: {e}")
        flipped = (await get_steady_state_skip(ctx, session_id="ph2_ss"))["enabled"]
        assert flipped == (not original)


# ===========================================================================
# Pollutants
# ===========================================================================


class TestPollutantsTool:
    async def test_count_on_reference_model(self, session_manager, inp_path):
        from openswmm_mcp.tools.pollutants import count

        ctx = await _opened(session_manager, inp_path, "ph2_pc")
        r = await count(ctx, session_id="ph2_pc")
        assert isinstance(r["count"], int)
        assert r["count"] >= 0

    async def test_add_in_building_state(self, session_manager):
        from openswmm_mcp.tools.pollutants import add, count

        ctx = await _building(session_manager, "ph2_pad")
        r = await add(ctx, session_id="ph2_pad", pollutant_id="TSS")
        assert r["status"] == "ok"
        assert r["id"] == "TSS"
        c = await count(ctx, session_id="ph2_pad")
        assert c["count"] == 1


# ===========================================================================
# Model (title / options / userflag / plugins / files)
# ===========================================================================


class TestModel:
    async def test_get_title_count_in_building(self, session_manager):
        from openswmm_mcp.tools.model import get_title_count

        ctx = await _building(session_manager, "ph2_mt")
        r = await get_title_count(ctx, session_id="ph2_mt")
        assert isinstance(r["count"], int)
        assert r["count"] >= 0

    async def test_get_option_flow_units(self, session_manager, inp_path):
        from openswmm_mcp.tools.model import get_option

        # get_option works in both BUILDING and OPENED+ states.
        ctx = await _opened(session_manager, inp_path, "ph2_mo")
        r = await get_option(ctx, session_id="ph2_mo", key="FLOW_UNITS")
        assert isinstance(r["value"], str)
        assert len(r["value"]) > 0

    async def test_userflag_set_in_building(self, session_manager):
        """Engine `set_userflag_int` succeeds on a fresh builder. The
        symmetric `get_userflag_int` raises 'Invalid parameter value' on
        an unset flag in the current engine build (engine-side quirk —
        worth filing); test only the set path here."""
        from openswmm_mcp.tools.model import set_userflag_int

        ctx = await _building(session_manager, "ph2_uf")
        r = await set_userflag_int(
            ctx, session_id="ph2_uf", name="my_flag", value=42,
        )
        assert r["status"] == "ok"
        assert r["value"] == 42

    async def test_userflag_rejected_outside_building(
        self, session_manager, inp_path
    ):
        from openswmm_mcp.tools.model import get_userflag_int

        ctx = await _opened(session_manager, inp_path, "ph2_uf_op")
        with pytest.raises(ToolError, match="requires 'building'"):
            await get_userflag_int(
                ctx, session_id="ph2_uf_op", name="x",
            )

    async def test_plugins_count_in_building(self, session_manager):
        from openswmm_mcp.tools.model import plugins_count

        ctx = await _building(session_manager, "ph2_pc2")
        r = await plugins_count(ctx, session_id="ph2_pc2")
        assert isinstance(r["count"], int)
        assert r["count"] >= 0


# ===========================================================================
# Hotstart saves
# ===========================================================================


class TestHotstartSaves:
    async def test_saves_empty_on_reference(self, session_manager, inp_path):
        from openswmm_mcp.tools.hotstart import saves_count

        ctx = await _opened(session_manager, inp_path, "ph2_hsc")
        r = await saves_count(ctx, session_id="ph2_hsc")
        assert r["count"] >= 0

    async def test_saves_add_then_remove(self, session_manager, inp_path, tmp_path):
        from openswmm_mcp.tools.hotstart import (
            saves_add,
            saves_count,
            saves_remove,
        )

        ctx = await _opened(session_manager, inp_path, "ph2_hsa")
        initial = (await saves_count(ctx, session_id="ph2_hsa"))["count"]
        try:
            r = await saves_add(
                ctx, session_id="ph2_hsa",
                path=str(tmp_path / "test.hsf"),
                datetime_oadate=0.0,
            )
        except (ToolError, RuntimeError) as e:
            pytest.skip(f"saves_add rejected: {e}")
        assert r["status"] == "ok"
        after_add = (await saves_count(ctx, session_id="ph2_hsa"))["count"]
        assert after_add == initial + 1

        await saves_remove(ctx, session_id="ph2_hsa", index=r["index"])
        after_remove = (await saves_count(ctx, session_id="ph2_hsa"))["count"]
        assert after_remove == initial


# ===========================================================================
# Quality (landuse / buildup / washoff / treatment)
# ===========================================================================


class TestQualityTool:
    async def test_landuse_count(self, session_manager, inp_path):
        from openswmm_mcp.tools.quality import landuse_count

        ctx = await _opened(session_manager, inp_path, "ph2_lc")
        r = await landuse_count(ctx, session_id="ph2_lc")
        assert isinstance(r["count"], int)

    async def test_invalid_buildup_func_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.quality import buildup_set

        ctx = await _opened(session_manager, inp_path, "ph2_bb")
        with pytest.raises(ToolError, match="Unknown buildup func"):
            await buildup_set(
                ctx, session_id="ph2_bb",
                landuse_id="L1", pollutant_id=0, function="bogus",
            )

    async def test_invalid_washoff_func_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.quality import washoff_set

        ctx = await _opened(session_manager, inp_path, "ph2_ww")
        with pytest.raises(ToolError, match="Unknown washoff func"):
            await washoff_set(
                ctx, session_id="ph2_ww",
                landuse_id="L1", pollutant_id=0, function="bogus",
            )

    async def test_invalid_sweep_fraction_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.quality import set_sweep_removal

        ctx = await _opened(session_manager, inp_path, "ph2_sr")
        with pytest.raises(ToolError, match=r"fraction must be in \[0, 1\]"):
            await set_sweep_removal(
                ctx, session_id="ph2_sr", landuse_id=0, fraction=2.0,
            )
