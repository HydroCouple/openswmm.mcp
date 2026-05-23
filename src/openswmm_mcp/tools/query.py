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
    ConduitGeometry,
    CrossSectionInfo,
    ElementSearchResult,
    GageInfo,
    LinkInfo,
    NodeInfo,
    OrificeGeometry,
    OutfallGeometry,
    PollutantInfo,
    PumpGeometry,
    StorageGeometry,
    SubcatchmentInfo,
    SystemSummary,
    WeirGeometry,
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
_OUTFALL_TYPE_NAMES = {0: "FREE", 1: "NORMAL", 2: "FIXED", 3: "TIDAL", 4: "TIMESERIES"}

# Cross-section shape codes → names
_XSECT_SHAPE_NAMES: dict[int, str] = {
    0: "CIRCULAR",
    1: "FILLED_CIRCULAR",
    2: "RECT_CLOSED",
    3: "RECT_OPEN",
    4: "TRAPEZOIDAL",
    5: "TRIANGULAR",
    6: "PARABOLIC",
    7: "POWER",
    8: "MODBASKETHANDLE",
    9: "EGGSHAPED",
    10: "HORSESHOE",
    11: "GOTHIC",
    12: "CATENARY",
    13: "SEMIELLIPTICAL",
    14: "BASKETHANDLE",
    15: "SEMICIRCULAR",
    16: "IRREGULAR",
    17: "CUSTOM",
    18: "FORCE_MAIN",
}

# Per-shape geometry parameter labels (geom1, geom2, geom3, geom4)
_XSECT_GEOM_LABELS: dict[int, tuple[str, ...]] = {
    0: ("diameter",),
    1: ("diameter", "filled_depth"),
    2: ("height", "width"),
    3: ("height", "width"),
    4: ("height", "bottom_width", "side_slope"),
    5: ("height", "top_width"),
    6: ("height", "top_width"),
    7: ("height", "top_width", "exponent"),
    8: ("height", "bottom_width", "top_radius"),
    9: ("height",),
    10: ("height",),
    11: ("height",),
    12: ("height",),
    13: ("height",),
    14: ("height",),
    15: ("height",),
    16: ("transect_index",),
    17: ("shape_curve_index",),
    18: ("diameter", "roughness"),
}


def _make_xsect_info(shape: int, g1: float, g2: float, g3: float, g4: float) -> CrossSectionInfo:
    """Build a CrossSectionInfo from raw C API tuple values."""
    labels = _XSECT_GEOM_LABELS.get(shape, ("geom1", "geom2", "geom3", "geom4"))
    values = (g1, g2, g3, g4)
    geom_labels = {label: values[i] for i, label in enumerate(labels)}
    return CrossSectionInfo(
        shape=shape,
        shape_name=_XSECT_SHAPE_NAMES.get(shape, f"SHAPE_{shape}"),
        geom1=g1,
        geom2=g2,
        geom3=g3,
        geom4=g4,
        geom_labels=geom_labels,
    )


# ---------------------------------------------------------------------------
# Internal builders
# ---------------------------------------------------------------------------


