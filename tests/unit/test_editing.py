"""Unit tests for editing tool functions (delete_object, analyze_impact, convert_*)."""

from __future__ import annotations

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import ConversionResultModel, ImpactReportModel


# ---------------------------------------------------------------------------
# Mock MCP Context
# ---------------------------------------------------------------------------


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_building_session(session_manager, session_id: str = "edit"):
    """Create a building session with a few objects and return its context."""
    from openswmm_mcp.tools.building import add_link, add_node, create_model

    ctx = MockContext(session_manager)
    await create_model(ctx, session_id=session_id)
    await add_node(ctx, session_id=session_id, node_id="J1", node_type="junction")
    await add_node(ctx, session_id=session_id, node_id="J2", node_type="junction")
    await add_node(ctx, session_id=session_id, node_id="O1", node_type="outfall")
    await add_link(ctx, session_id=session_id, link_id="C1", link_type="conduit",
                   from_node="J1", to_node="J2")
    return ctx


# ---------------------------------------------------------------------------
# analyze_impact
# ---------------------------------------------------------------------------


class TestAnalyzeImpact:
    async def test_analyze_node_returns_report(self, session_manager):
        from openswmm_mcp.tools.editing import analyze_impact

        ctx = await _create_building_session(session_manager, "ai_node")
        result = await analyze_impact(ctx, session_id="ai_node",
                                     object_type="node", object_id="J1")

        assert isinstance(result, ImpactReportModel)
        assert result.session_id == "ai_node"
        assert result.object_type == "node"
        assert result.object_id == "J1"
        assert result.dry_run is True

    async def test_analyze_link_returns_report(self, session_manager):
        from openswmm_mcp.tools.editing import analyze_impact

        ctx = await _create_building_session(session_manager, "ai_link")
        result = await analyze_impact(ctx, session_id="ai_link",
                                     object_type="link", object_id="C1")

        assert isinstance(result, ImpactReportModel)
        assert result.dry_run is True

    async def test_analyze_subcatch_returns_report(self, session_manager):
        from openswmm_mcp.tools.building import add_subcatchment
        from openswmm_mcp.tools.editing import analyze_impact

        ctx = await _create_building_session(session_manager, "ai_sc")
        await add_subcatchment(ctx, session_id="ai_sc", subcatch_id="S1",
                               area=10.0, imperv_pct=50.0, slope=0.01,
                               width=100.0, outlet_node="J1")
        result = await analyze_impact(ctx, session_id="ai_sc",
                                     object_type="subcatchment", object_id="S1")
        assert isinstance(result, ImpactReportModel)

    async def test_analyze_does_not_delete(self, session_manager):
        from openswmm_mcp.tools.editing import analyze_impact

        ctx = await _create_building_session(session_manager, "ai_nodmut")
        before = (await session_manager.get_session("ai_nodmut")).model_builder

        await analyze_impact(ctx, session_id="ai_nodmut",
                             object_type="node", object_id="J1")

        # ModelEditor wraps builder — counts shouldn't change
        assert len(before._nodes) == 3

    async def test_analyze_unknown_type_raises(self, session_manager):
        from openswmm_mcp.tools.editing import analyze_impact

        ctx = await _create_building_session(session_manager, "ai_unk")
        with pytest.raises(ToolError, match="Unknown object_type"):
            await analyze_impact(ctx, session_id="ai_unk",
                                 object_type="bogus", object_id="J1")

    async def test_analyze_unknown_element_raises(self, session_manager):
        from openswmm_mcp.tools.editing import analyze_impact

        ctx = await _create_building_session(session_manager, "ai_404")
        with pytest.raises(ToolError, match="not found"):
            await analyze_impact(ctx, session_id="ai_404",
                                 object_type="node", object_id="NONEXISTENT")

    async def test_analyze_wrong_state_raises(self, session_manager, tmp_inp):
        """analyze_impact requires building or opened state; initialized is rejected."""
        from openswmm_mcp.tools.editing import analyze_impact
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="ai_state")

        # After open_model the mock transitions to 'initialized' — not editable
        with pytest.raises(ToolError, match="INVALID_STATE"):
            await analyze_impact(ctx, session_id="ai_state",
                                 object_type="node", object_id="J1")

    async def test_analyze_empty_object_id_raises(self, session_manager):
        from openswmm_mcp.tools.editing import analyze_impact

        ctx = await _create_building_session(session_manager, "ai_empty")
        with pytest.raises(ToolError, match="object_id must not be empty"):
            await analyze_impact(ctx, session_id="ai_empty",
                                 object_type="node", object_id="")

    async def test_analyze_impact_entries_have_correct_fields(self, session_manager):
        from openswmm_mcp.tools.editing import analyze_impact

        ctx = await _create_building_session(session_manager, "ai_fields")
        result = await analyze_impact(ctx, session_id="ai_fields",
                                     object_type="node", object_id="J1")
        for entry in result.impacts:
            assert hasattr(entry, "obj_type")
            assert hasattr(entry, "obj_type_name")
            assert hasattr(entry, "obj_idx")
            assert hasattr(entry, "field")
            assert hasattr(entry, "cascaded")


