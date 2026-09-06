"""Unit tests for the inlet-junction tool surface (engine rounds 544c79f8 / d38152ea).

Covers the full-record inlet design get / set, the inlet placement table
(both host kinds), the node inlet flag + eligibility check, and the
inlet-junction edit operations (promote / demote, split-in, fuse-out)
against the engine's ``street_inlet_junction.inp`` example: one street reach
with a conduit-hosted inlet (Combo1 on ST_A -> MH1) and an inlet junction
(IJ1 between ST_B and ST_C, Curb1 -> MH2).

Sessions are opened leniently so they stay in the editable ``opened`` state.
Artifacts land in the reviewable ``tests/_output/inlets/`` tree (CLAUDE.md §4.1).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError

_DECK = Path(__file__).parent / "data" / "street_inlet_junction.inp"
_OUTPUT_ROOT = Path(__file__).parents[1] / "_output" / "inlets"


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):  # noqa: ARG002
        pass


@pytest.fixture
def inlet_inp(request) -> str:
    """Copy of the inlet example deck inside a per-test reviewable output directory."""
    out = _OUTPUT_ROOT / request.node.name
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    dest = out / _DECK.name
    shutil.copy(_DECK, dest)
    return str(dest)


async def _opened(session_manager, inp_path: str, session_id: str):
    """Open the deck leniently (state stays ``opened``, so the editing tools accept it)."""
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id, lenient_open=True)
    return ctx


# ---------------------------------------------------------------------------
# Enum maps
# ---------------------------------------------------------------------------


class TestInletEnumMaps:
    def test_maps_are_derived_from_the_engine_enums(self):
        eng = pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util import inlet_enums as ie

        cases = [
            (eng.InletType, ie.inlet_type_codes),
            (eng.GrateType, ie.grate_type_codes),
            (eng.ThroatType, ie.throat_type_codes),
            (eng.InletCurveKind, ie.inlet_curve_kind_codes),
            (eng.InletHostKind, ie.inlet_host_kind_codes),
            (eng.InletPlacement, ie.inlet_placement_codes),
        ]
        for enum, accessor in cases:
            codes = accessor()
            assert len(codes) == len(list(enum)), enum.__name__
            for member in enum:
                assert codes[member.name.lower()] == int(member), f"{enum.__name__}.{member.name}"


# ---------------------------------------------------------------------------
# Inlet designs
# ---------------------------------------------------------------------------


class TestInletDesign:
    async def test_get_design_reads_every_type_field(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.infrastructure import get_inlet_design

        ctx = await _opened(session_manager, inlet_inp, "in_design")
        combo = await get_inlet_design(ctx, session_id="in_design", inlet_id="Combo1")
        assert combo["inlet_id"] == "Combo1"
        d = combo["design"]
        assert d["type"] == "combo"
        assert (d["grate_length"], d["grate_width"]) == pytest.approx((2.0, 2.0))
        assert d["grate_type"] == "curved_vane"
        assert (d["curb_length"], d["curb_height"]) == pytest.approx((3.0, 0.5))
        assert d["throat"] == "vertical"

        custom = await get_inlet_design(ctx, session_id="in_design", inlet_id="Custom1")
        assert custom["design"]["type"] == "custom"
        assert custom["design"]["curve_id"] == "DIV_CAP"
        assert custom["design"]["curve_kind"] in ("diversion", "none")

        # An index resolves to the same record and reports the name.
        by_index = await get_inlet_design(
            ctx, session_id="in_design", inlet_id=combo["inlet_index"]
        )
        assert by_index["inlet_id"] == "Combo1"

    async def test_set_design_partial_update_keeps_other_fields(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.infrastructure import get_inlet_design, set_inlet_design

        ctx = await _opened(session_manager, inlet_inp, "in_design_set")
        result = await set_inlet_design(
            ctx, session_id="in_design_set", inlet_id="Curb1", throat="inclined"
        )
        assert result["status"] == "ok"
        assert result["design"]["throat"] == "inclined"
        d = (await get_inlet_design(ctx, session_id="in_design_set", inlet_id="Curb1"))["design"]
        assert d["type"] == "curb"
        assert d["throat"] == "inclined"
        assert (d["curb_length"], d["curb_height"]) == pytest.approx((3.0, 0.5))

    async def test_set_design_rejects_unknown_token_and_empty_update(
        self, session_manager, inlet_inp
    ):
        from openswmm_mcp.tools.infrastructure import set_inlet_design

        ctx = await _opened(session_manager, inlet_inp, "in_design_bad")
        with pytest.raises(ToolError, match="throat"):
            await set_inlet_design(
                ctx, session_id="in_design_bad", inlet_id="Curb1", throat="sideways"
            )
        with pytest.raises(ToolError, match="at least one design field"):
            await set_inlet_design(ctx, session_id="in_design_bad", inlet_id="Curb1")
        with pytest.raises(ToolError, match="not found"):
            await set_inlet_design(
                ctx, session_id="in_design_bad", inlet_id="NoSuchInlet", curb_length=1.0
            )


# ---------------------------------------------------------------------------
# Inlet placement table
# ---------------------------------------------------------------------------


class TestInletUsage:
    async def test_count_list_get_cover_both_host_kinds(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.infrastructure import (
            inlet_usage_count,
            inlet_usage_get,
            inlet_usage_list,
        )

        ctx = await _opened(session_manager, inlet_inp, "in_usage")
        assert (await inlet_usage_count(ctx, session_id="in_usage"))["count"] == 2

        listed = await inlet_usage_list(ctx, session_id="in_usage")
        assert listed["count"] == 2
        by_host = {(u["host_kind"], u["host_id"]): u for u in listed["usages"]}
        link_row = by_host[("link", "ST_A")]
        assert link_row["inlet_id"] == "Combo1"
        assert link_row["capture_node_id"] == "MH1"
        assert link_row["num_inlets"] == 2
        assert link_row["pct_clogged"] == pytest.approx(10.0)
        assert link_row["placement"] == "on_grade"
        node_row = by_host[("node", "IJ1")]
        assert node_row["inlet_id"] == "Curb1"
        assert node_row["capture_node_id"] == "MH2"
        assert node_row["num_inlets"] == 1

        one = await inlet_usage_get(ctx, session_id="in_usage", usage_index=link_row["usage_index"])
        assert one["host_id"] == "ST_A" and one["inlet_id"] == "Combo1"

    async def test_find_by_link_and_node_host(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.infrastructure import inlet_usage_find

        ctx = await _opened(session_manager, inlet_inp, "in_usage_find")
        hit = await inlet_usage_find(
            ctx, session_id="in_usage_find", host_kind="link", host_id="ST_A"
        )
        assert hit["found"] is True
        assert hit["usage"]["inlet_id"] == "Combo1"

        node_hit = await inlet_usage_find(
            ctx, session_id="in_usage_find", host_kind="node", host_id="IJ1"
        )
        assert node_hit["found"] is True
        assert node_hit["usage"]["capture_node_id"] == "MH2"

        miss = await inlet_usage_find(
            ctx, session_id="in_usage_find", host_kind="link", host_id="SW_1"
        )
        assert miss["found"] is False
        assert miss["usage_index"] == -1
        assert "usage" not in miss

        with pytest.raises(ToolError, match="host_kind"):
            await inlet_usage_find(
                ctx, session_id="in_usage_find", host_kind="subcatchment", host_id="ST_A"
            )

    async def test_set_replaces_the_row_of_an_existing_host(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.infrastructure import (
            inlet_usage_count,
            inlet_usage_find,
            inlet_usage_set,
        )

        ctx = await _opened(session_manager, inlet_inp, "in_usage_set")
        before = await inlet_usage_find(
            ctx, session_id="in_usage_set", host_kind="link", host_id="ST_A"
        )
        result = await inlet_usage_set(
            ctx,
            session_id="in_usage_set",
            host_kind="link",
            host_id="ST_A",
            inlet_id="Grate1",
            capture_node_id="MH1",
            num_inlets=3,
            pct_clogged=25.0,
            placement="on_sag",
        )
        assert result["status"] == "ok"
        assert result["usage_index"] == before["usage_index"]
        assert result["inlet_id"] == "Grate1"
        assert result["num_inlets"] == 3
        assert result["pct_clogged"] == pytest.approx(25.0)
        assert result["placement"] == "on_sag"
        assert (await inlet_usage_count(ctx, session_id="in_usage_set"))["count"] == 2

    async def test_set_rejects_unknown_inlet_and_missing_capture_node(
        self, session_manager, inlet_inp
    ):
        from openswmm_mcp.tools.infrastructure import inlet_usage_set

        ctx = await _opened(session_manager, inlet_inp, "in_usage_set_bad")
        with pytest.raises(ToolError, match="not found"):
            await inlet_usage_set(
                ctx,
                session_id="in_usage_set_bad",
                host_kind="link",
                host_id="ST_A",
                inlet_id="NoSuchInlet",
                capture_node_id="MH1",
            )
        with pytest.raises(ToolError, match="capture_node_id must not be empty"):
            await inlet_usage_set(
                ctx,
                session_id="in_usage_set_bad",
                host_kind="link",
                host_id="ST_A",
                inlet_id="Grate1",
            )

    async def test_remove_drops_the_row(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.infrastructure import inlet_usage_find, inlet_usage_remove

        ctx = await _opened(session_manager, inlet_inp, "in_usage_remove")
        hit = await inlet_usage_find(
            ctx, session_id="in_usage_remove", host_kind="link", host_id="ST_A"
        )
        result = await inlet_usage_remove(
            ctx, session_id="in_usage_remove", usage_index=hit["usage_index"]
        )
        assert result["status"] == "ok"
        assert result["count"] == 1
        again = await inlet_usage_find(
            ctx, session_id="in_usage_remove", host_kind="link", host_id="ST_A"
        )
        assert again["found"] is False


# ---------------------------------------------------------------------------
# Node flag + eligibility
# ---------------------------------------------------------------------------


class TestNodeInletFlag:
    async def test_is_inlet_and_eligibility_codes(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.nodes import inlet_eligible, is_inlet, is_virtual

        ctx = await _opened(session_manager, inlet_inp, "in_node")
        ij1 = await is_inlet(ctx, session_id="in_node", node_id="IJ1")
        assert ij1["is_inlet"] is True
        assert (await is_virtual(ctx, session_id="in_node", node_id="IJ1"))["is_virtual"] is True
        assert (await is_inlet(ctx, session_id="in_node", node_id="MH2"))["is_inlet"] is False

        # J_TOP has a single conduit -> virtual-junction rule 609.
        top = await inlet_eligible(ctx, session_id="in_node", node_id="J_TOP")
        assert top["eligible"] is False
        assert top["rule_code"] == 609
        # MH2 sits between two identical CIRCULAR sewers: virtual-eligible,
        # but not STREET -> inlet rule 623 (for a drop inlet too).
        mh2 = await inlet_eligible(ctx, session_id="in_node", node_id="MH2")
        assert mh2["eligible"] is False
        assert mh2["rule_code"] == 623
        drop = await inlet_eligible(ctx, session_id="in_node", node_id="MH2", for_drop_inlet=True)
        assert drop["for_drop_inlet"] is True
        assert drop["rule_code"] == 623
        # The existing inlet junction passes every rule.
        assert (await inlet_eligible(ctx, session_id="in_node", node_id="IJ1"))["eligible"] is True

    async def test_unknown_node_raises(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.nodes import is_inlet

        ctx = await _opened(session_manager, inlet_inp, "in_node_bad")
        with pytest.raises(ToolError, match="not found"):
            await is_inlet(ctx, session_id="in_node_bad", node_id="NoSuchNode")


# ---------------------------------------------------------------------------
# Inlet-junction edits
# ---------------------------------------------------------------------------


class TestInletJunctionEdits:
    async def test_demote_then_promote_and_re_place(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.editing import set_node_inlet
        from openswmm_mcp.tools.infrastructure import inlet_usage_count, inlet_usage_set
        from openswmm_mcp.tools.nodes import is_inlet, is_virtual

        ctx = await _opened(session_manager, inlet_inp, "in_edit_flag")
        demoted = await set_node_inlet(
            ctx, session_id="in_edit_flag", node_id="IJ1", make_inlet=False
        )
        assert demoted["status"] == "ok" and demoted["is_inlet"] is False
        assert (await is_inlet(ctx, session_id="in_edit_flag", node_id="IJ1"))["is_inlet"] is False
        # Demotion deletes the placement row but keeps the virtual junction.
        assert (await is_virtual(ctx, session_id="in_edit_flag", node_id="IJ1"))[
            "is_virtual"
        ] is True
        assert (await inlet_usage_count(ctx, session_id="in_edit_flag"))["count"] == 1

        promoted = await set_node_inlet(ctx, session_id="in_edit_flag", node_id="IJ1")
        assert promoted["is_inlet"] is True
        assert (await inlet_usage_count(ctx, session_id="in_edit_flag"))["count"] == 1
        placed = await inlet_usage_set(
            ctx,
            session_id="in_edit_flag",
            host_kind="node",
            host_id="IJ1",
            inlet_id="Curb1",
            capture_node_id="MH2",
            pct_clogged=10.0,
            placement="on_grade",
        )
        assert placed["host_kind"] == "node" and placed["host_id"] == "IJ1"
        assert (await inlet_usage_count(ctx, session_id="in_edit_flag"))["count"] == 2

    async def test_promote_ineligible_node_fails_untouched(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.editing import set_node_inlet
        from openswmm_mcp.tools.nodes import is_inlet, is_virtual

        ctx = await _opened(session_manager, inlet_inp, "in_edit_bad")
        with pytest.raises(ToolError, match="ENGINE_ERROR"):
            await set_node_inlet(ctx, session_id="in_edit_bad", node_id="MH2")
        assert (await is_inlet(ctx, session_id="in_edit_bad", node_id="MH2"))["is_inlet"] is False
        assert (await is_virtual(ctx, session_id="in_edit_bad", node_id="MH2"))[
            "is_virtual"
        ] is False
        with pytest.raises(ToolError, match="not found"):
            await set_node_inlet(ctx, session_id="in_edit_bad", node_id="NoSuchNode")

    async def test_split_conduit_inlet_then_fuse(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.editing import fuse_inlet_junction, split_conduit_inlet
        from openswmm_mcp.tools.infrastructure import (
            inlet_usage_count,
            inlet_usage_find,
            inlet_usage_remove,
        )
        from openswmm_mcp.tools.nodes import is_inlet

        ctx = await _opened(session_manager, inlet_inp, "in_edit_split")
        sid = "in_edit_split"
        # ST_A carries a conduit-hosted inlet; an inlet junction may not sit
        # on such a conduit (rule 629), so clear that row first.
        row = await inlet_usage_find(ctx, session_id=sid, host_kind="link", host_id="ST_A")
        await inlet_usage_remove(ctx, session_id=sid, usage_index=row["usage_index"])
        assert (await inlet_usage_count(ctx, session_id=sid))["count"] == 1

        split = await split_conduit_inlet(
            ctx,
            session_id=sid,
            link_id="ST_A",
            position=0.5,
            new_node_id="IJ_A",
            new_link_id="ST_A2",
            inlet_id="Grate1",
            capture_node_id="MH1",
        )
        assert split["status"] == "ok"
        assert split["new_node_index"] >= 0 and split["new_link_index"] >= 0
        assert (await is_inlet(ctx, session_id=sid, node_id="IJ_A"))["is_inlet"] is True
        placed = await inlet_usage_find(ctx, session_id=sid, host_kind="node", host_id="IJ_A")
        assert placed["found"] is True
        assert placed["usage"]["inlet_id"] == "Grate1"
        assert placed["usage"]["capture_node_id"] == "MH1"
        assert placed["usage"]["num_inlets"] == 1
        assert placed["usage"]["placement"] == "automatic"
        assert (await inlet_usage_count(ctx, session_id=sid))["count"] == 2

        fused = await fuse_inlet_junction(ctx, session_id=sid, node_id="IJ_A")
        assert fused["status"] == "ok"
        assert fused["surviving_link_id"] == "ST_A"
        assert (await inlet_usage_count(ctx, session_id=sid))["count"] == 1
        with pytest.raises(ToolError, match="not found"):
            await is_inlet(ctx, session_id=sid, node_id="IJ_A")

    async def test_split_validates_position_and_names(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.editing import split_conduit_inlet

        ctx = await _opened(session_manager, inlet_inp, "in_edit_split_bad")
        with pytest.raises(ToolError, match="position"):
            await split_conduit_inlet(
                ctx,
                session_id="in_edit_split_bad",
                link_id="ST_C",
                position=1.0,
                new_node_id="X",
                new_link_id="Y",
                inlet_id="Grate1",
                capture_node_id="MH2",
            )
        with pytest.raises(ToolError, match="new_node_id"):
            await split_conduit_inlet(
                ctx,
                session_id="in_edit_split_bad",
                link_id="ST_C",
                position=0.5,
                new_node_id="",
                new_link_id="Y",
                inlet_id="Grate1",
                capture_node_id="MH2",
            )

    async def test_edits_require_an_editable_state(self, session_manager, inlet_inp):
        from openswmm_mcp.tools.editing import set_node_inlet
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        # A strict open lands in 'initialized', which the editing tools reject.
        await open_model(ctx, inp_path=inlet_inp, session_id="in_edit_state")
        with pytest.raises(ToolError, match="INVALID_STATE"):
            await set_node_inlet(ctx, session_id="in_edit_state", node_id="IJ1", make_inlet=False)
