"""MCP resources exposing SWMM model data via ``swmm://`` URIs."""

from __future__ import annotations

import asyncio
import json

from fastmcp import Context, FastMCP

from openswmm_mcp.errors import ToolError

resources_mcp = FastMCP("resources")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session_manager(ctx: Context):
    """Extract the :class:`SessionManager` from the lifespan context."""
    try:
        return ctx.lifespan_context["session_manager"]
    except (KeyError, TypeError) as exc:
        raise ToolError(
            "Session manager is not available. The server may not have started correctly."
        ) from exc


_NODE_TYPE_NAMES = {0: "JUNCTION", 1: "OUTFALL", 2: "STORAGE", 3: "DIVIDER"}
_LINK_TYPE_NAMES = {0: "CONDUIT", 1: "PUMP", 2: "ORIFICE", 3: "WEIR", 4: "OUTLET"}
_FLOW_UNITS_NAMES = {0: "CFS", 1: "GPM", 2: "MGD", 3: "CMS", 4: "LPS", 5: "MLD"}
_ROUTE_MODEL_NAMES = {0: "STEADY", 1: "KINWAVE", 2: "DYNWAVE"}


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
        raw_units = await asyncio.to_thread(solver.get_option, "FLOW_UNITS")
        flow_units = _flow_units_name(raw_units)
    except Exception:
        flow_units = "UNKNOWN"

    try:
        raw_route = await asyncio.to_thread(solver.get_option, "FLOW_ROUTING")
        route_model = _route_model_name(raw_route)
    except Exception:
        route_model = "UNKNOWN"

    summary = {
        "session_id": session_id,
        "state": session.state,
        "node_count": await asyncio.to_thread(nodes.count),
        "link_count": await asyncio.to_thread(links.count),
        "subcatchment_count": await asyncio.to_thread(subcatchments.count),
        "flow_units": flow_units,
        "route_model": route_model,
        "start_time": await asyncio.to_thread(solver.get_start_time),
        "end_time": await asyncio.to_thread(solver.get_end_time),
        "routing_step": await asyncio.to_thread(solver.get_routing_step),
    }

    return json.dumps(summary, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/nodes")
async def list_nodes(session_id: str, ctx: Context) -> str:
    """Return a JSON array of all node IDs with their types."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    nodes = session.nodes

    count = await asyncio.to_thread(nodes.count)
    result = []
    for i in range(count):
        node_id = await asyncio.to_thread(nodes.get_id, i)
        node_type_code = await asyncio.to_thread(nodes.get_type, i)
        result.append({"node_id": node_id, "type": _node_type_name(node_type_code), "index": i})

    return json.dumps(result, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/nodes/{node_id}")
async def node_detail(session_id: str, node_id: str, ctx: Context) -> str:
    """Return a JSON object with full properties and state for a single node."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    nodes = session.nodes

    index = await asyncio.to_thread(nodes.get_index, node_id)
    node_type_code = await asyncio.to_thread(nodes.get_type, index)
    invert = await asyncio.to_thread(nodes.get_invert_elev, index)
    max_depth = await asyncio.to_thread(nodes.get_max_depth, index)

    detail: dict = {
        "node_id": node_id,
        "index": index,
        "node_type": _node_type_name(node_type_code),
        "invert_elev": invert,
        "max_depth": max_depth,
    }

    if session.state in ("running", "ended"):
        detail["depth"] = await asyncio.to_thread(nodes.get_depth, index)
        detail["head"] = await asyncio.to_thread(nodes.get_head, index)
        detail["volume"] = await asyncio.to_thread(nodes.get_volume, index)
        detail["lateral_inflow"] = await asyncio.to_thread(nodes.get_lateral_inflow, index)
        detail["overflow"] = await asyncio.to_thread(nodes.get_overflow, index)

    return json.dumps(detail, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/links")
async def list_links(session_id: str, ctx: Context) -> str:
    """Return a JSON array of all link IDs with their types."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    links = session.links

    count = await asyncio.to_thread(links.count)
    result = []
    for i in range(count):
        link_id = await asyncio.to_thread(links.get_id, i)
        link_type_code = await asyncio.to_thread(links.get_type, i)
        result.append({"link_id": link_id, "type": _link_type_name(link_type_code), "index": i})

    return json.dumps(result, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/links/{link_id}")
async def link_detail(session_id: str, link_id: str, ctx: Context) -> str:
    """Return a JSON object with full properties and state for a single link."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    links = session.links
    nodes = session.nodes

    index = await asyncio.to_thread(links.get_index, link_id)
    link_type_code = await asyncio.to_thread(links.get_type, index)
    from_node_idx = await asyncio.to_thread(links.get_from_node, index)
    to_node_idx = await asyncio.to_thread(links.get_to_node, index)
    from_node = await asyncio.to_thread(nodes.get_id, from_node_idx)
    to_node = await asyncio.to_thread(nodes.get_id, to_node_idx)
    length = await asyncio.to_thread(links.get_length, index)
    roughness = await asyncio.to_thread(links.get_roughness, index)

    detail: dict = {
        "link_id": link_id,
        "index": index,
        "link_type": _link_type_name(link_type_code),
        "from_node": from_node,
        "to_node": to_node,
        "length": length,
        "roughness": roughness,
    }

    if session.state in ("running", "ended"):
        detail["flow"] = await asyncio.to_thread(links.get_flow, index)
        detail["depth"] = await asyncio.to_thread(links.get_depth, index)
        detail["velocity"] = await asyncio.to_thread(links.get_velocity, index)
        detail["capacity"] = await asyncio.to_thread(links.get_capacity, index)

    return json.dumps(detail, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/subcatchments")
async def list_subcatchments(session_id: str, ctx: Context) -> str:
    """Return a JSON array of all subcatchment IDs."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    subcatchments = session.subcatchments

    count = await asyncio.to_thread(subcatchments.count)
    result = []
    for i in range(count):
        sc_id = await asyncio.to_thread(subcatchments.get_id, i)
        result.append({"subcatch_id": sc_id, "index": i})

    return json.dumps(result, indent=2)


@resources_mcp.resource("swmm://session/{session_id}/mass_balance")
async def mass_balance(session_id: str, ctx: Context) -> str:
    """Return a JSON object with continuity errors and volumetric totals."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    mb = session.mass_balance

    runoff_error = await asyncio.to_thread(mb.get_runoff_continuity_error)
    routing_error = await asyncio.to_thread(mb.get_routing_continuity_error)

    result: dict = {
        "session_id": session_id,
        "runoff_continuity_error": runoff_error,
        "routing_continuity_error": routing_error,
    }

    try:
        quality_error = await asyncio.to_thread(mb.get_quality_continuity_error, 0)
        result["quality_continuity_error"] = quality_error
    except Exception:
        result["quality_continuity_error"] = None

    try:
        from openswmm.engine import RunoffTotal

        runoff_total = {}
        for comp in RunoffTotal:
            val = await asyncio.to_thread(mb.get_runoff_total, comp)
            runoff_total[comp.name.lower()] = val
        result["runoff_total"] = runoff_total
    except Exception:
        result["runoff_total"] = {}

    try:
        from openswmm.engine import RoutingTotal

        routing_total = {}
        for comp in RoutingTotal:
            val = await asyncio.to_thread(mb.get_routing_total, comp)
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
        raw_units = await asyncio.to_thread(solver.get_option, "FLOW_UNITS")
        flow_units = _flow_units_name(raw_units)
    except Exception:
        flow_units = "UNKNOWN"

    try:
        raw_route = await asyncio.to_thread(solver.get_option, "FLOW_ROUTING")
        route_model = _route_model_name(raw_route)
    except Exception:
        route_model = "UNKNOWN"

    options = {
        "session_id": session_id,
        "flow_units": flow_units,
        "route_model": route_model,
        "start_time": await asyncio.to_thread(solver.get_start_time),
        "end_time": await asyncio.to_thread(solver.get_end_time),
        "routing_step": await asyncio.to_thread(solver.get_routing_step),
    }

    return json.dumps(options, indent=2)
