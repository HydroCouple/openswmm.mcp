"""Query tools for the OpenSWMM MCP server.

Provides read-only tools for inspecting model elements (nodes, links,
subcatchments, gages) and searching across the model.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import get_session_manager, require_state
from openswmm_mcp._util.formatting import paginate_list
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
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


def _build_node_info_sync(session, nodes, index: int) -> NodeInfo:
    """Build NodeInfo synchronously by holding the Node wrapper once.

    Designed to be wrapped in a single ``asyncio.to_thread`` — replaces
    the previous ~17 ``asyncio.gather`` thread submissions with one C-ABI
    crossing per attribute via the v1 property surface.

    Sub-views (``.storage``, ``.outfall``) are accessed only when the node
    type matches; everything else is direct ``node.<attr>`` access.
    """
    node = nodes[index]

    node_id = node.id
    type_code = int(node.type)
    invert = node.invert_elev
    max_depth = node.max_depth
    crown_elev = node.crown_elev
    full_volume = node.full_volume
    surcharge_depth = node.surcharge_depth
    ponded_area = node.ponded_area
    degree = node.degree
    initial_depth = node.initial_depth
    losses = node.losses
    outflow = node.outflow

    # Runtime hydraulic state — only meaningful after start().  v1 exposes
    # them on every node, but read attempts during "opened" / "initialized"
    # can return zeros; we still gate on session.state to match the
    # previous behaviour exactly.
    depth: float | None = None
    head: float | None = None
    volume: float | None = None
    lat_inflow: float | None = None
    overflow: float | None = None

    if session.state in ("running", "ended"):
        try:
            depth = node.depth
            head = node.head
            volume = node.volume
            lat_inflow = node.lateral_inflow
            overflow = node.overflow
        except Exception:
            pass

    # Type-specific geometry (sub-views raise AttributeError on the wrong
    # type, so guard with the type code).
    storage_geom: StorageGeometry | None = None
    outfall_geom: OutfallGeometry | None = None
    outfall_route_to: int | None = None

    if type_code == 2:  # STORAGE
        try:
            storage = node.storage
            curve_idx = storage.curve
            fa, fb, fc = storage.functional
            seep_rate = storage.seep_rate
            exfil_suction = exfil_ksat = exfil_imd = None
            try:
                exfil_suction, exfil_ksat, exfil_imd = storage.exfil_params
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
            outfall = node.outfall
            outfall_type = int(outfall.type)
            outfall_param = outfall.param
            flap_gate = outfall.flap_gate
            try:
                rt = outfall.route_to
                if rt >= 0:
                    outfall_route_to = int(rt)
            except Exception:
                pass
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


async def _build_node_info(session, nodes, index: int) -> NodeInfo:
    """Async wrapper around :func:`_build_node_info_sync`.

    Single ``to_thread`` containing every attribute read for one node.
    """
    return await asyncio.to_thread(_build_node_info_sync, session, nodes, index)


# ---------------------------------------------------------------------------
# Phase 4: bulk all-node-info builder
# ---------------------------------------------------------------------------
#
# ``_build_node_info`` is fine for a single-node query (it issues at most
# ~17 ``asyncio.to_thread`` calls, which is negligible).  But for "all
# nodes" mode the original ``get_node_info`` looped ``asyncio.gather`` over
# every node — N × 17 thread submissions on a 5 000-node network is roughly
# 85 000 context switches just to assemble a static-geometry snapshot.
#
# ``_build_all_node_infos_sync`` collapses that into a single ``to_thread``:
# the Phase 3 bulk getters (``get_volumes_bulk`` / ``get_outflows_bulk`` /
# …) fill NumPy arrays for the runtime state in O(n_nodes) total C work,
# and the static scalar accessors are looped *inside* the same worker
# thread (no Python-side hops between them).  The result is one thread
# submission per ``get_node_info`` call regardless of model size.


def _build_all_node_infos_sync(session, nodes) -> list[NodeInfo]:
    """Single-thread bulk assembly of NodeInfo for every node.

    Designed to be wrapped in **one** ``asyncio.to_thread`` so the entire
    network snapshot is built in a single worker hop.

    Bulk-vs-scalar split:

      * Runtime state (depths, heads, volumes, overflows, lateral_inflows,
        losses, outflows) — fetched via v1 numpy-array properties on the
        collection (``nodes.depths``, ``nodes.heads``, …); one C call per
        quantity over the whole network.
      * Static geometry (invert, max_depth, crown_elev, …) — looped per
        node via property access on the wrapper; no thread hops occur
        inside the loop (we are already inside the ``to_thread`` worker).
      * Type-specific geometry (storage / outfall blocks) — populated only
        for nodes whose type matches.
    """
    n = len(nodes)
    if n == 0:
        return []

    meta = session.meta
    ids = meta.node_ids  # cached after first call this session

    # ---- runtime state via bulk numpy properties -----------------------
    running = session.state in ("running", "ended")
    depths = heads = volumes = lats = overflows = None
    losses_bulk = outflows_bulk = None
    if running:
        try:
            depths = nodes.depths
            heads = nodes.heads
            volumes = nodes.volumes
            lats = nodes.lateral_inflows
            overflows = nodes.overflows
            losses_bulk = nodes.losses
            outflows_bulk = nodes.outflows
        except AttributeError:
            # Legacy backend doesn't surface these as bulk; the
            # per-node scalar fallback below handles it.
            depths = heads = volumes = lats = overflows = None
            losses_bulk = outflows_bulk = None
        except Exception:
            depths = heads = volumes = lats = overflows = None
            losses_bulk = outflows_bulk = None

    results: list[NodeInfo] = []
    for i in range(n):
        node = nodes[i]
        type_code = int(node.type)

        # Static geometry — direct property access, no thread hops.
        invert = node.invert_elev
        max_depth = node.max_depth
        crown_elev = node.crown_elev
        full_volume = node.full_volume
        surcharge_depth = node.surcharge_depth
        ponded_area = node.ponded_area
        degree = node.degree
        initial_depth = node.initial_depth
        # Losses / outflow: prefer the bulk array if we have it.
        losses_val = float(losses_bulk[i]) if losses_bulk is not None else node.losses
        outflow_val = float(outflows_bulk[i]) if outflows_bulk is not None else node.outflow

        # Runtime state — prefer bulks, scalar fallback for legacy.
        depth = float(depths[i]) if depths is not None else (node.depth if running else None)
        head = float(heads[i]) if heads is not None else (node.head if running else None)
        volume = (float(volumes[i]) if volumes is not None
                  else (node.volume if running else None))
        lat_inflow = (float(lats[i]) if lats is not None
                      else (node.lateral_inflow if running else None))
        overflow = (float(overflows[i]) if overflows is not None
                    else (node.overflow if running else None))

        outfall_route_to: int | None = None
        storage_geom: StorageGeometry | None = None
        outfall_geom: OutfallGeometry | None = None

        if type_code == 2:  # STORAGE
            try:
                storage = node.storage
                curve_idx = storage.curve
                fa, fb, fc = storage.functional
                seep_rate = storage.seep_rate
                exfil_suction = exfil_ksat = exfil_imd = None
                try:
                    exfil_suction, exfil_ksat, exfil_imd = storage.exfil_params
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
                outfall = node.outfall
                outfall_type = int(outfall.type)
                outfall_param = outfall.param
                flap_gate = outfall.flap_gate
                try:
                    rt = outfall.route_to
                    if rt >= 0:
                        outfall_route_to = int(rt)
                except Exception:
                    pass
                outfall_geom = OutfallGeometry(
                    outfall_type=outfall_type,
                    outfall_type_name=_OUTFALL_TYPE_NAMES.get(
                        outfall_type, f"TYPE_{outfall_type}"),
                    param=outfall_param,
                    flap_gate=bool(flap_gate),
                )
            except Exception:
                pass

        results.append(NodeInfo(
            node_id=ids[i],
            index=i,
            node_type=_NODE_TYPE_NAMES.get(type_code, f"UNKNOWN({type_code})"),
            invert_elev=invert,
            max_depth=max_depth,
            crown_elev=crown_elev,
            full_volume=full_volume,
            surcharge_depth=surcharge_depth,
            ponded_area=ponded_area,
            degree=degree,
            initial_depth=initial_depth,
            losses=losses_val,
            outflow=outflow_val,
            storage=storage_geom,
            outfall=outfall_geom,
            depth=depth,
            head=head,
            volume=volume,
            lateral_inflow=lat_inflow,
            overflow=overflow,
            outfall_route_to=outfall_route_to,
        ))
    return results


def _build_link_info_sync(session, links, nodes, index: int) -> LinkInfo:
    """Build LinkInfo synchronously via v1 property access on the wrapper.

    Designed to be wrapped in a single ``asyncio.to_thread``.  ``nodes``
    is kept as a parameter for backward signature compatibility; v1
    ``link.from_node`` already returns a Node wrapper exposing ``.id``,
    so we don't need to do a separate ``nodes.get_id(idx)`` round-trip.
    """
    link = links[index]

    link_id = link.id
    type_code = int(link.type)
    from_node_wrap = link.from_node
    to_node_wrap = link.to_node
    from_node_id = from_node_wrap.id
    to_node_id = to_node_wrap.id
    length = link.length
    roughness = link.roughness
    slope = link.slope
    offset_up = link.offset_up
    offset_dn = link.offset_dn

    # Cross-section: v1 returns an XSection object with as_tuple().
    xsect_info: CrossSectionInfo | None = None
    try:
        shape, g1, g2, g3, g4 = link.xsect.as_tuple()
        xsect_info = _make_xsect_info(int(shape), g1, g2, g3, g4)
    except Exception:
        pass

    # Type-specific geometry
    conduit_geom: ConduitGeometry | None = None
    weir_geom: WeirGeometry | None = None
    orifice_geom: OrificeGeometry | None = None
    pump_geom: PumpGeometry | None = None

    if type_code == 0:  # CONDUIT
        try:
            loss_raw = link.loss_coeff  # tuple (inlet, outlet, avg)
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
                flap_gate=bool(link.flap_gate),
                seep_rate=link.seep_rate,
                culvert_code=link.culvert_code,
                barrels=link.barrels,
            )
        except Exception:
            pass

    elif type_code == 3:  # WEIR
        try:
            weir = link.weir
            weir_geom = WeirGeometry(
                xsect=xsect_info,
                crest_height=weir.crest_height,
                discharge_coeff=weir.discharge_coeff,
                end_contractions=weir.end_contractions,
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
            pump = link.pump
            pump_geom = PumpGeometry(
                pump_curve_idx=pump.curve,
                init_state_on=bool(pump.init_state),
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
            flow = link.flow
            depth = link.depth
            velocity = link.velocity
            capacity = link.capacity
        except Exception:
            pass

        try:
            hydraulic_power = link.hyd_power
        except Exception:
            pass

        if type_code == 1:  # PUMP — read stats from the sub-view.
            try:
                stats = link.stats
                pump_cycles = int(stats.pump_cycles)
                pump_on_time = float(stats.pump_on_time)
                pump_volume = float(stats.pump_volume)
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
        initial_flow=None,
        max_flow=None,
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


async def _build_link_info(session, links, nodes, index: int) -> LinkInfo:
    """Async wrapper around :func:`_build_link_info_sync`."""
    return await asyncio.to_thread(_build_link_info_sync, session, links, nodes, index)


# ---------------------------------------------------------------------------
# Phase 4b: bulk all-link-info builder
# ---------------------------------------------------------------------------
#
# Mirrors ``_build_all_node_infos_sync`` for links. The old all-mode path
# launched N independent ``_build_link_info`` coroutines, each of which
# fired ~10–13 ``asyncio.to_thread`` calls. For a 5 000-link network that
# is ~50 000 context switches per ``get_link_info`` call. The bulk
# version does ONE ``to_thread`` and uses Phase 3 link bulks for runtime
# state (flows, depths, velocities, capacities, hyd_powers, pump stats).


def _build_all_link_infos_sync(session, links, nodes) -> list[LinkInfo]:
    """Single-thread bulk assembly of LinkInfo for every link.

    Designed to be wrapped in **one** ``asyncio.to_thread``.  See
    :func:`_build_all_node_infos_sync` for the rationale; this is the link
    analogue.

    Bulk-vs-scalar split:

      * Runtime state — fetched via v1 numpy-array properties on the
        collection (``links.flows``, ``links.depths``, ``links.velocities``,
        ``links.capacities``, ``links.hyd_powers``) and ``links.pump_stats()``
        for the pump triple.
      * Identifiers — ``session.meta.link_ids`` / ``node_ids`` (cached).
      * Static geometry and per-type blocks (conduit / weir / orifice /
        pump) — looped via v1 property access on the wrapper.
    """
    meta = session.meta
    n = meta.n_links
    if n == 0:
        return []

    link_ids = meta.link_ids
    node_ids = meta.node_ids

    # ---- runtime bulks (no-ops when not running, fall back on legacy) --
    running = session.state in ("running", "ended")
    flows = depths = velocities = capacities = hyd_powers = None
    pump_stats: tuple[Any, Any, Any] | None = None
    if running:
        try:
            flows = links.flows
            depths = links.depths
            velocities = links.velocities
            capacities = links.capacities
            hyd_powers = links.hyd_powers
        except AttributeError:
            # Legacy backend — scalar fallback per link below.
            flows = depths = velocities = capacities = hyd_powers = None
        except Exception:
            flows = depths = velocities = capacities = hyd_powers = None
        # Pump stats live behind a method, not a property, in v1.
        pump_stats_fn = getattr(links, "pump_stats", None)
        if callable(pump_stats_fn):
            try:
                pump_stats = pump_stats_fn()
            except Exception:
                pump_stats = None

    results: list[LinkInfo] = []
    for i in range(n):
        link = links[i]
        type_code = int(link.type)

        # Identifiers — use the v1 from_node/to_node wrappers for ids when
        # the cached node_ids array is too short (defensive).
        try:
            from_idx = link.from_node.index
        except AttributeError:
            from_idx = -1
        try:
            to_idx = link.to_node.index
        except AttributeError:
            to_idx = -1
        from_node_id = (node_ids[from_idx]
                        if 0 <= from_idx < len(node_ids) else "")
        to_node_id = (node_ids[to_idx]
                      if 0 <= to_idx < len(node_ids) else "")

        length = link.length
        roughness = link.roughness
        slope = link.slope
        offset_up = link.offset_up
        offset_dn = link.offset_dn

        xsect_info: CrossSectionInfo | None = None
        try:
            shape, g1, g2, g3, g4 = link.xsect.as_tuple()
            xsect_info = _make_xsect_info(int(shape), g1, g2, g3, g4)
        except Exception:
            pass

        # Per-type geometry block.
        conduit_geom: ConduitGeometry | None = None
        weir_geom: WeirGeometry | None = None
        orifice_geom: OrificeGeometry | None = None
        pump_geom: PumpGeometry | None = None
        if type_code == 0:  # CONDUIT
            try:
                loss_raw = link.loss_coeff
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
                    flap_gate=bool(link.flap_gate),
                    seep_rate=link.seep_rate,
                    culvert_code=link.culvert_code,
                    barrels=link.barrels,
                )
            except Exception:
                pass
        elif type_code == 3:  # WEIR
            try:
                weir = link.weir
                weir_geom = WeirGeometry(
                    xsect=xsect_info,
                    crest_height=weir.crest_height,
                    discharge_coeff=weir.discharge_coeff,
                    end_contractions=weir.end_contractions,
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
                pump = link.pump
                pump_geom = PumpGeometry(
                    pump_curve_idx=pump.curve,
                    init_state_on=bool(pump.init_state),
                    offset_up=offset_up,
                    offset_dn=offset_dn,
                )
            except Exception:
                pass

        # Runtime state — prefer bulks, scalar fallback.
        flow = float(flows[i]) if flows is not None else (
            link.flow if running else None)
        depth = float(depths[i]) if depths is not None else (
            link.depth if running else None)
        velocity = float(velocities[i]) if velocities is not None else (
            link.velocity if running else None)
        capacity = float(capacities[i]) if capacities is not None else (
            link.capacity if running else None)
        hydraulic_power = float(hyd_powers[i]) if hyd_powers is not None else None
        if hydraulic_power is None and running:
            try:
                hydraulic_power = link.hyd_power
            except Exception:
                pass

        pump_cycles = pump_on_time = pump_volume = None
        if type_code == 1 and running:
            if pump_stats is not None:
                try:
                    cycles_arr, on_time_arr, vol_arr = pump_stats
                    c = int(cycles_arr[i])
                    if c >= 0:
                        pump_cycles = c
                        pump_on_time = float(on_time_arr[i])
                        pump_volume = float(vol_arr[i])
                except Exception:
                    pass
            else:
                try:
                    stats = link.stats
                    pump_cycles = int(stats.pump_cycles)
                    pump_on_time = float(stats.pump_on_time)
                    pump_volume = float(stats.pump_volume)
                except Exception:
                    pass

        results.append(LinkInfo(
            link_id=link_ids[i],
            index=i,
            link_type=_LINK_TYPE_NAMES.get(type_code, f"UNKNOWN({type_code})"),
            from_node=from_node_id,
            to_node=to_node_id,
            length=length,
            roughness=roughness,
            slope=slope,
            offset_up=offset_up,
            offset_dn=offset_dn,
            initial_flow=None,
            max_flow=None,
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
        ))
    return results


def _build_subcatch_info_sync(session, subcatchments, index: int) -> SubcatchmentInfo:
    """Build SubcatchmentInfo synchronously via v1 property access."""
    sub = subcatchments[index]
    sc_id = sub.id
    area = sub.area
    imperv = sub.imperv_pct
    slope = sub.slope
    width = sub.width

    # Runtime state
    rainfall: float | None = None
    runoff: float | None = None
    depth: float | None = None

    if session.state in ("running", "ended"):
        try:
            rainfall = sub.rainfall
            runoff = sub.runoff
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


async def _build_subcatch_info(session, subcatchments, index: int) -> SubcatchmentInfo:
    return await asyncio.to_thread(_build_subcatch_info_sync, session, subcatchments, index)


def _build_gage_info_sync(session, gages, index: int) -> GageInfo:
    """Build GageInfo synchronously via v1 property access.

    Legacy backend returns plain ints for data_source / rain_type since
    those aren't surfaced via the toolkit; v1 returns enum members.
    Coerce both to int for the lookup table.
    """
    gage = gages[index]
    gage_id = gage.id
    try:
        source_code = int(gage.data_source)
    except AttributeError:
        source_code = 0
    try:
        rain_type_code = int(gage.rain_type)
    except AttributeError:
        rain_type_code = 0

    rainfall: float | None = None
    if session.state in ("running", "ended"):
        try:
            rainfall = gage.rainfall
        except Exception:
            pass

    return GageInfo(
        gage_id=gage_id,
        index=index,
        data_source=_GAGE_SOURCE_NAMES.get(source_code, f"UNKNOWN({source_code})"),
        rain_type=_GAGE_RAIN_NAMES.get(rain_type_code, f"UNKNOWN({rain_type_code})"),
        rainfall=rainfall,
    )


async def _build_gage_info(session, gages, index: int) -> GageInfo:
    return await asyncio.to_thread(_build_gage_info_sync, session, gages, index)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@query_mcp.tool
async def get_node_info(
    ctx: Context,
    session_id: str = "default",
    node_id: str | None = None,
    properties: list[str] | None = None,
    start_index: int = 0,
    limit: int | None = None,
) -> NodeInfo | list[NodeInfo]:
    """Return properties and state for one or all nodes.

    When *node_id* is given, returns a single :class:`NodeInfo`. When
    omitted, returns a list of :class:`NodeInfo` for every node in the
    model.  The optional *properties* list filters which fields are
    included (not yet implemented; reserved for future optimisation).

    Phase 4d adds ``start_index`` and ``limit`` for paginated reads of
    the all-mode response.  Both default to "no pagination" (return
    every node).  Pagination is applied **after** the bulk fetch — the
    underlying engine still does one pass over the entire network
    regardless of slice — so callers can safely make many small paged
    calls without re-paying the bulk-fetch cost beyond the per-call
    Python-side slice.

    Parameters
    ----------
    start_index:
        Zero-based offset of the first node to include in the response.
        Negative values are clamped to ``0``.
    limit:
        Maximum number of nodes returned.  ``None`` (the default) means
        "no limit"; non-positive values produce an empty list.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended")

    nodes = session.nodes

    if node_id is not None:
        idx = await resolve_index(nodes, node_id, "Node")
        if idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found.")
        return await _build_node_info(session, nodes, idx)

    # All-nodes mode (Phase 4): one ``to_thread`` that performs every C call
    # inside the worker — bulk getters for runtime state (Phase 3) and an
    # in-thread scalar loop for static geometry.  Replaces the previous
    # ``asyncio.gather`` over N * ~17 ``to_thread`` submissions, which became
    # the dominant cost on large networks (5 000+ nodes).
    all_infos = await asyncio.to_thread(_build_all_node_infos_sync, session, nodes)
    # Phase 4d pagination — applied after the bulk fetch so the engine
    # work is amortised across paginated polls.
    if start_index == 0 and limit is None:
        return all_infos
    sliced, _meta = paginate_list(all_infos, start_index=start_index, limit=limit)
    return sliced


