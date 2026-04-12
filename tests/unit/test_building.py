"""Unit tests for model-building tool functions."""

from __future__ import annotations

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import BuildingResult

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


async def _create_building_session(session_manager, session_id="build"):
    """Create a building session and return context."""
    from openswmm_mcp.tools.building import create_model

    ctx = MockContext(session_manager)
    await create_model(ctx, session_id=session_id)
    return ctx


# ---------------------------------------------------------------------------
# Tests: create_model
# ---------------------------------------------------------------------------


class TestCreateModel:
    async def test_create_model(self, session_manager):
        from openswmm_mcp.tools.building import create_model

        ctx = MockContext(session_manager)
        result = await create_model(ctx, session_id="bld_new")

        assert result["status"] == "created"
        assert result["session_id"] == "bld_new"
        assert result["state"] == "building"

    async def test_create_model_session_state(self, session_manager):
        await _create_building_session(session_manager, "bld_state")

        session = await session_manager.get_session("bld_state")
        assert session.state == "building"
        assert session.model_builder is not None

    async def test_create_model_duplicate(self, session_manager):
        from openswmm_mcp.tools.building import create_model

        ctx = MockContext(session_manager)
        await create_model(ctx, session_id="bld_dup")

        with pytest.raises(ToolError, match="already exists"):
            await create_model(ctx, session_id="bld_dup")


# ---------------------------------------------------------------------------
# Tests: add_node
# ---------------------------------------------------------------------------


class TestAddNode:
    async def test_add_node_junction(self, session_manager):
        from openswmm_mcp.tools.building import add_node

        ctx = await _create_building_session(session_manager, "bld_nj")
        result = await add_node(
            ctx,
            session_id="bld_nj",
            node_id="J1",
            node_type="junction",
            invert_elev=100.0,
            max_depth=6.0,
        )

        assert isinstance(result, BuildingResult)
        assert result.status == "ok"
        assert result.element_type == "node"
        assert result.element_id == "J1"
        assert result.index == 0

    async def test_add_node_outfall(self, session_manager):
        from openswmm_mcp.tools.building import add_node

        ctx = await _create_building_session(session_manager, "bld_no")
        result = await add_node(
            ctx,
            session_id="bld_no",
            node_id="O1",
            node_type="outfall",
            invert_elev=90.0,
        )

        assert result.element_id == "O1"
        assert "outfall" in result.message.lower()

    async def test_add_node_with_coordinates(self, session_manager):
        from openswmm_mcp.tools.building import add_node

        ctx = await _create_building_session(session_manager, "bld_nxy")
        result = await add_node(
            ctx,
            session_id="bld_nxy",
            node_id="J1",
            node_type="junction",
            invert_elev=100.0,
            x=1000.0,
            y=2000.0,
        )

        assert result.status == "ok"

    async def test_add_node_empty_id(self, session_manager):
        from openswmm_mcp.tools.building import add_node

        ctx = await _create_building_session(session_manager, "bld_nempty")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await add_node(ctx, session_id="bld_nempty", node_id="")

    async def test_add_node_invalid_type(self, session_manager):
        from openswmm_mcp.tools.building import add_node

        ctx = await _create_building_session(session_manager, "bld_nbad")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await add_node(ctx, session_id="bld_nbad", node_id="X1", node_type="bogus")

    async def test_add_multiple_nodes(self, session_manager):
        from openswmm_mcp.tools.building import add_node

        ctx = await _create_building_session(session_manager, "bld_nmult")

        r1 = await add_node(ctx, session_id="bld_nmult", node_id="J1", node_type="junction")
        r2 = await add_node(ctx, session_id="bld_nmult", node_id="J2", node_type="junction")

        assert r1.index == 0
        assert r2.index == 1


# ---------------------------------------------------------------------------
# Tests: add_link
# ---------------------------------------------------------------------------