async def _build_node_info(session, nodes, index: int) -> NodeInfo:
    """Build a NodeInfo for a single node by index, including all geometry."""
    # Fetch basic identity + all static geometry concurrently
    (
        node_id,
        type_code,
        invert,
        max_depth,
        crown_elev,
        full_volume,
        surcharge_depth,
        ponded_area,
        degree,
        initial_depth,
        losses,
        outflow,
    ) = await asyncio.gather(
        asyncio.to_thread(nodes.get_id, index),
        asyncio.to_thread(nodes.get_type, index),
        asyncio.to_thread(nodes.get_invert_elev, index),
        asyncio.to_thread(nodes.get_max_depth, index),
        asyncio.to_thread(nodes.get_crown_elev, index),
        asyncio.to_thread(nodes.get_full_volume, index),
        asyncio.to_thread(nodes.get_surcharge_depth, index),
        asyncio.to_thread(nodes.get_ponded_area, index),
        asyncio.to_thread(nodes.get_degree, index),
        asyncio.to_thread(nodes.get_initial_depth, index),
        asyncio.to_thread(nodes.get_losses, index),
        asyncio.to_thread(nodes.get_outflow, index),
    )

    # Runtime hydraulic state
    depth: float | None = None
    head: float | None = None
    volume: float | None = None
    lat_inflow: float | None = None
    overflow: float | None = None

    if session.state in ("running", "ended"):
        try:
            depth, head, volume, lat_inflow, overflow = await asyncio.gather(
                asyncio.to_thread(nodes.get_depth, index),
                asyncio.to_thread(nodes.get_head, index),
                asyncio.to_thread(nodes.get_volume, index),
                asyncio.to_thread(nodes.get_lateral_inflow, index),
                asyncio.to_thread(nodes.get_overflow, index),
            )
        except Exception:
            pass

    outfall_route_to: int | None = None
    if hasattr(nodes, "get_outfall_route_to"):
        try:
            rt = await asyncio.to_thread(nodes.get_outfall_route_to, index)
            if rt >= 0:
                outfall_route_to = rt
        except Exception:
            pass

    # Type-specific geometry
    storage_geom: StorageGeometry | None = None
    outfall_geom: OutfallGeometry | None = None

    if type_code == 2:  # STORAGE
        try:
            curve_idx, (fa, fb, fc), seep_rate = await asyncio.gather(
                asyncio.to_thread(nodes.get_storage_curve, index),
                asyncio.to_thread(nodes.get_storage_functional, index),
                asyncio.to_thread(nodes.get_storage_seep_rate, index),
            )
            exfil_suction = exfil_ksat = exfil_imd = None
            try:
                exfil_suction, exfil_ksat, exfil_imd = await asyncio.to_thread(
                    nodes.get_exfil_params, index
                )
            except Exception:
                pass
            storage_geom = StorageGeometry(
                storage_type="curve" if curve_idx >= 0 else "functional",
                curve_idx=curve_idx if curve_idx >= 0 else None,
                functional_a=fa,
                functional_b=fb,
                functional_c=fc,
                seep_rate=seep_rate,
                exfil_suction=exfil_suction,
                exfil_ksat=exfil_ksat,
                exfil_imd=exfil_imd,
            )
        except Exception:
            pass

    elif type_code == 1:  # OUTFALL
        try:
            outfall_type, outfall_param, flap_gate = await asyncio.gather(
                asyncio.to_thread(nodes.get_outfall_type, index),
                asyncio.to_thread(nodes.get_outfall_param, index),
                asyncio.to_thread(nodes.get_outfall_flap_gate, index),
            )
            outfall_geom = OutfallGeometry(
                outfall_type=outfall_type,
                outfall_type_name=_OUTFALL_TYPE_NAMES.get(outfall_type, f"TYPE_{outfall_type}"),
                param=outfall_param,
                flap_gate=bool(flap_gate),
            )
        except Exception:
            pass

    return NodeInfo(
        node_id=node_id,
        index=index,
        node_type=_NODE_TYPE_NAMES.get(type_code, f"UNKNOWN({type_code})"),
        invert_elev=invert,
        max_depth=max_depth,
        crown_elev=crown_elev,
        full_volume=full_volume,
        surcharge_depth=surcharge_depth,
        ponded_area=ponded_area,
        degree=degree,
        initial_depth=initial_depth,
        losses=losses,
        outflow=outflow,
        storage=storage_geom,
        outfall=outfall_geom,
        depth=depth,
        head=head,
        volume=volume,
        lateral_inflow=lat_inflow,
        overflow=overflow,
        outfall_route_to=outfall_route_to,
    )


