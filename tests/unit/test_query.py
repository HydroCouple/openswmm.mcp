"""Unit tests for query tool functions against the real openswmm engine.

Uses site_drainage_model.inp: 12 nodes (J1–J11, O1), 11 conduits (C1–C11),
7 subcatchments (S1–S7), 1 gage (RainGage), 1 pollutant (TSS).
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import (
    ElementSearchResult,
    GageInfo,
    LinkInfo,
    NodeInfo,
    SubcatchmentInfo,
    SystemSummary,
)


class _Ctx:
    def __init__(self, sm):
        self.lifespan_context = {"session_manager": sm}

    async def report_progress(self, *_):
        pass


async def _open(session_manager, tmp_inp, session_id="default"):
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = _Ctx(session_manager)
    await open_model(ctx, inp_path=tmp_inp, session_id=session_id)
    return ctx


# ---------------------------------------------------------------------------
# get_node_info
# ---------------------------------------------------------------------------


class TestGetNodeInfo:
    async def test_get_node_info_single(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "qn1")
        result = await get_node_info(ctx, session_id="qn1", node_id=reference_model.FIRST_NODE_ID)

        assert isinstance(result, NodeInfo)
        assert result.node_id == reference_model.FIRST_NODE_ID
        assert result.node_type == "JUNCTION"
        assert result.index >= 0
        assert result.invert_elev == pytest.approx(4973.0, abs=1.0)

    async def test_get_node_info_outfall(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "qn_out")
        result = await get_node_info(ctx, session_id="qn_out", node_id=reference_model.OUTFALL_ID)

        assert result.node_type == "OUTFALL"
        assert result.invert_elev == pytest.approx(4962.0, abs=1.0)

    async def test_get_node_info_all(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "qn_all")
        result = await get_node_info(ctx, session_id="qn_all")

        assert isinstance(result, list)
        assert len(result) == reference_model.NODE_COUNT
        assert all(isinstance(r, NodeInfo) for r in result)

    async def test_get_node_info_sequential_indices(
        self, session_manager, tmp_inp, reference_model
    ):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "qn_idx")
        result = await get_node_info(ctx, session_id="qn_idx")

        indices = [n.index for n in result]
        assert indices == list(range(reference_model.NODE_COUNT))

    async def test_get_node_info_nonexistent(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "qn_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_node_info(ctx, session_id="qn_bad", node_id="BOGUS")

    async def test_no_runtime_depth_before_start(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "qn_norun")
        result = await get_node_info(
            ctx, session_id="qn_norun", node_id=reference_model.FIRST_NODE_ID
        )
        assert result.depth is None

    async def test_runtime_depth_present_after_step(
        self, session_manager, tmp_inp, reference_model
    ):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation
        from openswmm_mcp.tools.query import get_node_info

        ctx = _Ctx(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="qn_run")
        await step_simulation(ctx, session_id="qn_run", num_steps=10)
        result = await get_node_info(
            ctx, session_id="qn_run", node_id=reference_model.FIRST_NODE_ID
        )
        assert result.depth is not None
        assert result.head is not None


# ---------------------------------------------------------------------------
# get_link_info
# ---------------------------------------------------------------------------


class TestGetLinkInfo:
    async def test_get_link_info_single(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open(session_manager, tmp_inp, "ql1")
        result = await get_link_info(ctx, session_id="ql1", link_id=reference_model.FIRST_LINK_ID)

        assert isinstance(result, LinkInfo)
        assert result.link_id == reference_model.FIRST_LINK_ID
        assert result.link_type == "CONDUIT"
        assert result.length > 0
        assert result.roughness > 0

    async def test_c1_topology(self, session_manager, tmp_inp):
        """C1 connects J1 → J5 in site_drainage_model.inp."""
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open(session_manager, tmp_inp, "ql_c1")
        result = await get_link_info(ctx, session_id="ql_c1", link_id="C1")

        assert result.from_node == "J1"
        assert result.to_node == "J5"
        assert result.length == pytest.approx(185.0, abs=0.5)
        assert result.roughness == pytest.approx(0.05, abs=0.001)

    async def test_c11_terminates_at_outfall(self, session_manager, tmp_inp):
        """C11 connects J11 → O1."""
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open(session_manager, tmp_inp, "ql_c11")
        result = await get_link_info(ctx, session_id="ql_c11", link_id="C11")

        assert result.from_node == "J11"
        assert result.to_node == "O1"

    async def test_get_link_info_all(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open(session_manager, tmp_inp, "ql_all")
        result = await get_link_info(ctx, session_id="ql_all")

        assert isinstance(result, list)
        assert len(result) == reference_model.LINK_COUNT

    async def test_get_link_info_nonexistent(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open(session_manager, tmp_inp, "ql_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_link_info(ctx, session_id="ql_bad", link_id="BOGUS")

    # ------------------------------------------------------------------
    # Phase 4b: all-mode now uses _build_all_link_infos_sync. Verify that
    # the bulk path produces records equivalent to the per-link path.
    # ------------------------------------------------------------------

    async def test_get_link_info_all_matches_per_link_path(
        self,
        session_manager,
        tmp_inp,
        reference_model,
    ):
        """All-mode (bulk path) must produce the same LinkInfo records as
        calling the per-link path for each id. A regression where a bulk
        getter reads the wrong column would show up here as a per-field
        mismatch."""
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open(session_manager, tmp_inp, "ql_eq")
        all_links = await get_link_info(ctx, session_id="ql_eq")
        assert isinstance(all_links, list)
        assert len(all_links) == reference_model.LINK_COUNT

        # Per-link spot check on every link in the fixture.
        for record in all_links:
            single = await get_link_info(ctx, session_id="ql_eq", link_id=record.link_id)
            assert isinstance(single, LinkInfo)
            # Static identity / topology — must match exactly.
            assert single.link_id == record.link_id
            assert single.index == record.index
            assert single.link_type == record.link_type
            assert single.from_node == record.from_node
            assert single.to_node == record.to_node
            # Static geometry — match to numeric tolerance.
            assert single.length == pytest.approx(record.length)
            assert single.roughness == pytest.approx(record.roughness)
            assert single.slope == pytest.approx(record.slope)

    async def test_get_link_info_all_preserves_index_order(
        self,
        session_manager,
        tmp_inp,
        reference_model,
    ):
        """The bulk all-mode loop iterates links in index order; the
        returned list must reflect that contract."""
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open(session_manager, tmp_inp, "ql_idx")
        result = await get_link_info(ctx, session_id="ql_idx")
        indices = [li.index for li in result]
        assert indices == list(range(reference_model.LINK_COUNT))


# ---------------------------------------------------------------------------
# Phase 4d: pagination on get_node_info / get_link_info
# ---------------------------------------------------------------------------


class TestPagination:
    """``start_index`` / ``limit`` semantics for the all-mode list tools.

    Pagination is applied **after** the bulk fetch (the engine still
    walks the whole network); these tests verify the slice math and the
    "no pagination" default.
    """

    async def test_node_info_default_returns_all(
        self,
        session_manager,
        tmp_inp,
        reference_model,
    ):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "pg_node_all")
        result = await get_node_info(ctx, session_id="pg_node_all")
        assert isinstance(result, list)
        assert len(result) == reference_model.NODE_COUNT

    async def test_node_info_start_index_slices_correctly(
        self,
        session_manager,
        tmp_inp,
        reference_model,
    ):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "pg_node_start")
        full = await get_node_info(ctx, session_id="pg_node_start")
        sliced = await get_node_info(ctx, session_id="pg_node_start", start_index=2)
        # Same identity tail, just shifted.
        assert len(sliced) == reference_model.NODE_COUNT - 2
        assert sliced[0].node_id == full[2].node_id
        assert sliced[-1].node_id == full[-1].node_id

    async def test_node_info_limit_caps_returned_count(
        self,
        session_manager,
        tmp_inp,
    ):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "pg_node_limit")
        sliced = await get_node_info(ctx, session_id="pg_node_limit", limit=3)
        assert len(sliced) == 3

    async def test_node_info_start_plus_limit(
        self,
        session_manager,
        tmp_inp,
    ):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "pg_node_both")
        full = await get_node_info(ctx, session_id="pg_node_both")
        page = await get_node_info(ctx, session_id="pg_node_both", start_index=4, limit=3)
        assert len(page) == 3
        assert page[0].node_id == full[4].node_id
        assert page[1].node_id == full[5].node_id
        assert page[2].node_id == full[6].node_id

    async def test_node_info_oversized_start_returns_empty(
        self,
        session_manager,
        tmp_inp,
        reference_model,
    ):
        from openswmm_mcp.tools.query import get_node_info

        ctx = await _open(session_manager, tmp_inp, "pg_node_over")
        over = await get_node_info(
            ctx, session_id="pg_node_over", start_index=reference_model.NODE_COUNT + 5
        )
        assert over == []

    async def test_link_info_pagination_matches_node_pattern(
        self,
        session_manager,
        tmp_inp,
    ):
        """Same pagination semantics for links."""
        from openswmm_mcp.tools.query import get_link_info

        ctx = await _open(session_manager, tmp_inp, "pg_link")
        full = await get_link_info(ctx, session_id="pg_link")
        page = await get_link_info(ctx, session_id="pg_link", start_index=1, limit=2)
        assert len(page) == 2
        assert page[0].link_id == full[1].link_id
        assert page[1].link_id == full[2].link_id


# ---------------------------------------------------------------------------
# get_subcatchment_info
# ---------------------------------------------------------------------------


class TestGetSubcatchmentInfo:
    async def test_get_subcatchment_info_single(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_subcatchment_info

        ctx = await _open(session_manager, tmp_inp, "qs1")
        result = await get_subcatchment_info(
            ctx, session_id="qs1", subcatch_id=reference_model.FIRST_SUBCATCH_ID
        )

        assert isinstance(result, SubcatchmentInfo)
        assert result.subcatch_id == reference_model.FIRST_SUBCATCH_ID
        assert result.area == pytest.approx(4.55, abs=0.1)
        assert result.imperv_pct == pytest.approx(56.8, abs=0.5)

    async def test_get_subcatchment_info_all(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_subcatchment_info

        ctx = await _open(session_manager, tmp_inp, "qs_all")
        result = await get_subcatchment_info(ctx, session_id="qs_all")

        assert isinstance(result, list)
        assert len(result) == reference_model.SUBCATCH_COUNT

    async def test_get_subcatchment_info_nonexistent(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_subcatchment_info

        ctx = await _open(session_manager, tmp_inp, "qs_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_subcatchment_info(ctx, session_id="qs_bad", subcatch_id="BOGUS")


# ---------------------------------------------------------------------------
# get_gage_info
# ---------------------------------------------------------------------------


class TestGetGageInfo:
    async def test_get_gage_info_single(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_gage_info

        ctx = await _open(session_manager, tmp_inp, "qg1")
        result = await get_gage_info(ctx, session_id="qg1", gage_id=reference_model.GAGE_ID)

        assert isinstance(result, GageInfo)
        assert result.gage_id == reference_model.GAGE_ID
        assert result.data_source == "TIMESERIES"
        # VOLUME format gage → rain_type VOLUME (code 1)
        assert result.rain_type in ("INTENSITY", "VOLUME", "CUMULATIVE")

    async def test_get_gage_info_all(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_gage_info

        ctx = await _open(session_manager, tmp_inp, "qg_all")
        result = await get_gage_info(ctx, session_id="qg_all")

        assert isinstance(result, list)
        assert len(result) == reference_model.GAGE_COUNT

    async def test_get_gage_info_nonexistent(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_gage_info

        ctx = await _open(session_manager, tmp_inp, "qg_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
            await get_gage_info(ctx, session_id="qg_bad", gage_id="BOGUS")


# ---------------------------------------------------------------------------
# get_system_summary
# ---------------------------------------------------------------------------


class TestGetSystemSummary:
    async def test_get_system_summary_counts(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_system_summary

        ctx = await _open(session_manager, tmp_inp, "qsys")
        result = await get_system_summary(ctx, session_id="qsys")

        assert isinstance(result, SystemSummary)
        assert result.node_count == reference_model.NODE_COUNT
        assert result.link_count == reference_model.LINK_COUNT
        assert result.subcatchment_count == reference_model.SUBCATCH_COUNT
        assert result.gage_count == reference_model.GAGE_COUNT
        assert result.pollutant_count == reference_model.POLLUTANT_COUNT

    async def test_get_system_summary_options(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_system_summary

        ctx = await _open(session_manager, tmp_inp, "qsys_opts")
        result = await get_system_summary(ctx, session_id="qsys_opts")

        assert result.flow_units == "CFS"
        assert result.route_model == "DYNWAVE"

    async def test_get_system_summary_timing(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import get_system_summary

        ctx = await _open(session_manager, tmp_inp, "qsys_time")
        result = await get_system_summary(ctx, session_id="qsys_time")

        duration = result.end_time - result.start_time
        assert duration == pytest.approx(reference_model.EXPECTED_DURATION_DAYS, rel=1e-3)
        assert result.routing_step == pytest.approx(
            reference_model.EXPECTED_ROUTING_STEP_SECS, rel=1e-3
        )

    async def test_current_time_none_before_run(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import get_system_summary

        ctx = await _open(session_manager, tmp_inp, "qsys_init")
        result = await get_system_summary(ctx, session_id="qsys_init")

        assert result.current_time is None


# ---------------------------------------------------------------------------
# find_elements
# ---------------------------------------------------------------------------


class TestFindElements:
    async def test_find_elements_all(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open(session_manager, tmp_inp, "fe_all")
        result = await find_elements(ctx, session_id="fe_all")

        expected = (
            reference_model.NODE_COUNT
            + reference_model.LINK_COUNT
            + reference_model.SUBCATCH_COUNT
            + reference_model.GAGE_COUNT
        )
        assert len(result) == expected
        assert all(isinstance(r, ElementSearchResult) for r in result)

    async def test_find_elements_by_pattern(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open(session_manager, tmp_inp, "fe_pat")
        result = await find_elements(ctx, session_id="fe_pat", pattern="^J1$")

        assert len(result) == 1
        assert result[0].element_id == "J1"
        assert result[0].element_type == "node"

    async def test_find_all_junctions(self, session_manager, tmp_inp):
        """J1–J11 = 11 junctions; O1 outfall should not match."""
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open(session_manager, tmp_inp, "fe_junc")
        result = await find_elements(
            ctx, session_id="fe_junc", pattern="^J[0-9]+$", element_type="node"
        )

        assert len(result) == 11

    async def test_find_elements_by_type(self, session_manager, tmp_inp, reference_model):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open(session_manager, tmp_inp, "fe_type")
        result = await find_elements(ctx, session_id="fe_type", element_type="link")

        assert len(result) == reference_model.LINK_COUNT
        assert all(r.element_type == "link" for r in result)

    async def test_find_elements_c1_pattern(self, session_manager, tmp_inp):
        """Pattern 'C1' (case-insensitive substring) matches C1, C10, C11."""
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open(session_manager, tmp_inp, "fe_c1")
        result = await find_elements(ctx, session_id="fe_c1", pattern="C1", element_type="link")

        assert len(result) == 3

    async def test_find_elements_invalid_type(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open(session_manager, tmp_inp, "fe_bad")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await find_elements(ctx, session_id="fe_bad", element_type="bogus")

    async def test_find_elements_invalid_regex(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.query import find_elements

        ctx = await _open(session_manager, tmp_inp, "fe_regex")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await find_elements(ctx, session_id="fe_regex", pattern="[invalid")
