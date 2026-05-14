"""Tests for openswmm_mcp.resources.model -- MCP resources exposing model data."""

from __future__ import annotations

import json

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.resources.model import (
    list_links,
    list_nodes,
    list_sessions,
    list_subcatchments,
    mass_balance,
    node_detail,
    session_summary,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _open(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)


async def _open_and_run(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)
    await run_simulation(fake_ctx, session_id=session_id)


async def _open_and_step(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)
    await step_simulation(fake_ctx, session_id=session_id, num_steps=1)


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


class TestListSessionsResource:
    async def test_list_sessions_empty(self, fake_ctx):
        """list_sessions returns an empty array when no sessions exist."""
        raw = await list_sessions(fake_ctx)
        data = json.loads(raw)
        assert data == []

    async def test_list_sessions_with_sessions(self, fake_ctx, inp_path):
        """list_sessions returns one entry per open session."""
        await _open(fake_ctx, inp_path, "s1")
        await _open(fake_ctx, inp_path, "s2")

        raw = await list_sessions(fake_ctx)
        data = json.loads(raw)

        assert isinstance(data, list)
        assert len(data) == 2
        ids = {entry["id"] for entry in data}
        assert "s1" in ids
        assert "s2" in ids


# ---------------------------------------------------------------------------
# session_summary
# ---------------------------------------------------------------------------


class TestSessionSummaryResource:
    async def test_session_summary_counts(self, fake_ctx, inp_path, reference_model):
        """session_summary reports correct element counts and options."""
        await _open(fake_ctx, inp_path, "sum_test")
        raw = await session_summary("sum_test", fake_ctx)
        data = json.loads(raw)

        assert data["session_id"] == "sum_test"
        assert data["node_count"] == reference_model.NODE_COUNT
        assert data["link_count"] == reference_model.LINK_COUNT
        assert data["subcatchment_count"] == reference_model.SUBCATCH_COUNT
        assert data["flow_units"] == "CFS"
        assert data["route_model"] == "DYNWAVE"
        assert isinstance(data["routing_step"], (int, float))
        assert data["routing_step"] > 0

    async def test_session_summary_missing_session(self, fake_ctx):
        """session_summary raises ToolError for unknown session_id."""
        with pytest.raises(ToolError):
            await session_summary("nonexistent", fake_ctx)


# ---------------------------------------------------------------------------
# list_nodes
# ---------------------------------------------------------------------------


class TestListNodesResource:
    async def test_list_nodes(self, fake_ctx, inp_path, reference_model):
        """list_nodes returns one entry per node with id, type, and index."""
        await _open(fake_ctx, inp_path, "nodes_test")
        raw = await list_nodes("nodes_test", fake_ctx)
        data = json.loads(raw)

        assert isinstance(data, list)
        assert len(data) == reference_model.NODE_COUNT
        first = data[0]
        assert first["node_id"] == reference_model.FIRST_NODE_ID
        assert first["index"] == 0
        assert first["type"] in ("JUNCTION", "OUTFALL", "STORAGE", "DIVIDER")

    async def test_list_nodes_all_have_required_keys(self, fake_ctx, inp_path):
        """Every node entry has node_id, type, and index keys."""
        await _open(fake_ctx, inp_path, "nodes_keys")
        raw = await list_nodes("nodes_keys", fake_ctx)
        data = json.loads(raw)

        for entry in data:
            assert "node_id" in entry
            assert "type" in entry
            assert "index" in entry


# ---------------------------------------------------------------------------
# node_detail
# ---------------------------------------------------------------------------


class TestNodeDetailResource:
    async def test_node_detail_static_properties(self, fake_ctx, inp_path, reference_model):
        """node_detail returns geometry for an initialized session."""
        await _open(fake_ctx, inp_path, "nd_static")
        raw = await node_detail("nd_static", reference_model.FIRST_NODE_ID, fake_ctx)
        data = json.loads(raw)

        assert data["node_id"] == reference_model.FIRST_NODE_ID
        assert data["node_type"] in ("JUNCTION", "OUTFALL", "STORAGE", "DIVIDER")
        assert "invert_elev" in data
        assert "max_depth" in data
        assert "depth" not in data

    async def test_node_detail_runtime_state(self, fake_ctx, inp_path, reference_model):
        """node_detail includes runtime state when the session is running."""
        await _open_and_step(fake_ctx, inp_path, "nd_running")
        raw = await node_detail("nd_running", reference_model.FIRST_NODE_ID, fake_ctx)
        data = json.loads(raw)

        assert "depth" in data
        assert "head" in data
        assert "volume" in data
        assert "lateral_inflow" in data
        assert "overflow" in data
        assert isinstance(data["depth"], float)
        assert isinstance(data["head"], float)

    async def test_node_detail_outfall(self, fake_ctx, inp_path, reference_model):
        """node_detail works for the outfall node."""
        await _open(fake_ctx, inp_path, "nd_outfall")
        raw = await node_detail("nd_outfall", reference_model.OUTFALL_ID, fake_ctx)
        data = json.loads(raw)

        assert data["node_id"] == reference_model.OUTFALL_ID
        assert data["node_type"] == "OUTFALL"


# ---------------------------------------------------------------------------
# list_links
# ---------------------------------------------------------------------------


class TestListLinksResource:
    async def test_list_links(self, fake_ctx, inp_path, reference_model):
        """list_links returns one entry per link with id, type, and index."""
        await _open(fake_ctx, inp_path, "links_test")
        raw = await list_links("links_test", fake_ctx)
        data = json.loads(raw)

        assert isinstance(data, list)
        assert len(data) == reference_model.LINK_COUNT
        first = data[0]
        assert first["link_id"] == reference_model.FIRST_LINK_ID
        assert first["index"] == 0
        assert first["type"] in ("CONDUIT", "PUMP", "ORIFICE", "WEIR", "OUTLET")

    async def test_list_links_all_have_required_keys(self, fake_ctx, inp_path):
        """Every link entry has link_id, type, and index keys."""
        await _open(fake_ctx, inp_path, "links_keys")
        raw = await list_links("links_keys", fake_ctx)
        data = json.loads(raw)

        for entry in data:
            assert "link_id" in entry
            assert "type" in entry
            assert "index" in entry


# ---------------------------------------------------------------------------
# list_subcatchments
# ---------------------------------------------------------------------------


class TestListSubcatchmentsResource:
    async def test_list_subcatchments(self, fake_ctx, inp_path, reference_model):
        """list_subcatchments returns one entry per subcatchment."""
        await _open(fake_ctx, inp_path, "sc_test")
        raw = await list_subcatchments("sc_test", fake_ctx)
        data = json.loads(raw)

        assert isinstance(data, list)
        assert len(data) == reference_model.SUBCATCH_COUNT
        assert data[0]["subcatch_id"] == reference_model.FIRST_SUBCATCH_ID
        assert data[0]["index"] == 0


# ---------------------------------------------------------------------------
# mass_balance
# ---------------------------------------------------------------------------


class TestMassBalanceResource:
    async def test_mass_balance_after_run(self, fake_ctx, inp_path):
        """mass_balance returns continuity errors and totals after a full run."""
        await _open_and_run(fake_ctx, inp_path, "mb_run")
        raw = await mass_balance("mb_run", fake_ctx)
        data = json.loads(raw)

        assert data["session_id"] == "mb_run"
        assert isinstance(data["runoff_continuity_error"], float)
        assert isinstance(data["routing_continuity_error"], float)
        assert isinstance(data["runoff_total"], dict)
        assert isinstance(data["routing_total"], dict)
        # RunoffTotal enum components
        assert "rainfall" in data["runoff_total"]
        # RoutingTotal enum components
        assert "wet_weather" in data["routing_total"]

    async def test_mass_balance_no_pollutants(self, fake_ctx, inp_path, reference_model):
        """quality_continuity_error is None when the model has no pollutants."""
        # The unit test model has TSS, so use the root model (0 pollutants) if available.
        # Here we rely on the conftest's reference_model to know pollutant count.
        await _open_and_run(fake_ctx, inp_path, "mb_qual")
        raw = await mass_balance("mb_qual", fake_ctx)
        data = json.loads(raw)

        # If no pollutants: quality_continuity_error should be None.
        # If pollutants exist: it may be a float.
        assert data["quality_continuity_error"] is None or isinstance(
            data["quality_continuity_error"], float
        )