async def _build_link_info(session, links, nodes, index: int) -> LinkInfo:
    """Build a LinkInfo for a single link by index, including full geometry."""
    # Fetch all common static properties concurrently
    (
        link_id,
        type_code,
        from_node_idx,
        to_node_idx,
        length,
        roughness,
        slope,
        offset_up,
        offset_dn,
        xsect_raw,
    ) = await asyncio.gather(
        asyncio.to_thread(links.get_id, index),
        asyncio.to_thread(links.get_type, index),
        asyncio.to_thread(links.get_from_node, index),
        asyncio.to_thread(links.get_to_node, index),
        asyncio.to_thread(links.get_length, index),
        asyncio.to_thread(links.get_roughness, index),
        asyncio.to_thread(links.get_slope, index),
        asyncio.to_thread(links.get_offset_up, index),
        asyncio.to_thread(links.get_offset_dn, index),
        asyncio.to_thread(links.get_xsect, index),  # → (shape, g1, g2, g3, g4)
    )
    from_node_id, to_node_id = await asyncio.gather(
        asyncio.to_thread(nodes.get_id, from_node_idx),
        asyncio.to_thread(nodes.get_id, to_node_idx),
    )

    # Build cross-section info from raw tuple
    xsect_info: CrossSectionInfo | None = None
    if xsect_raw is not None:
        try:
            xsect_info = _make_xsect_info(*xsect_raw)
        except Exception:
            pass

    # Type-specific geometry
    conduit_geom: ConduitGeometry | None = None
    weir_geom: WeirGeometry | None = None
    orifice_geom: OrificeGeometry | None = None
    pump_geom: PumpGeometry | None = None
    initial_flow: float | None = None
    max_flow: float | None = None

    if type_code == 0:  # CONDUIT
        try:
            loss_raw, flap_gate, seep_rate, culvert_code, barrels = await asyncio.gather(
                asyncio.to_thread(links.get_loss_coeff, index),  # → (inlet, outlet, avg)
                asyncio.to_thread(links.get_flap_gate, index),
                asyncio.to_thread(links.get_seep_rate, index),
                asyncio.to_thread(links.get_culvert_code, index),
                asyncio.to_thread(links.get_barrels, index),
            )
            conduit_geom = ConduitGeometry(
                xsect=xsect_info,
                slope=slope,
                offset_up=offset_up,
                offset_dn=offset_dn,
                initial_flow=0.0,
                max_flow=0.0,
                loss_coeff_inlet=loss_raw[0],
                loss_coeff_outlet=loss_raw[1],
                loss_coeff_avg=loss_raw[2],
                flap_gate=bool(flap_gate),
                seep_rate=seep_rate,
                culvert_code=culvert_code,
                barrels=barrels,
            )
        except Exception:
            pass

    elif type_code == 3:  # WEIR
        try:
            crest_height, discharge_coeff, end_contractions = await asyncio.gather(
                asyncio.to_thread(links.get_crest_height, index),
                asyncio.to_thread(links.get_discharge_coeff, index),
                asyncio.to_thread(links.get_end_contractions, index),
            )
            weir_geom = WeirGeometry(
                xsect=xsect_info,
                crest_height=crest_height,
                discharge_coeff=discharge_coeff,
                end_contractions=end_contractions,
                offset_up=offset_up,
                offset_dn=offset_dn,
            )
        except Exception:
            pass

    elif type_code == 2:  # ORIFICE
        orifice_geom = OrificeGeometry(
            xsect=xsect_info,
            offset_up=offset_up,
            offset_dn=offset_dn,
        )

    elif type_code == 1:  # PUMP
        try:
            pump_curve_idx, init_state = await asyncio.gather(
                asyncio.to_thread(links.get_pump_curve, index),
                asyncio.to_thread(links.get_pump_init_state, index),
            )
            pump_geom = PumpGeometry(
                pump_curve_idx=pump_curve_idx,
                init_state_on=bool(init_state),
                offset_up=offset_up,
                offset_dn=offset_dn,
            )
        except Exception:
            pass

    # Runtime hydraulic state
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
            flow, depth, velocity, capacity = await asyncio.gather(
                asyncio.to_thread(links.get_flow, index),
                asyncio.to_thread(links.get_depth, index),
                asyncio.to_thread(links.get_velocity, index),
                asyncio.to_thread(links.get_capacity, index),
            )
        except Exception:
            pass

        if hasattr(links, "get_hyd_power"):
            try:
                hydraulic_power = await asyncio.to_thread(links.get_hyd_power, index)
            except Exception:
                pass

        if type_code == 1 and hasattr(links, "get_stat_pump_cycles"):
            try:
                pump_cycles, pump_on_time, pump_volume = await asyncio.gather(
                    asyncio.to_thread(links.get_stat_pump_cycles, index),
                    asyncio.to_thread(links.get_stat_pump_on_time, index),
                    asyncio.to_thread(links.get_stat_pump_volume, index),
                )
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
        slope=slope,
        offset_up=offset_up,
        offset_dn=offset_dn,
        initial_flow=initial_flow,
        max_flow=max_flow,
        xsect=xsect_info,
        conduit=conduit_geom,
        weir=weir_geom,
        orifice=orifice_geom,
        pump=pump_geom,
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

    # Return all nodes — build each concurrently (each _build_node_info uses gather internally)
    count = await asyncio.to_thread(nodes.count)
    if count == 0:
        return []

    results = await asyncio.gather(*[_build_node_info(session, nodes, i) for i in range(count)])
    return list(results)


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

    # Return all links — build each concurrently (each _build_link_info uses gather internally)
    count = await asyncio.to_thread(links.count)
    if count == 0:
        return []

    results = await asyncio.gather(
        *[_build_link_info(session, links, nodes, i) for i in range(count)]
    )
    return list(results)


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


