"""Query tools for the OpenSWMM MCP server.

Provides read-only tools for inspecting model elements (nodes, links,
subcatchments, gages) and searching across the model.
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import get_session_manager, require_state
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import (
    ElementSearchResult,
    GageInfo,
    LinkInfo,
    NodeInfo,
    SubcatchmentInfo,
    SystemSummary,
)

logger = logging.getLogger(__name__)

query_mcp = FastMCP("query")


# ---------------------------------------------------------------------------
# Enum-to-string helpers
# ---------------------------------------------------------------------------

_NODE_TYPE_NAMES = {0: "JUNCTION", 1: "OUTFALL", 2: "STORAGE", 3: "DIVIDER"}
_LINK_TYPE_NAMES = {0: "CONDUIT", 1: "PUMP", 2: "ORIFICE", 3: "WEIR", 4: "OUTLET"}
_GAGE_SOURCE_NAMES = {0: "TIMESERIES", 1: "FILE"}
_GAGE_RAIN_NAMES = {0: "INTENSITY", 1: "VOLUME", 2: "CUMULATIVE"}


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------


async def _build_node_info(session, nodes, index: int) -> NodeInfo:
    """Build a NodeInfo for a single node by index."""
    node_id = await asyncio.to_thread(nodes.get_id, index)
    type_code = await asyncio.to_thread(nodes.get_type, index)
    invert = await asyncio.to_thread(nodes.get_invert_elev, index)
    max_depth = await asyncio.to_thread(nodes.get_max_depth, index)

    # Runtime state -- may not be available if simulation hasn't started
    depth: float | None = None
    head: float | None = None
    volume: float | None = None
    lat_inflow: float | None = None
    overflow: float | None = None

    outfall_route_to: int | None = None

    if session.state in ("running", "ended"):
        try:
            depth = await asyncio.to_thread(nodes.get_depth, index)
            head = await asyncio.to_thread(nodes.get_head, index)
            volume = await asyncio.to_thread(nodes.get_volume, index)
            lat_inflow = await asyncio.to_thread(nodes.get_lateral_inflow, index)
            overflow = await asyncio.to_thread(nodes.get_overflow, index)
        except Exception:
            pass

    # Outfall route-to subcatchment (available in any state)
    if hasattr(nodes, "get_outfall_route_to"):
        try:
            rt = await asyncio.to_thread(nodes.get_outfall_route_to, index)
            if rt >= 0:
                outfall_route_to = rt
        except Exception:
            pass

    return NodeInfo(
        node_id=node_id,
        index=index,
        node_type=_NODE_TYPE_NAMES.get(type_code, f"UNKNOWN({type_code})"),
        invert_elev=invert,
        max_depth=max_depth,
        depth=depth,
        head=head,
        volume=volume,
        lateral_inflow=lat_inflow,
        overflow=overflow,
        outfall_route_to=outfall_route_to,
    )


async def _build_link_info(session, links, nodes, index: int) -> LinkInfo:
    """Build a LinkInfo for a single link by index."""
    link_id = await asyncio.to_thread(links.get_id, index)
    type_code = await asyncio.to_thread(links.get_type, index)
    from_node_idx = await asyncio.to_thread(links.get_from_node, index)
    to_node_idx = await asyncio.to_thread(links.get_to_node, index)
    from_node_id = await asyncio.to_thread(nodes.get_id, from_node_idx)
    to_node_id = await asyncio.to_thread(nodes.get_id, to_node_idx)
    length = await asyncio.to_thread(links.get_length, index)
    roughness = await asyncio.to_thread(links.get_roughness, index)

    # Runtime state
    flow: float | None = None
    depth: float | None = None
    velocity: float | None = None
    capacity: float | None = None

    hydraulic_power: float | None = None
    pump_cycles: int | None = None
    pump_on_time: float | None = None
    pump_volume: float | None = None

    if session.state in ("running", "ended"):
        try:
            flow = await asyncio.to_thread(links.get_flow, index)
            depth = await asyncio.to_thread(links.get_depth, index)
            velocity = await asyncio.to_thread(links.get_velocity, index)
            capacity = await asyncio.to_thread(links.get_capacity, index)
        except Exception:
            pass

        # Hydraulic power (available for all link types)
        if hasattr(links, "get_hyd_power"):
            try:
                hydraulic_power = await asyncio.to_thread(links.get_hyd_power, index)
            except Exception:
                pass

        # Pump statistics (only for PUMP type links, type_code == 1)
        if type_code == 1 and hasattr(links, "get_stat_pump_cycles"):
            try:
                pump_cycles = await asyncio.to_thread(links.get_stat_pump_cycles, index)
                pump_on_time = await asyncio.to_thread(links.get_stat_pump_on_time, index)
                pump_volume = await asyncio.to_thread(links.get_stat_pump_volume, index)
            except Exception:
                pass

    return LinkInfo(
        link_id=link_id,
        index=index,
        link_type=_LINK_TYPE_NAMES.get(type_code, f"UNKNOWN({type_code})"),
        from_node=from_node_id,
        to_node=to_node_id,
        length=length,
        roughness=roughness,
        flow=flow,
        depth=depth,
        velocity=velocity,
        capacity=capacity,
        hydraulic_power=hydraulic_power,
        pump_cycles=pump_cycles,
        pump_on_time=pump_on_time,
        pump_volume=pump_volume,
    )


async def _build_subcatch_info(session, subcatchments, index: int) -> SubcatchmentInfo:
    """Build a SubcatchmentInfo for a single subcatchment by index."""
    sc_id = await asyncio.to_thread(subcatchments.get_id, index)
    area = await asyncio.to_thread(subcatchments.get_area, index)
    imperv = await asyncio.to_thread(subcatchments.get_imperv_pct, index)
    slope = await asyncio.to_thread(subcatchments.get_slope, index)
    width = await asyncio.to_thread(subcatchments.get_width, index)

    # Runtime state
    rainfall: float | None = None
    runoff: float | None = None
    depth: float | None = None

    if session.state in ("running", "ended"):
        try:
            rainfall = await asyncio.to_thread(subcatchments.get_rainfall, index)
            runoff = await asyncio.to_thread(subcatchments.get_runoff, index)
        except Exception:
            pass

    return SubcatchmentInfo(
        subcatch_id=sc_id,
        index=index,
        area=area,
        imperv_pct=imperv,
        slope=slope,
        width=width,
        rainfall=rainfall,
        runoff=runoff,
        depth=depth,
    )


async def _build_gage_info(session, gages, index: int) -> GageInfo:
    """Build a GageInfo for a single rain gage by index."""
    gage_id = await asyncio.to_thread(gages.get_id, index)
    source_code = await asyncio.to_thread(gages.get_data_source, index)
    rain_type_code = await asyncio.to_thread(gages.get_rain_type, index)

    rainfall: float | None = None
    if session.state in ("running", "ended"):
        try:
            rainfall = await asyncio.to_thread(gages.get_rainfall, index)
        except Exception:
            pass

    return GageInfo(
        gage_id=gage_id,
        index=index,
        data_source=_GAGE_SOURCE_NAMES.get(source_code, f"UNKNOWN({source_code})"),
        rain_type=_GAGE_RAIN_NAMES.get(rain_type_code, f"UNKNOWN({rain_type_code})"),
        rainfall=rainfall,
    )


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@query_mcp.tool
async def get_node_info(
    ctx: Context,
    session_id: str = "default",
    node_id: str | None = None,
    properties: list[str] | None = None,
) -> NodeInfo | list[NodeInfo]:
    """Return properties and state for one or all nodes.

    When *node_id* is given, returns a single NodeInfo.  When omitted,
    returns a list of NodeInfo for every node in the model.  The optional
    *properties* list filters which fields are included (not yet implemented;
    reserved for future optimisation).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended")

    nodes = session.nodes

    if node_id is not None:
        idx = await asyncio.to_thread(nodes.get_index, node_id)
        if idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found.")
        return await _build_node_info(session, nodes, idx)

    # Return all nodes
    count = await asyncio.to_thread(nodes.count)
    results: list[NodeInfo] = []
    for i in range(count):
        results.append(await _build_node_info(session, nodes, i))
    return results


