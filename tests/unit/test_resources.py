"""Tests for openswmm_mcp.resources.model -- MCP resources exposing model data."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from openswmm_mcp.errors import ToolError
from openswmm_mcp.resources.model import (
    list_links,
    list_nodes,
    list_sessions,
    mass_balance,
    node_detail,
    session_summary,
)

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}


def _make_solver():
    """Return a mock solver with common query methods."""
    solver = MagicMock()
    solver.get_node_count = MagicMock(return_value=5)
    solver.get_link_count = MagicMock(return_value=4)
    solver.get_subcatch_count = MagicMock(return_value=3)
    solver.get_flow_units = MagicMock(return_value="CFS")
    solver.get_route_model = MagicMock(return_value="DYNWAVE")
    solver.get_start_time = MagicMock(return_value=0.0)
    solver.get_end_time = MagicMock(return_value=86400.0)
    solver.get_route_step = MagicMock(return_value=30.0)
    return solver


def _make_session(state: str = "opened"):
    """Return a mock SimSession."""
    session = MagicMock()
    session.state = state
    session.solver = _make_solver()

    # nodes facade
    session.nodes.get_id = MagicMock(side_effect=lambda i: f"J{i + 1}")
    session.nodes.get_type = MagicMock(return_value="JUNCTION")
    session.nodes.get_index = MagicMock(side_effect=lambda nid: int(nid[1:]) - 1)
    session.nodes.get_invert = MagicMock(return_value=10.0)
    session.nodes.get_max_depth = MagicMock(return_value=6.0)
    session.nodes.get_depth = MagicMock(return_value=1.5)
    session.nodes.get_head = MagicMock(return_value=11.5)
    session.nodes.get_volume = MagicMock(return_value=100.0)
    session.nodes.get_lateral_inflow = MagicMock(return_value=0.5)
    session.nodes.get_overflow = MagicMock(return_value=0.0)

    # links facade
    session.links.get_id = MagicMock(side_effect=lambda i: f"C{i + 1}")
    session.links.get_type = MagicMock(return_value="CONDUIT")
    session.links.get_index = MagicMock(side_effect=lambda lid: int(lid[1:]) - 1)
    session.links.get_from_node = MagicMock(return_value="J1")
    session.links.get_to_node = MagicMock(return_value="J2")
    session.links.get_length = MagicMock(return_value=400.0)
    session.links.get_roughness = MagicMock(return_value=0.01)
    session.links.get_flow = MagicMock(return_value=2.0)
    session.links.get_depth = MagicMock(return_value=0.8)
    session.links.get_velocity = MagicMock(return_value=3.5)
    session.links.get_capacity = MagicMock(return_value=0.6)

    # subcatchments facade
    session.subcatchments.get_id = MagicMock(side_effect=lambda i: f"S{i + 1}")

    # mass_balance facade
    session.mass_balance.get_runoff_error = MagicMock(return_value=0.01)
    session.mass_balance.get_routing_error = MagicMock(return_value=-0.05)
    session.mass_balance.get_quality_error = MagicMock(return_value=0.0)
    session.mass_balance.get_runoff_total = MagicMock(
        return_value={"inflow": 1000.0, "outflow": 990.0}
    )
    session.mass_balance.get_routing_total = MagicMock(
        return_value={"inflow": 990.0, "outflow": 985.0}
    )

    return session


def _make_session_manager(sessions: dict | None = None):
    sm = MagicMock()
    _sessions = sessions or {}

    async def _get_session(sid):
        if sid not in _sessions:
            raise ToolError(f"No session with id '{sid}'.")
        return _sessions[sid]

    sm.get_session = AsyncMock(side_effect=_get_session)

    async def _list_sessions():
        return [
            {"id": sid, "state": s.state, "working_dir": "/tmp"} for sid, s in _sessions.items()
        ]

    sm.list_sessions = AsyncMock(side_effect=_list_sessions)

    return sm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestListSessionsResource:
    async def test_list_sessions_resource(self):
        """list_sessions returns a JSON string with session metadata."""
        session = _make_session()
        sm = _make_session_manager({"default": session, "scenario_a": session})
        ctx = MockContext(sm)

        raw = await list_sessions(ctx)
        data = json.loads(raw)

        assert isinstance(data, list)
        assert len(data) == 2
        ids = {entry["id"] for entry in data}
        assert "default" in ids
        assert "scenario_a" in ids

    async def test_list_sessions_empty(self):
        """list_sessions returns an empty JSON array when no sessions exist."""
        sm = _make_session_manager({})
        ctx = MockContext(sm)

        raw = await list_sessions(ctx)
        data = json.loads(raw)

        assert data == []


class TestSessionSummaryResource:
    async def test_session_summary_resource(self):
        """session_summary returns model summary as JSON."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        raw = await session_summary("default", ctx)
        data = json.loads(raw)

        assert data["session_id"] == "default"
        assert data["node_count"] == 5
        assert data["link_count"] == 4
        assert data["subcatchment_count"] == 3
        assert data["flow_units"] == "CFS"
        assert data["route_model"] == "DYNWAVE"
        assert data["routing_step"] == 30.0


