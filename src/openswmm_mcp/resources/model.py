"""MCP resources exposing SWMM model data via ``swmm://`` URIs."""

from __future__ import annotations

import asyncio
import json

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import get_session_manager as _get_session_manager
from openswmm_mcp.errors import ToolError, resolve_index

resources_mcp = FastMCP("resources")


_NODE_TYPE_NAMES = {0: "JUNCTION", 1: "OUTFALL", 2: "STORAGE", 3: "DIVIDER"}
_LINK_TYPE_NAMES = {0: "CONDUIT", 1: "PUMP", 2: "ORIFICE", 3: "WEIR", 4: "OUTLET"}
_FLOW_UNITS_NAMES = {0: "CFS", 1: "GPM", 2: "MGD", 3: "CMS", 4: "LPS", 5: "MLD"}
_ROUTE_MODEL_NAMES = {0: "STEADY", 1: "KINWAVE", 2: "DYNWAVE", 3: "FV"}


def _node_type_name(code) -> str:
    try:
        return _NODE_TYPE_NAMES.get(int(code), f"UNKNOWN({code})")
    except (ValueError, TypeError):
        return str(code)


def _link_type_name(code) -> str:
    try:
        return _LINK_TYPE_NAMES.get(int(code), f"UNKNOWN({code})")
    except (ValueError, TypeError):
        return str(code)


def _flow_units_name(code) -> str:
    try:
        return _FLOW_UNITS_NAMES.get(int(code), f"UNKNOWN({code})")
    except (ValueError, TypeError):
        return str(code).upper() or "UNKNOWN"