@query_mcp.tool
async def get_link_info(
    ctx: Context,
    session_id: str = "default",
    link_id: str | None = None,
    start_index: int = 0,
    limit: int | None = None,
) -> LinkInfo | list[LinkInfo]:
    """Return properties and state for one or all links.

    When *link_id* is given, returns a single :class:`LinkInfo`. When
    omitted, returns a list of :class:`LinkInfo` for every link in the
    model.

    Phase 4d adds ``start_index`` / ``limit`` for paginated reads of the
    all-mode response, mirroring :func:`get_node_info`.

    Parameters
    ----------
    start_index:
        Zero-based offset of the first link to include.
    limit:
        Maximum number of links returned, or ``None`` for "no limit".
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended")

    links = session.links
    nodes = session.nodes

    if link_id is not None:
        idx = await resolve_index(links, link_id, "Link")
        if idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found.")
        return await _build_link_info(session, links, nodes, idx)

    # All-links mode (Phase 4b): one ``to_thread`` that uses the Phase 3
    # link bulks for runtime state and an in-thread scalar loop for
    # static geometry / per-type blocks. Replaces N x ~10 ``to_thread``
    # submissions; see ``_build_all_link_infos_sync`` for the breakdown.
    all_infos = await asyncio.to_thread(
        _build_all_link_infos_sync, session, links, nodes)
    if start_index == 0 and limit is None:
        return all_infos
    sliced, _meta = paginate_list(all_infos, start_index=start_index, limit=limit)
    return sliced


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
        idx = await resolve_index(subcatchments, subcatch_id, "Subcatchment")
        if idx < 0:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{subcatch_id}' not found."
            )
        return await _build_subcatch_info(session, subcatchments, idx)

    # Return all subcatchments
    count = await asyncio.to_thread(lambda: len(subcatchments))
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
        idx = await resolve_index(gages, gage_id, "Gage")
        if idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Gage '{gage_id}' not found.")
        return await _build_gage_info(session, gages, idx)

    # Return all gages
    count = await asyncio.to_thread(lambda: len(gages))
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

    _FLOW_UNITS = {0: "CFS", 1: "GPM", 2: "MGD", 3: "CMS", 4: "LPS", 5: "MLD"}
    _ROUTE_MODELS = {0: "STEADY", 1: "KINWAVE", 2: "DYNWAVE"}

    def _summary_static() -> dict[str, Any]:
        """Single-thread sweep of every static field.

        Pulls counts, timing, and option lookups in one to_thread hop so
        we don't pay ~12 worker submissions for what is effectively
        constant data.
        """
        node_count = len(nodes)
        link_count = len(links)
        subcatch_count = len(subcatchments)
        gage_count = len(gages)
        pollutant_count = len(pollutants)

        # v1: solver.start_datetime / end_datetime / current_datetime are
        # datetime objects; routing_step is timedelta.  The JSON wire
        # format wants floats (days since start, seconds), so we convert
        # here at the boundary.
        start_dt = solver.start_datetime
        end_dt = solver.end_datetime
        start_time = 0.0
        end_time = (end_dt - start_dt).total_seconds() / 86400.0
        try:
            routing_step = solver.routing_step.total_seconds()
        except AttributeError:
            # Defensive: should not happen given the legacy adapter shim,
            # but keep a sane fallback.
            routing_step = float(solver.routing_step)

        # Option lookups via the v1 mapping; the legacy adapter exposes
        # the same shape.  KeyError on unsupported keys.
        options = solver.options

        def _get_opt(key: str) -> str | None:
            try:
                return options[key]
            except (KeyError, Exception):
                return None

        flow_units_name = "UNKNOWN"
        raw_units = _get_opt("FLOW_UNITS")
        if raw_units is not None:
            try:
                flow_units_name = _FLOW_UNITS.get(int(raw_units), "UNKNOWN")
            except (ValueError, TypeError):
                flow_units_name = str(raw_units).upper() or "UNKNOWN"

        route_model_name = "UNKNOWN"
        raw_route = _get_opt("FLOW_ROUTING")
        if raw_route is not None:
            try:
                route_model_name = _ROUTE_MODELS.get(int(raw_route), "UNKNOWN")
            except (ValueError, TypeError):
                route_model_name = str(raw_route).upper() or "UNKNOWN"

        cur_time: float | None = None
        if session.state in ("running", "ended"):
            try:
                cur_time = (solver.current_datetime - start_dt).total_seconds() / 86400.0
            except Exception:
                pass

        surcharge_method = _get_opt("SURCHARGE_METHOD")
        dps_celerity = dps_alpha = dps_decay_time = None
        if surcharge_method and "DYNAMIC" in str(surcharge_method).upper():
            try:
                dps_celerity = float(_get_opt("DPS_CELERITY"))
                dps_alpha = float(_get_opt("DPS_ALPHA"))
                dps_decay_time = float(_get_opt("DPS_DECAY_TIME"))
            except (TypeError, ValueError):
                pass

        # event_count — v1 exposes solver.events as a MutableSequence.
        event_count: int | None = None
        events_view = getattr(solver, "events", None)
        if events_view is not None:
            try:
                event_count = len(events_view)
            except Exception:
                pass

        # steady_state_skip — v1 exposes it as a bool property.
        steady_state_skip: bool | None = None
        try:
            steady_state_skip = solver.steady_state_skip
        except AttributeError:
            pass

        return {
            "node_count": node_count,
            "link_count": link_count,
            "subcatch_count": subcatch_count,
            "gage_count": gage_count,
            "pollutant_count": pollutant_count,
            "start_time": start_time,
            "end_time": end_time,
            "routing_step": routing_step,
            "flow_units": flow_units_name,
            "route_model": route_model_name,
            "current_time": cur_time,
            "surcharge_method": surcharge_method,
            "dps_celerity": dps_celerity,
            "dps_alpha": dps_alpha,
            "dps_decay_time": dps_decay_time,
            "event_count": event_count,
            "steady_state_skip": steady_state_skip,
        }

    static = await asyncio.to_thread(_summary_static)

    node_count = static["node_count"]
    link_count = static["link_count"]
    subcatch_count = static["subcatch_count"]
    gage_count = static["gage_count"]
    pollutant_count = static["pollutant_count"]
    start_time = static["start_time"]
    end_time = static["end_time"]
    routing_step = static["routing_step"]
    flow_units = static["flow_units"]
    route_model = static["route_model"]
    current_time = static["current_time"]
    surcharge_method = static["surcharge_method"]
    dps_celerity = static["dps_celerity"]
    dps_alpha = static["dps_alpha"]
    dps_decay_time = static["dps_decay_time"]
    event_count = static["event_count"]
    steady_state_skip = static["steady_state_skip"]

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
        # Single to_thread per accessor: pull every id in one worker hop
        # via the v1 ``ids`` bulk getter, then filter in pure Python.
        def _ids(acc=accessor) -> list[str]:
            ids_attr = getattr(acc, "ids", None)
            if ids_attr is not None:
                return [str(x) for x in ids_attr]
            n = len(acc)
            return [acc.get_id(i) for i in range(n)]

        ids = await asyncio.to_thread(_ids)
        for i, eid in enumerate(ids):
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


def _build_pollutant_info_sync(pollutants, index: int) -> PollutantInfo:
    """Build PollutantInfo synchronously via v1 property access."""
    p = pollutants[index]
    poll_id = p.id
    units = int(p.units)
    kdecay = p.kdecay
    rain_conc = p.rain_conc
    gw_conc = p.gw_conc
    init_conc = p.init_conc
    rdii_conc = p.rdii_conc
    mwt = p.mwt
    snow_only = p.snow_only

    # v1 co_pollutant returns Optional[Tuple[Pollutant, float]].
    co_idx = 0
    co_frac = 0.0
    try:
        co = p.co_pollutant
        if co is not None:
            co_pollutant_wrap, co_frac = co
            co_idx = co_pollutant_wrap.index
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


async def _build_pollutant_info(pollutants, index: int) -> PollutantInfo:
    return await asyncio.to_thread(_build_pollutant_info_sync, pollutants, index)


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
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running", "ended", "opened", "building")

    # In 'building' state there is no backend yet — access via model_builder.
    if session.state == "building":
        if session.model_builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no ModelBuilder."
            )
        # v1: construct a Pollutants collection over the ModelBuilder for
        # property-style access pre-finalize.
        from openswmm.engine import Pollutants as _Pollutants
        pollutants = _Pollutants(session.model_builder)
    else:
        pollutants = session.pollutants

    if pollutant_id is not None:
        idx = await resolve_index(pollutants, pollutant_id, "Pollutant")
        if idx < 0:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] Pollutant '{pollutant_id}' not found."
            )
        return await _build_pollutant_info(pollutants, idx)

    count = await asyncio.to_thread(lambda: len(pollutants))
    return [await _build_pollutant_info(pollutants, i) for i in range(count)]