# ---------------------------------------------------------------------------
# delete_object
# ---------------------------------------------------------------------------


class TestDeleteObject:
    async def test_delete_node_reduces_count(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        ctx = await _create_building_session(session_manager, "del_node")
        before = len(
            (await session_manager.get_session("del_node")).model_builder._nodes
        )

        result = await delete_object(ctx, session_id="del_node",
                                     object_type="node", object_id="J1")

        assert isinstance(result, ImpactReportModel)
        after = len(
            (await session_manager.get_session("del_node")).model_builder._nodes
        )
        assert after == before - 1

    async def test_delete_link_reduces_count(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        ctx = await _create_building_session(session_manager, "del_link")
        before = len(
            (await session_manager.get_session("del_link")).model_builder._links
        )

        await delete_object(ctx, session_id="del_link",
                            object_type="link", object_id="C1")

        after = len(
            (await session_manager.get_session("del_link")).model_builder._links
        )
        assert after == before - 1

    async def test_delete_dry_run_does_not_mutate(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        ctx = await _create_building_session(session_manager, "del_dry")
        before = len(
            (await session_manager.get_session("del_dry")).model_builder._nodes
        )

        result = await delete_object(ctx, session_id="del_dry",
                                     object_type="node", object_id="J1",
                                     dry_run=True)

        assert result.dry_run is True
        after = len(
            (await session_manager.get_session("del_dry")).model_builder._nodes
        )
        assert after == before  # unchanged

    async def test_delete_node_not_found_raises(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        ctx = await _create_building_session(session_manager, "del_404n")
        with pytest.raises(ToolError, match="not found"):
            await delete_object(ctx, session_id="del_404n",
                                object_type="node", object_id="MISSING")

    async def test_delete_link_not_found_raises(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        ctx = await _create_building_session(session_manager, "del_404l")
        with pytest.raises(ToolError, match="not found"):
            await delete_object(ctx, session_id="del_404l",
                                object_type="link", object_id="NOSUCHLINK")

    async def test_delete_wrong_state_raises(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        sm = session_manager
        # No session exists — get_session would raise SESSION_NOT_FOUND
        ctx = MockContext(sm)
        with pytest.raises(Exception):
            await delete_object(ctx, session_id="nonexistent",
                                object_type="node", object_id="J1")

    async def test_delete_unknown_type_raises(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        ctx = await _create_building_session(session_manager, "del_unk")
        with pytest.raises(ToolError, match="Unknown object_type"):
            await delete_object(ctx, session_id="del_unk",
                                object_type="planet", object_id="Earth")

    async def test_delete_returns_impact_model(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        ctx = await _create_building_session(session_manager, "del_ret")
        result = await delete_object(ctx, session_id="del_ret",
                                     object_type="node", object_id="J1")

        assert isinstance(result, ImpactReportModel)
        assert result.object_type == "node"
        assert result.object_id == "J1"
        assert isinstance(result.impacts, list)

    async def test_delete_impacts_have_model_fields(self, session_manager):
        from openswmm_mcp.tools.editing import delete_object

        ctx = await _create_building_session(session_manager, "del_fields")
        result = await delete_object(ctx, session_id="del_fields",
                                     object_type="node", object_id="J1")

        for entry in result.impacts:
            assert isinstance(entry.obj_type, int)
            assert isinstance(entry.cascaded, bool)


# ---------------------------------------------------------------------------
# convert_node
# ---------------------------------------------------------------------------


class TestConvertNode:
    async def test_junction_to_outfall(self, session_manager):
        from openswmm_mcp.tools.editing import convert_node

        ctx = await _create_building_session(session_manager, "cn_j2o")
        result = await convert_node(ctx, session_id="cn_j2o",
                                   node_id="J1", new_type="outfall")

        assert isinstance(result, ConversionResultModel)
        assert result.new_type == "outfall"
        assert result.object_type == "node"

    async def test_storage_to_junction(self, session_manager):
        from openswmm_mcp.tools.building import add_node
        from openswmm_mcp.tools.editing import convert_node

        ctx = await _create_building_session(session_manager, "cn_s2j")
        await add_node(ctx, session_id="cn_s2j", node_id="S1", node_type="storage")

        result = await convert_node(ctx, session_id="cn_s2j",
                                   node_id="S1", new_type="junction")

        assert result.new_type == "junction"
        assert len(result.cleared_fields) > 0

    async def test_conversion_result_has_warnings(self, session_manager):
        from openswmm_mcp.tools.editing import convert_node

        ctx = await _create_building_session(session_manager, "cn_warn")
        # Converting J1 to storage — mock returns a warning
        result = await convert_node(ctx, session_id="cn_warn",
                                   node_id="J1", new_type="storage")

        assert isinstance(result.warnings, list)

    async def test_invalid_type_raises(self, session_manager):
        from openswmm_mcp.tools.editing import convert_node

        ctx = await _create_building_session(session_manager, "cn_inv")
        with pytest.raises(ToolError, match="Unknown node_type"):
            await convert_node(ctx, session_id="cn_inv",
                              node_id="J1", new_type="planet")

    async def test_node_not_found_raises(self, session_manager):
        from openswmm_mcp.tools.editing import convert_node

        ctx = await _create_building_session(session_manager, "cn_404")
        with pytest.raises(ToolError, match="not found"):
            await convert_node(ctx, session_id="cn_404",
                              node_id="MISSING", new_type="outfall")

    async def test_empty_node_id_raises(self, session_manager):
        from openswmm_mcp.tools.editing import convert_node

        ctx = await _create_building_session(session_manager, "cn_empty")
        with pytest.raises(ToolError, match="node_id must not be empty"):
            await convert_node(ctx, session_id="cn_empty",
                              node_id="", new_type="outfall")

    async def test_convert_node_returns_session_id(self, session_manager):
        from openswmm_mcp.tools.editing import convert_node

        ctx = await _create_building_session(session_manager, "cn_sid")
        result = await convert_node(ctx, session_id="cn_sid",
                                   node_id="J1", new_type="storage")

        assert result.session_id == "cn_sid"


# ---------------------------------------------------------------------------
# convert_link
# ---------------------------------------------------------------------------


class TestConvertLink:
    async def test_conduit_to_pump(self, session_manager):
        from openswmm_mcp.tools.editing import convert_link

        ctx = await _create_building_session(session_manager, "cl_c2p")
        result = await convert_link(ctx, session_id="cl_c2p",
                                   link_id="C1", new_type="pump")

        assert isinstance(result, ConversionResultModel)
        assert result.new_type == "pump"
        assert result.object_type == "link"

    async def test_conduit_to_weir(self, session_manager):
        from openswmm_mcp.tools.editing import convert_link

        ctx = await _create_building_session(session_manager, "cl_c2w")
        result = await convert_link(ctx, session_id="cl_c2w",
                                   link_id="C1", new_type="weir")

        assert result.new_type == "weir"
        assert len(result.cleared_fields) > 0

    async def test_conduit_to_orifice(self, session_manager):
        from openswmm_mcp.tools.editing import convert_link

        ctx = await _create_building_session(session_manager, "cl_c2or")
        result = await convert_link(ctx, session_id="cl_c2or",
                                   link_id="C1", new_type="orifice")

        assert result.new_type == "orifice"

    async def test_conduit_to_outlet(self, session_manager):
        from openswmm_mcp.tools.editing import convert_link

        ctx = await _create_building_session(session_manager, "cl_c2out")
        result = await convert_link(ctx, session_id="cl_c2out",
                                   link_id="C1", new_type="outlet")

        assert result.new_type == "outlet"

    async def test_invalid_link_type_raises(self, session_manager):
        from openswmm_mcp.tools.editing import convert_link

        ctx = await _create_building_session(session_manager, "cl_inv")
        with pytest.raises(ToolError, match="Unknown link_type"):
            await convert_link(ctx, session_id="cl_inv",
                              link_id="C1", new_type="aqueduct")

    async def test_link_not_found_raises(self, session_manager):
        from openswmm_mcp.tools.editing import convert_link

        ctx = await _create_building_session(session_manager, "cl_404")
        with pytest.raises(ToolError, match="not found"):
            await convert_link(ctx, session_id="cl_404",
                              link_id="MISSING", new_type="pump")

    async def test_empty_link_id_raises(self, session_manager):
        from openswmm_mcp.tools.editing import convert_link

        ctx = await _create_building_session(session_manager, "cl_empty")
        with pytest.raises(ToolError, match="link_id must not be empty"):
            await convert_link(ctx, session_id="cl_empty",
                              link_id="", new_type="pump")

    async def test_convert_link_result_structure(self, session_manager):
        from openswmm_mcp.tools.editing import convert_link

        ctx = await _create_building_session(session_manager, "cl_struct")
        result = await convert_link(ctx, session_id="cl_struct",
                                   link_id="C1", new_type="weir")

        assert result.session_id == "cl_struct"
        assert result.object_id == "C1"
        assert isinstance(result.cleared_fields, list)
        assert isinstance(result.warnings, list)