@query_mcp.tool
async def get_link_info(
    ctx: Context,
    session_id: str = "default",
    link_id: str | None = None,
) -> LinkInfo | list[LinkInfo]:
    """Return properties and state for one or all links.

    When *link_id* is given, returns a single LinkInfo.  When omitted,
    returns a list of LinkInfo for every link in the model.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended")

    links = session.links
    nodes = session.nodes

    if link_id is not None:
        idx = await asyncio.to_thread(links.get_index, link_id)
        if idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found.")
        return await _build_link_info(session, links, nodes, idx)

    # Return all links
    count = await asyncio.to_thread(links.count)
    results: list[LinkInfo] = []
    for i in range(count):
        results.append(await _build_link_info(session, links, nodes, i))
    return results


@query_mcp.tool
async def get_subcatchment_info(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | None = None,
) -> SubcatchmentInfo | list[SubcatchmentInfo]:
    """Return properties and state for one or all subcatchments.

    When *subcatch_id* is given, returns a single SubcatchmentInfo.  When
    omitted, returns a list of SubcatchmentInfo for every subcatchment.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended")

    subcatchments = session.subcatchments

    if subcatch_id is not None:
        idx = await asyncio.to_thread(subcatchments.get_index, subcatch_id)
        if idx < 0:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{subcatch_id}' not found."
            )
        return await _build_subcatch_info(session, subcatchments, idx)

    # Return all subcatchments
    count = await asyncio.to_thread(subcatchments.count)
    results: list[SubcatchmentInfo] = []
    for i in range(count):
        results.append(await _build_subcatch_info(session, subcatchments, i))
    return results