class TestListNodesResource:
    async def test_session_nodes_resource(self):
        """list_nodes returns a JSON array of node IDs and types."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        raw = await list_nodes("default", ctx)
        data = json.loads(raw)

        assert isinstance(data, list)
        assert len(data) == 5  # get_node_count returns 5
        assert data[0]["node_id"] == "J1"
        assert data[0]["type"] == "JUNCTION"
        assert data[0]["index"] == 0


class TestNodeDetailResource:
    async def test_session_node_detail_resource(self):
        """node_detail returns full node properties as JSON."""
        session = _make_session(state="opened")
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        raw = await node_detail("default", "J1", ctx)
        data = json.loads(raw)

        assert data["node_id"] == "J1"
        assert data["node_type"] == "JUNCTION"
        assert data["invert_elev"] == 10.0
        assert data["max_depth"] == 6.0
        # State fields should not be present when not running
        assert "depth" not in data

    async def test_session_node_detail_with_runtime_state(self):
        """node_detail includes runtime state when the session is running."""
        session = _make_session(state="running")
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        raw = await node_detail("default", "J1", ctx)
        data = json.loads(raw)

        assert data["depth"] == 1.5
        assert data["head"] == 11.5
        assert data["volume"] == 100.0
        assert data["lateral_inflow"] == 0.5
        assert data["overflow"] == 0.0


class TestListLinksResource:
    async def test_session_links_resource(self):
        """list_links returns a JSON array of link IDs and types."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        raw = await list_links("default", ctx)
        data = json.loads(raw)

        assert isinstance(data, list)
        assert len(data) == 4  # get_link_count returns 4
        assert data[0]["link_id"] == "C1"
        assert data[0]["type"] == "CONDUIT"
        assert data[0]["index"] == 0


class TestMassBalanceResource:
    async def test_session_mass_balance_resource(self):
        """mass_balance returns continuity errors and totals."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        raw = await mass_balance("default", ctx)
        data = json.loads(raw)

        assert data["session_id"] == "default"
        assert data["runoff_continuity_error"] == 0.01
        assert data["routing_continuity_error"] == -0.05
        assert data["quality_continuity_error"] == 0.0
        assert "inflow" in data["runoff_total"]
        assert "inflow" in data["routing_total"]

    async def test_session_mass_balance_missing_quality(self):
        """mass_balance gracefully handles missing quality error."""
        session = _make_session()
        # Make get_quality_error raise an exception
        session.mass_balance.get_quality_error = MagicMock(
            side_effect=RuntimeError("no quality data")
        )
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        raw = await mass_balance("default", ctx)
        data = json.loads(raw)

        assert data["quality_continuity_error"] is None