_POLLUTANT_UNITS_NAMES = {0: "MG/L", 1: "UG/L", 2: "#/L"}


async def _build_pollutant_info(pollutants, index: int) -> PollutantInfo:
    """Build a PollutantInfo for a single pollutant by index."""
    (
        poll_id,
        units,
        kdecay,
        rain_conc,
        gw_conc,
        init_conc,
        rdii_conc,
        mwt,
        snow_only,
    ) = await asyncio.gather(
        asyncio.to_thread(pollutants.get_id, index),
        asyncio.to_thread(pollutants.get_units, index),
        asyncio.to_thread(pollutants.get_kdecay, index),
        asyncio.to_thread(pollutants.get_rain_conc, index),
        asyncio.to_thread(pollutants.get_gw_conc, index),
        asyncio.to_thread(pollutants.get_init_conc, index),
        asyncio.to_thread(pollutants.get_rdii_conc, index),
        asyncio.to_thread(pollutants.get_mwt, index),
        asyncio.to_thread(pollutants.get_snow_only, index),
    )
    co_idx = co_frac = 0
    try:
        co_idx, co_frac = await asyncio.to_thread(pollutants.get_co_pollutant, index)
    except Exception:
        pass

    return PollutantInfo(
        pollutant_id=poll_id,
        index=index,
        units=units,
        units_name=_POLLUTANT_UNITS_NAMES.get(units, f"UNKNOWN({units})"),
        kdecay=kdecay,
        rain_conc=rain_conc,
        gw_conc=gw_conc,
        init_conc=init_conc,
        rdii_conc=rdii_conc,
        mwt=mwt,
        snow_only=bool(snow_only),
        co_pollutant_idx=co_idx,
        co_pollutant_frac=co_frac,
    )


@query_mcp.tool
async def get_pollutant_info(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | None = None,
) -> PollutantInfo | list[PollutantInfo]:
    """Return definition and properties for one or all pollutants.

    When *pollutant_id* is given, returns a single PollutantInfo.  When
    omitted, returns a list of PollutantInfo for every pollutant in the model.
    Valid in any non-closed session state.
    """
    from openswmm.engine import Pollutants as _Pollutants

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended", "opened", "building")

    # In 'building' state there is no backend yet — access via model_builder.
    if session.state == "building":
        if session.model_builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no ModelBuilder."
            )
        pollutants = _Pollutants(session.model_builder)
    else:
        pollutants = session.pollutants

    if pollutant_id is not None:
        idx = await asyncio.to_thread(pollutants.get_index, pollutant_id)
        if idx < 0:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] Pollutant '{pollutant_id}' not found."
            )
        return await _build_pollutant_info(pollutants, idx)

    count = await asyncio.to_thread(pollutants.count)
    return [await _build_pollutant_info(pollutants, i) for i in range(count)]