@query_mcp.tool
async def get_gage_info(
    ctx: Context,
    session_id: str = "default",
    gage_id: str | None = None,
) -> GageInfo | list[GageInfo]:
    """Return properties and state for one or all rain gages.

    When *gage_id* is given, returns a single GageInfo.  When omitted,
    returns a list of GageInfo for every rain gage in the model.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended")

    gages = session.gages

    if gage_id is not None:
        idx = await asyncio.to_thread(gages.get_index, gage_id)
        if idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Gage '{gage_id}' not found.")
        return await _build_gage_info(session, gages, idx)

    # Return all gages
    count = await asyncio.to_thread(gages.count)
    results: list[GageInfo] = []
    for i in range(count):
        results.append(await _build_gage_info(session, gages, i))
    return results


@query_mcp.tool
async def get_system_summary(
    ctx: Context,
    session_id: str = "default",
) -> SystemSummary:
    """Return a full system summary including counts, options, and timing.

    Provides an overview of the loaded model's configuration and, if a
    simulation is running or has ended, the current simulation time.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended")

    solver = session.solver
    nodes = session.nodes
    links = session.links
    subcatchments = session.subcatchments
    gages = session.gages
    pollutants = session.pollutants

    node_count = await asyncio.to_thread(nodes.count)
    link_count = await asyncio.to_thread(links.count)
    subcatch_count = await asyncio.to_thread(subcatchments.count)
    gage_count = await asyncio.to_thread(gages.count)
    pollutant_count = await asyncio.to_thread(pollutants.count)

    start_time = await asyncio.to_thread(solver.get_start_time)
    end_time = await asyncio.to_thread(solver.get_end_time)
    routing_step = await asyncio.to_thread(solver.get_routing_step)

    _FLOW_UNITS = {0: "CFS", 1: "GPM", 2: "MGD", 3: "CMS", 4: "LPS", 5: "MLD"}
    _ROUTE_MODELS = {0: "STEADY", 1: "KINWAVE", 2: "DYNWAVE"}

    try:
        raw_units = await asyncio.to_thread(solver.get_option, "FLOW_UNITS")
        try:
            flow_units = _FLOW_UNITS.get(int(raw_units), "UNKNOWN")
        except (ValueError, TypeError):
            flow_units = str(raw_units).upper() or "UNKNOWN"
    except Exception:
        flow_units = "UNKNOWN"

    try:
        raw_route = await asyncio.to_thread(solver.get_option, "FLOW_ROUTING")
        try:
            route_model = _ROUTE_MODELS.get(int(raw_route), "UNKNOWN")
        except (ValueError, TypeError):
            route_model = str(raw_route).upper() or "UNKNOWN"
    except Exception:
        route_model = "UNKNOWN"

    current_time: float | None = None
    if session.state in ("running", "ended"):
        try:
            current_time = await asyncio.to_thread(solver.get_current_time)
        except Exception:
            pass

    # Extended fields from refactored engine
    surcharge_method: str | None = None
    dps_celerity: float | None = None
    dps_alpha: float | None = None
    dps_decay_time: float | None = None
    event_count: int | None = None
    steady_state_skip: bool | None = None

    try:
        surcharge_method = await asyncio.to_thread(solver.get_option, "SURCHARGE_METHOD")
    except Exception:
        pass

    if surcharge_method and "DYNAMIC" in str(surcharge_method).upper():
        try:
            dps_celerity = float(await asyncio.to_thread(solver.get_option, "DPS_CELERITY"))
            dps_alpha = float(await asyncio.to_thread(solver.get_option, "DPS_ALPHA"))
            dps_decay_time = float(await asyncio.to_thread(solver.get_option, "DPS_DECAY_TIME"))
        except Exception:
            pass

    if hasattr(solver, "get_event_count"):
        try:
            event_count = await asyncio.to_thread(solver.get_event_count)
        except Exception:
            pass

    if hasattr(solver, "get_steady_state_skip"):
        try:
            steady_state_skip = await asyncio.to_thread(solver.get_steady_state_skip)
        except Exception:
            pass

    return SystemSummary(
        session_id=session_id,
        state=session.state,
        engine=session.engine_kind,
        node_count=node_count,
        link_count=link_count,
        subcatchment_count=subcatch_count,
        gage_count=gage_count,
        pollutant_count=pollutant_count,
        flow_units=flow_units,
        route_model=route_model,
        start_time=start_time,
        end_time=end_time,
        routing_step=routing_step,
        current_time=current_time,
        surcharge_method=surcharge_method,
        dps_celerity=dps_celerity,
        dps_alpha=dps_alpha,
        dps_decay_time=dps_decay_time,
        event_count=event_count,
        steady_state_skip=steady_state_skip,
    )