def _route_model_name(code) -> str:
    try:
        return _ROUTE_MODEL_NAMES.get(int(code), f"UNKNOWN({code})")
    except (ValueError, TypeError):
        return str(code).upper() or "UNKNOWN"


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@resources_mcp.resource("swmm://sessions")
async def list_sessions(ctx: Context) -> str:
    """Return a JSON array describing every active session.

    Each entry contains the session id, current state, and working directory.
    """
    sm = _get_session_manager(ctx)
    sessions = await sm.list_sessions()
    return json.dumps(sessions, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/summary")
async def session_summary(session_id: str, ctx: Context) -> str:
    """Return a JSON object summarising the model loaded in *session_id*.

    Includes element counts, flow units, routing model, and time range.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    solver = session.solver
    nodes = session.nodes
    links = session.links
    subcatchments = session.subcatchments

    try:
        raw_units = await asyncio.to_thread(lambda: solver.options["FLOW_UNITS"])
        flow_units = _flow_units_name(raw_units)
    except Exception:
        flow_units = "UNKNOWN"

    try:
        raw_route = await asyncio.to_thread(lambda: solver.options["FLOW_ROUTING"])
        route_model = _route_model_name(raw_route)
    except Exception:
        route_model = "UNKNOWN"

    summary = {
        "session_id": session_id,
        "state": session.state,
        "node_count": await asyncio.to_thread(len, nodes),
        "link_count": await asyncio.to_thread(len, links),
        "subcatchment_count": await asyncio.to_thread(len, subcatchments),
        "flow_units": flow_units,
        "route_model": route_model,
        "start_time": await asyncio.to_thread(lambda: solver.sim_start_time.isoformat()),
        "end_time": await asyncio.to_thread(lambda: solver.sim_end_time.isoformat()),
        "routing_step": await asyncio.to_thread(lambda: solver.routing_step.total_seconds()),
    }

    return json.dumps(summary, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/nodes")
async def list_nodes(session_id: str, ctx: Context) -> str:
    """Return a JSON array of all node IDs with their types.

    Phase 4: ids come from ``session.meta.node_ids`` (cached on first
    access via the Phase 3 ``get_ids_bulk`` accessor); the type field
    still goes through the per-element scalar accessor but the entire
    loop runs in a single worker thread instead of N round-trips.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    nodes = session.nodes

    def _build_sync() -> list[dict]:
        ids = session.meta.node_ids
        return [
            {"node_id": ids[i], "type": _node_type_name(int(nodes[i].type)), "index": i}
            for i in range(len(ids))
        ]

    result = await asyncio.to_thread(_build_sync)
    return json.dumps(result, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/nodes/{node_id}")
async def node_detail(session_id: str, node_id: str, ctx: Context) -> str:
    """Return a JSON object with full properties and state for a single node."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    nodes = session.nodes

    index = await resolve_index(nodes, node_id, "Node")

    def _build() -> dict:
        node = nodes[index]
        d: dict = {
            "node_id": node_id,
            "index": index,
            "node_type": _node_type_name(int(node.type)),
            "invert_elev": node.invert_elev,
            "max_depth": node.max_depth,
        }
        if session.state in ("running", "ended"):
            d["depth"] = node.depth
            d["head"] = node.head
            d["volume"] = node.volume
            d["lateral_inflow"] = node.lateral_inflow
            d["overflow"] = node.overflow
        return d

    detail = await asyncio.to_thread(_build)
    return json.dumps(detail, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/links")
async def list_links(session_id: str, ctx: Context) -> str:
    """Return a JSON array of all link IDs with their types.

    Phase 4: same bulk pattern as ``list_nodes``.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    links = session.links

    def _build_sync() -> list[dict]:
        ids = session.meta.link_ids
        return [
            {"link_id": ids[i], "type": _link_type_name(int(links[i].type)), "index": i}
            for i in range(len(ids))
        ]

    result = await asyncio.to_thread(_build_sync)
    return json.dumps(result, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/links/{link_id}")
async def link_detail(session_id: str, link_id: str, ctx: Context) -> str:
    """Return a JSON object with full properties and state for a single link."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    links = session.links

    index = await resolve_index(links, link_id, "Link")

    def _build() -> dict:
        link = links[index]
        d: dict = {
            "link_id": link_id,
            "index": index,
            "link_type": _link_type_name(int(link.type)),
            "from_node": link.from_node.id,
            "to_node": link.to_node.id,
            "length": link.length,
            "roughness": link.roughness,
        }
        if session.state in ("running", "ended"):
            d["flow"] = link.flow
            d["depth"] = link.depth
            d["velocity"] = link.velocity
            d["capacity"] = link.capacity
        return d

    detail = await asyncio.to_thread(_build)
    return json.dumps(detail, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/subcatchments")
async def list_subcatchments(session_id: str, ctx: Context) -> str:
    """Return a JSON array of all subcatchment IDs.

    Phase 4: ids come from ``session.meta.subcatch_ids`` — a single C
    call via the Phase 3 ``get_ids_bulk`` accessor (cached for the
    session's lifetime).  The previous implementation issued N
    ``asyncio.to_thread`` calls.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)

    def _build_sync() -> list[dict]:
        ids = session.meta.subcatch_ids
        return [{"subcatch_id": ids[i], "index": i} for i in range(len(ids))]

    result = await asyncio.to_thread(_build_sync)
    return json.dumps(result, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/mass_balance")
async def mass_balance(session_id: str, ctx: Context) -> str:
    """Return a JSON object with continuity errors and volumetric totals."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    mb = session.mass_balance

    runoff_error = await asyncio.to_thread(lambda: mb.runoff_continuity_error)
    routing_error = await asyncio.to_thread(lambda: mb.routing_continuity_error)

    result: dict = {
        "session_id": session_id,
        "runoff_continuity_error": runoff_error,
        "routing_continuity_error": routing_error,
    }

    try:
        quality_error = await asyncio.to_thread(mb.quality_continuity_error, 0)
        result["quality_continuity_error"] = quality_error
    except Exception:
        result["quality_continuity_error"] = None

    try:
        from openswmm.engine import RunoffTotal

        runoff_total = {}
        for comp in RunoffTotal:
            val = await asyncio.to_thread(mb.runoff_total, comp)
            runoff_total[comp.name.lower()] = val
        result["runoff_total"] = runoff_total
    except Exception:
        result["runoff_total"] = {}

    try:
        from openswmm.engine import RoutingTotal

        routing_total = {}
        for comp in RoutingTotal:
            val = await asyncio.to_thread(mb.routing_total, comp)
            routing_total[comp.name.lower()] = val
        result["routing_total"] = routing_total
    except Exception:
        result["routing_total"] = {}

    return json.dumps(result, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/options")
async def simulation_options(session_id: str, ctx: Context) -> str:
    """Return a JSON object with the model's simulation options."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    solver = session.solver

    try:
        raw_units = await asyncio.to_thread(lambda: solver.options["FLOW_UNITS"])
        flow_units = _flow_units_name(raw_units)
    except Exception:
        flow_units = "UNKNOWN"

    try:
        raw_route = await asyncio.to_thread(lambda: solver.options["FLOW_ROUTING"])
        route_model = _route_model_name(raw_route)
    except Exception:
        route_model = "UNKNOWN"

    options = {
        "session_id": session_id,
        "flow_units": flow_units,
        "route_model": route_model,
        "start_time": await asyncio.to_thread(lambda: solver.sim_start_time.isoformat()),
        "end_time": await asyncio.to_thread(lambda: solver.sim_end_time.isoformat()),
        "routing_step": await asyncio.to_thread(lambda: solver.routing_step.total_seconds()),
    }

    return json.dumps(options, indent=2)