class TestAddLink:
    async def test_add_link_conduit(self, session_manager):
        from openswmm_mcp.tools.building import add_link, add_node

        ctx = await _create_building_session(session_manager, "bld_lc")

        await add_node(ctx, session_id="bld_lc", node_id="J1", node_type="junction")
        await add_node(ctx, session_id="bld_lc", node_id="J2", node_type="junction")

        result = await add_link(
            ctx,
            session_id="bld_lc",
            link_id="C1",
            link_type="conduit",
            from_node="J1",
            to_node="J2",
            length=400.0,
            roughness=0.013,
            xsect_shape="circular",
            xsect_geom1=1.5,
        )

        assert isinstance(result, BuildingResult)
        assert result.status == "ok"
        assert result.element_type == "link"
        assert result.element_id == "C1"
        assert result.index == 0

    async def test_add_link_empty_id(self, session_manager):
        from openswmm_mcp.tools.building import add_link

        ctx = await _create_building_session(session_manager, "bld_lempty")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await add_link(
                ctx,
                session_id="bld_lempty",
                link_id="",
                from_node="J1",
                to_node="J2",
            )

    async def test_add_link_missing_nodes(self, session_manager):
        from openswmm_mcp.tools.building import add_link

        ctx = await _create_building_session(session_manager, "bld_lmn")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await add_link(
                ctx,
                session_id="bld_lmn",
                link_id="C1",
                from_node="",
                to_node="J2",
            )

    async def test_add_link_invalid_type(self, session_manager):
        from openswmm_mcp.tools.building import add_link

        ctx = await _create_building_session(session_manager, "bld_lbad")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await add_link(
                ctx,
                session_id="bld_lbad",
                link_id="C1",
                link_type="bogus",
                from_node="J1",
                to_node="J2",
            )


# ---------------------------------------------------------------------------
# Tests: add_subcatchment
# ---------------------------------------------------------------------------


class TestAddSubcatchment:
    async def test_add_subcatchment(self, session_manager):
        from openswmm_mcp.tools.building import add_subcatchment

        ctx = await _create_building_session(session_manager, "bld_sc")
        result = await add_subcatchment(
            ctx,
            session_id="bld_sc",
            subcatch_id="S1",
            area=10.0,
            imperv_pct=50.0,
            slope=0.5,
            width=100.0,
            outlet_node="J1",
        )

        assert isinstance(result, BuildingResult)
        assert result.status == "ok"
        assert result.element_type == "subcatchment"
        assert result.element_id == "S1"
        assert result.index == 0

    async def test_add_subcatchment_empty_id(self, session_manager):
        from openswmm_mcp.tools.building import add_subcatchment

        ctx = await _create_building_session(session_manager, "bld_scempty")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await add_subcatchment(ctx, session_id="bld_scempty", subcatch_id="")


# ---------------------------------------------------------------------------
# Tests: validate_model
# ---------------------------------------------------------------------------


class TestValidateModel:
    async def test_validate_model(self, session_manager):
        from openswmm_mcp.tools.building import add_node, validate_model

        ctx = await _create_building_session(session_manager, "bld_val")

        await add_node(ctx, session_id="bld_val", node_id="J1", node_type="junction")

        result = await validate_model(ctx, session_id="bld_val")

        assert result["valid"] is True
        assert result["status"] == "valid"
        assert result["message_count"] == 0
        assert result["messages"] == []


# ---------------------------------------------------------------------------
# Tests: write_model
# ---------------------------------------------------------------------------


class TestWriteModel:
    async def test_write_model(self, session_manager, tmp_path):
        from openswmm_mcp.tools.building import add_node, write_model

        ctx = await _create_building_session(session_manager, "bld_write")

        await add_node(ctx, session_id="bld_write", node_id="J1", node_type="junction")
        await add_node(ctx, session_id="bld_write", node_id="O1", node_type="outfall")

        out_path = str(tmp_path / "output.inp")
        result = await write_model(ctx, session_id="bld_write", output_path=out_path)

        assert result["status"] == "ok"
        assert result["session_id"] == "bld_write"
        assert "output.inp" in result["path"]

    async def test_write_model_empty_path(self, session_manager):
        from openswmm_mcp.tools.building import write_model

        ctx = await _create_building_session(session_manager, "bld_wempty")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await write_model(ctx, session_id="bld_wempty", output_path="")

    async def test_write_model_transitions_state(self, session_manager, tmp_path):
        from openswmm_mcp.tools.building import add_node, write_model

        ctx = await _create_building_session(session_manager, "bld_wstate")

        await add_node(ctx, session_id="bld_wstate", node_id="J1", node_type="junction")

        out_path = str(tmp_path / "finalized.inp")
        await write_model(ctx, session_id="bld_wstate", output_path=out_path)

        # After write, session state should be "created" (builder finalized)
        session = await session_manager.get_session("bld_wstate")
        assert session.state == "created"
        assert session.solver is not None
