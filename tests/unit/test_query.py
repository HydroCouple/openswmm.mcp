"""Unit tests for query tool functions."""

from __future__ import annotations

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import (
    ElementSearchResult,
    GageInfo,
    LinkInfo,
    NodeInfo,
    SubcatchmentInfo,
    SystemSummary,
)

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


async def _open_model(session_manager, tmp_inp, session_id="default"):
    """Open a model via lifecycle tool and return the context."""
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=tmp_inp, session_id=session_id)
    return ctx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetNodeInfo:
    async def test_get_node_info_single(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open_model(session_manager, tmp_inp, "qn1")
        result = await get_node_info(ctx, session_id="qn1", node_id="J1")

        assert isinstance(result, NodeInfo)
        assert result.node_id == "J1"
        assert result.node_type == "JUNCTION"
        assert result.index == 0
        assert result.invert_elev is not None

    async def test_get_node_info_outfall(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open_model(session_manager, tmp_inp, "qn_out")
        result = await get_node_info(ctx, session_id="qn_out", node_id="O1")

        assert result.node_type == "OUTFALL"
        assert result.index == 11

    async def test_get_node_info_all(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open_model(session_manager, tmp_inp, "qn_all")
        result = await get_node_info(ctx, session_id="qn_all")

        assert isinstance(result, list)
        assert len(result) == 12
        assert all(isinstance(r, NodeInfo) for r in result)

    async def test_get_node_info_nonexistent(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open_model(session_manager, tmp_inp, "qn_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_node_info(ctx, session_id="qn_bad", node_id="BOGUS")


class TestGetLinkInfo:
    async def test_get_link_info_single(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open_model(session_manager, tmp_inp, "ql1")
        result = await get_link_info(ctx, session_id="ql1", link_id="C1")

        assert isinstance(result, LinkInfo)
        assert result.link_id == "C1"
        assert result.link_type == "CONDUIT"
        assert result.from_node == "J1"
        assert result.to_node == "J2"
        assert result.length == 400.0
        assert result.roughness == 0.013

    async def test_get_link_info_all(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open_model(session_manager, tmp_inp, "ql_all")
        result = await get_link_info(ctx, session_id="ql_all")

        assert isinstance(result, list)
        assert len(result) == 11

    async def test_get_link_info_nonexistent(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open_model(session_manager, tmp_inp, "ql_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_link_info(ctx, session_id="ql_bad", link_id="BOGUS")


class TestGetSubcatchmentInfo:
    async def test_get_subcatchment_info_single(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_subcatchment_info

        ctx = await _open_model(session_manager, tmp_inp, "qs1")
        result = await get_subcatchment_info(ctx, session_id="qs1", subcatch_id="S1")

        assert isinstance(result, SubcatchmentInfo)
        assert result.subcatch_id == "S1"
        assert result.area == 5.0
        assert result.imperv_pct == 50.0

    async def test_get_subcatchment_info_all(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_subcatchment_info

        ctx = await _open_model(session_manager, tmp_inp, "qs_all")
        result = await get_subcatchment_info(ctx, session_id="qs_all")

        assert isinstance(result, list)
        assert len(result) == 8

    async def test_get_subcatchment_info_nonexistent(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_subcatchment_info

        ctx = await _open_model(session_manager, tmp_inp, "qs_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_subcatchment_info(ctx, session_id="qs_bad", subcatch_id="BOGUS")


class TestGetGageInfo:
    async def test_get_gage_info_single(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_gage_info

        ctx = await _open_model(session_manager, tmp_inp, "qg1")
        result = await get_gage_info(ctx, session_id="qg1", gage_id="RG1")

        assert isinstance(result, GageInfo)
        assert result.gage_id == "RG1"
        assert result.data_source == "TIMESERIES"
        assert result.rain_type == "INTENSITY"

    async def test_get_gage_info_all(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_gage_info

        ctx = await _open_model(session_manager, tmp_inp, "qg_all")
        result = await get_gage_info(ctx, session_id="qg_all")

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_get_gage_info_nonexistent(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_gage_info

        ctx = await _open_model(session_manager, tmp_inp, "qg_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_gage_info(ctx, session_id="qg_bad", gage_id="BOGUS")


class TestGetSystemSummary:
    async def test_get_system_summary(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_system_summary

        ctx = await _open_model(session_manager, tmp_inp, "qsys")
        result = await get_system_summary(ctx, session_id="qsys")

        assert isinstance(result, SystemSummary)
        assert result.node_count == 12
        assert result.link_count == 11
        assert result.subcatchment_count == 8
        assert result.gage_count == 2
        assert result.pollutant_count == 0
        assert result.flow_units == "CFS"
        assert result.route_model == "DYNWAVE"
        assert result.start_time == 45000.0
        assert result.end_time == 45000.25

    async def test_get_system_summary_current_time_not_running(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_system_summary

        ctx = await _open_model(session_manager, tmp_inp, "qsys_init")
        result = await get_system_summary(ctx, session_id="qsys_init")

        # In "initialized" state, current_time should be None
        assert result.current_time is None


class TestFindElements:
    async def test_find_elements_all(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open_model(session_manager, tmp_inp, "fe_all")
        result = await find_elements(ctx, session_id="fe_all")

        # 12 nodes + 11 links + 8 subcatchments + 2 gages = 33
        assert len(result) == 33
        assert all(isinstance(r, ElementSearchResult) for r in result)

    async def test_find_elements_by_pattern(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open_model(session_manager, tmp_inp, "fe_pat")
        result = await find_elements(ctx, session_id="fe_pat", pattern="^J1$")

        assert len(result) == 1
        assert result[0].element_id == "J1"
        assert result[0].element_type == "node"

    async def test_find_elements_by_type(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open_model(session_manager, tmp_inp, "fe_type")
        result = await find_elements(ctx, session_id="fe_type", element_type="link")

        assert len(result) == 11
        assert all(r.element_type == "link" for r in result)

    async def test_find_elements_by_pattern_and_type(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open_model(session_manager, tmp_inp, "fe_both")
        result = await find_elements(ctx, session_id="fe_both", pattern="C1", element_type="link")

        # C1, C10, C11 all match "C1"
        assert len(result) == 3

    async def test_find_elements_invalid_type(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open_model(session_manager, tmp_inp, "fe_bad")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await find_elements(ctx, session_id="fe_bad", element_type="bogus")

    async def test_find_elements_invalid_regex(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open_model(session_manager, tmp_inp, "fe_regex")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await find_elements(ctx, session_id="fe_regex", pattern="[invalid")