@query_mcp.tool
async def find_elements(
    ctx: Context,
    session_id: str = "default",
    pattern: str | None = None,
    element_type: str | None = None,
) -> list[ElementSearchResult]:
    """Search for model elements by ID pattern and/or type.

    *pattern* is a Python regex matched against element IDs (case-insensitive).
    *element_type* restricts the search to ``"node"``, ``"link"``,
    ``"subcatchment"``, or ``"gage"``.  Both are optional; when neither is
    given all elements are returned.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended")

    # Validate the element type if provided
    valid_types = {"node", "link", "subcatchment", "gage"}
    if element_type is not None:
        element_type = element_type.strip().lower()
        if element_type not in valid_types:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown element_type "
                f"'{element_type}'. Valid types: {', '.join(sorted(valid_types))}."
            )

    # Compile the regex, if any
    regex = None
    if pattern is not None:
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Invalid regex pattern: {exc}") from exc

    results: list[ElementSearchResult] = []

    # Define the element sources to scan
    sources: list[tuple[str, object]] = []

    if element_type is None or element_type == "node":
        sources.append(("node", session.nodes))
    if element_type is None or element_type == "link":
        sources.append(("link", session.links))
    if element_type is None or element_type == "subcatchment":
        sources.append(("subcatchment", session.subcatchments))
    if element_type is None or element_type == "gage":
        sources.append(("gage", session.gages))

    for etype, accessor in sources:
        count = await asyncio.to_thread(accessor.count)
        for i in range(count):
            eid = await asyncio.to_thread(accessor.get_id, i)
            if regex is not None and not regex.search(eid):
                continue
            results.append(
                ElementSearchResult(
                    element_type=etype,
                    element_id=eid,
                    index=i,
                )
            )

    return results
