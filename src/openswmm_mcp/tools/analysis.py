"""Post-simulation analysis tools for the OpenSWMM MCP server.

Provides tools for querying simulation statistics, mass balance, time series
output, flooding summaries, capacity summaries, scenario comparison, and
result export.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
from datetime import timedelta
from pathlib import Path

from fastmcp import Context, FastMCP

from openswmm_mcp._util.formatting import ndarray_to_list
from openswmm_mcp.dependencies import get_session_manager, require_new_engine, require_state
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
from openswmm_mcp.models import (
    CapacitySummaryItem,
    ExportResult,
    FloodingSummaryItem,
    MassBalanceResult,
    PumpEntryModel,
    ReportSnapshotModel,
    TimeSeries,
)

logger = logging.getLogger(__name__)

analysis_mcp = FastMCP("analysis")


# ---------------------------------------------------------------------------
# Variable-name-to-enum mapping helpers
# ---------------------------------------------------------------------------

# These are imported lazily inside the helpers below so that the module can
# still be loaded/inspected even when the engine package is not installed
# (e.g. during linting or documentation builds).


def _resolve_node_var(variable: str):
    """Map a human-friendly variable string to an ``OutNodeVar`` member."""
    from openswmm.engine import OutNodeVar

    _MAP = {
        "depth": OutNodeVar.DEPTH,
        "head": OutNodeVar.HEAD,
        "volume": OutNodeVar.VOLUME,
        "lateral_inflow": OutNodeVar.LATERAL_INFLOW,
        "total_inflow": OutNodeVar.TOTAL_INFLOW,
        "overflow": OutNodeVar.OVERFLOW,
    }
    key = variable.strip().lower()
    if key in _MAP:
        return _MAP[key]
    # Fall back to direct enum attribute lookup
    try:
        return OutNodeVar[variable.strip().upper()]
    except KeyError:
        members = ", ".join(m.name.lower() for m in OutNodeVar)
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown node variable '{variable}'. "
            f"Available variables: {members}."
        )


def _resolve_link_var(variable: str):
    """Map a human-friendly variable string to an ``OutLinkVar`` member."""
    from openswmm.engine import OutLinkVar

    _MAP = {
        "flow": OutLinkVar.FLOW,
        "depth": OutLinkVar.DEPTH,
        "velocity": OutLinkVar.VELOCITY,
        "volume": OutLinkVar.VOLUME,
        "capacity": OutLinkVar.CAPACITY,
    }
    key = variable.strip().lower()
    if key in _MAP:
        return _MAP[key]
    try:
        return OutLinkVar[variable.strip().upper()]
    except KeyError:
        members = ", ".join(m.name.lower() for m in OutLinkVar)
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown link variable '{variable}'. "
            f"Available variables: {members}."
        )


def _resolve_subcatch_var(variable: str):
    """Map a human-friendly variable string to an ``OutSubcatchVar`` member."""
    from openswmm.engine import OutSubcatchVar

    _MAP = {
        "rainfall": OutSubcatchVar.RAINFALL,
        "snow_depth": OutSubcatchVar.SNOW_DEPTH,
        "evap": OutSubcatchVar.EVAP,
        "infil": OutSubcatchVar.INFIL,
        "runoff": OutSubcatchVar.RUNOFF,
        "gw_flow": OutSubcatchVar.GW_FLOW,
        "gw_elev": OutSubcatchVar.GW_ELEV,
        "soil_moist": OutSubcatchVar.SOIL_MOIST,
    }
    key = variable.strip().lower()
    if key in _MAP:
        return _MAP[key]
    try:
        return OutSubcatchVar[variable.strip().upper()]
    except KeyError:
        members = ", ".join(m.name.lower() for m in OutSubcatchVar)
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown subcatchment variable "
            f"'{variable}'. Available variables: {members}."
        )


def _resolve_system_var(variable: str):
    """Map a human-friendly variable string to an ``OutSystemVar`` member."""
    from openswmm.engine import OutSystemVar

    _MAP = {
        "temperature": OutSystemVar.TEMPERATURE,
        "rainfall": OutSystemVar.RAINFALL,
        "snow_depth": OutSystemVar.SNOW_DEPTH,
        "evap": OutSystemVar.EVAP,
        "infil": OutSystemVar.INFIL,
        "runoff": OutSystemVar.RUNOFF,
        "dw_inflow": OutSystemVar.DW_INFLOW,
        "gw_inflow": OutSystemVar.GW_INFLOW,
        "lat_inflow": OutSystemVar.LAT_INFLOW,
        "flooding": OutSystemVar.FLOODING,
        "outflow": OutSystemVar.OUTFLOW,
        "storage": OutSystemVar.STORAGE,
        "evap_total": OutSystemVar.EVAP_TOTAL,
        "pet": OutSystemVar.PET,
    }
    key = variable.strip().lower()
    if key in _MAP:
        return _MAP[key]
    try:
        return OutSystemVar[variable.strip().upper()]
    except KeyError:
        members = ", ".join(m.name.lower() for m in OutSystemVar)
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown system variable '{variable}'. "
            f"Available variables: {members}."
        )


# ---------------------------------------------------------------------------
# OutputReader helpers
# ---------------------------------------------------------------------------


async def _ensure_output_reader(session):
    """Lazily create and cache an ``OutputReader`` on *session*.

    The OutputReader is stored on ``session.output_reader`` so it is created
    at most once per session and reused for subsequent time-series queries.
    """
    if session.output_reader is not None:
        return session.output_reader

    from openswmm.engine import OutputReader

    # session.out_path is the .out file written by the engine after the run.
    out_path = session.out_path
    reader = OutputReader(out_path)
    session.output_reader = reader
    return reader


def _build_timestamps(reader, start: int, end: int, step: int) -> list[float]:
    """Build a list of timestamps (as floats, Julian days) for [start, end] inclusive.

    v1 OutputReader exposes ``start_datetime`` (datetime) and
    ``report_step`` (timedelta).  We project to Julian-day floats to keep
    the historical JSON shape.
    """
    from openswmm.engine import datetime_to_oadate

    start_date_julian = datetime_to_oadate(reader.start_datetime)
    report_step_days = reader.report_step.total_seconds() / 86400.0
    timestamps = []
    for i in range(start, end + 1, step):  # end is inclusive, matching the reader
        timestamps.append(start_date_julian + i * report_step_days)
    return timestamps


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@analysis_mcp.tool()
async def get_statistics(
    ctx: Context,
    session_id: str = "default",
    element_type: str = "node",
    element_id: str = "",
) -> dict:
    """Retrieve post-simulation statistics for a single model element.

    Returns peak / max values and duration statistics collected by the engine
    during the simulation run.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    element_type:
        One of ``"node"``, ``"link"``, or ``"subcatchment"``.
    element_id:
        The identifier (name) of the element.
    """
    if not element_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] element_id is required.")

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running", "ended")
    require_new_engine(session, "Per-element simulation statistics")

    stats = session.statistics
    etype = element_type.strip().lower()

    if etype == "node":
        nodes = session.nodes
        idx = await resolve_index(nodes, element_id, "Node")
        if idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{element_id}' not found.")
        try:
            # v1 Statistics arrays are NumPy properties; index into them.
            def _read_node() -> tuple[float, float, float, float]:
                return (
                    float(stats.node_max_depth[idx]),
                    float(stats.node_max_overflow[idx]),
                    float(stats.node_vol_flooded[idx]),
                    float(stats.node_time_flooded[idx]),
                )
            max_depth, max_overflow, vol_flooded, time_flooded = await asyncio.to_thread(
                _read_node
            )
            return {
                "element_type": "node",
                "element_id": element_id,
                "max_depth": max_depth,
                "max_overflow": max_overflow,
                "vol_flooded": vol_flooded,
                "time_flooded": time_flooded,
            }
        except Exception as exc:
            raise ToolError(
                f"[{ErrorCode.ENGINE_ERROR}] Failed to get node statistics "
                f"for '{element_id}': {exc}"
            ) from exc

    elif etype == "link":
        links = session.links
        idx = await resolve_index(links, element_id, "Link")
        if idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{element_id}' not found.")
        try:
            def _read_link() -> tuple[int, float, float, float, float, float]:
                return (
                    int(links[idx].type),
                    float(stats.link_max_flow[idx]),
                    float(stats.link_max_velocity[idx]),
                    float(stats.link_max_filling[idx]),
                    float(stats.link_surcharge_time[idx]),
                    float(stats.link_vol_flow[idx]),
                )
            ltype, max_flow, max_vel, max_fill, surcharge, vol_flow = await asyncio.to_thread(
                _read_link
            )
            result = {
                "element_type": "link",
                "element_id": element_id,
                "max_flow": max_flow,
                "max_velocity": max_vel,
                "max_filling": max_fill,
                "surcharge_time": surcharge,
                "vol_flow": vol_flow,
            }
            # Pump-specific stats (type code 1 = PUMP)
            if ltype == 1:
                def _pump_stats() -> tuple[int, float, float]:
                    s = links[idx].stats
                    return int(s.pump_cycles), float(s.pump_on_time), float(s.pump_volume)
                pump_cycles, pump_on_time, pump_volume = await asyncio.to_thread(_pump_stats)
                result["pump_cycles"] = pump_cycles
                result["pump_on_time"] = pump_on_time
                result["pump_volume"] = pump_volume
            return result
        except Exception as exc:
            raise ToolError(
                f"[{ErrorCode.ENGINE_ERROR}] Failed to get link statistics "
                f"for '{element_id}': {exc}"
            ) from exc

    elif etype == "subcatchment":
        subcatchments = session.subcatchments
        idx = await resolve_index(subcatchments, element_id, "Subcatchment")
        if idx < 0:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{element_id}' not found."
            )
        try:
            # v1: stats.subcatchment_* (renamed from subcatch_*), numpy arrays.
            def _read_sub() -> tuple[float, float, float]:
                return (
                    # subcatch_precip wasn't a numpy bulk in v0 — v1 doesn't
                    # expose a precip array, so use the wrapper's stats view.
                    float(session.subcatchments[idx].stats.precip),
                    float(stats.subcatchment_runoff_vol[idx]),
                    float(stats.subcatchment_max_runoff[idx]),
                )
            precip, runoff_vol, max_runoff = await asyncio.to_thread(_read_sub)
            return {
                "element_type": "subcatchment",
                "element_id": element_id,
                "total_precip": precip,
                "total_runoff_vol": runoff_vol,
                "max_runoff": max_runoff,
                "runoff_coefficient": runoff_vol / precip if precip > 0 else 0.0,
            }
        except Exception as exc:
            raise ToolError(
                f"[{ErrorCode.ENGINE_ERROR}] Failed to get subcatchment "
                f"statistics for '{element_id}': {exc}"
            ) from exc

    else:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown element_type "
            f"'{element_type}'. Expected 'node', 'link', or 'subcatchment'."
        )


@analysis_mcp.tool()
async def get_mass_balance(
    ctx: Context,
    session_id: str = "default",
) -> MassBalanceResult:
    """Retrieve mass-balance continuity errors and volumetric totals.

    Returns the runoff, routing, and (if pollutants exist) quality continuity
    errors together with the individual volume components (rainfall, runoff,
    flooding, outflow, etc.).

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    """
    from openswmm.engine import RoutingTotal, RunoffTotal

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running", "ended")

    mb = session.mass_balance

    # v1 MassBalance: property-style accessors.  Legacy adapter exposes
    # both the v1 properties and the v0 get_* methods, so this works on
    # either backend.
    runoff_err = await asyncio.to_thread(lambda: mb.runoff_continuity_error)
    routing_err = await asyncio.to_thread(lambda: mb.routing_continuity_error)

    # Quality error -- per-pollutant when pollutants are modelled
    quality_err: float | None = None
    quality_errs: dict[str, float] | None = None
    pollutants = session.pollutants
    poll_count = await asyncio.to_thread(lambda: len(pollutants))
    if poll_count > 0:
        try:
            quality_err = await asyncio.to_thread(mb.quality_continuity_error, 0)
        except Exception:
            quality_err = None

        # Per-pollutant quality errors
        try:
            qe = {}
            for p in range(poll_count):
                pid = await asyncio.to_thread(pollutants.get_id, p)
                err = await asyncio.to_thread(mb.quality_continuity_error, p)
                qe[pid] = err
            if qe:
                quality_errs = qe
        except Exception:
            pass

    # Collect all runoff total components — v1 method ``runoff_total`` (was
    # ``get_runoff_total``); legacy backend's MassBalance shim lacks this
    # entirely, so guard with hasattr.
    runoff_total: dict[str, float] = {}
    runoff_total_fn = getattr(mb, "runoff_total", None)
    if callable(runoff_total_fn):
        for member in RunoffTotal:
            try:
                value = await asyncio.to_thread(runoff_total_fn, member)
                runoff_total[member.name.lower()] = value
            except Exception:
                pass

    # Collect all routing total components
    routing_total: dict[str, float] = {}
    routing_total_fn = getattr(mb, "routing_total", None)
    if callable(routing_total_fn):
        for member in RoutingTotal:
            try:
                value = await asyncio.to_thread(routing_total_fn, member)
                routing_total[member.name.lower()] = value
            except Exception:
                pass

    # Routing diagnostics — v1 exposes a RoutingDiagnostics object via the
    # ``routing_diagnostics`` property, plus a top-level ``max_courant``
    # property.  Project both down to the legacy dict shape for the
    # response.  Legacy backend doesn't expose either — guard with hasattr.
    routing_stats: dict[str, float] | None = None
    max_courant: float | None = None
    routing_stats_supported = hasattr(mb, "routing_diagnostics")
    if routing_stats_supported:
        try:
            def _read_diag() -> tuple[dict[str, float], float | None]:
                diag = mb.routing_diagnostics
                # RoutingDiagnostics is a NamedTuple / dataclass-like; expose
                # whatever fields it carries as a flat dict.
                if hasattr(diag, "_asdict"):
                    stats_dict = dict(diag._asdict())
                elif hasattr(diag, "__dict__"):
                    stats_dict = {k: v for k, v in vars(diag).items()
                                  if not k.startswith("_")}
                else:
                    stats_dict = {}
                # max_courant is also a top-level MassBalance property.
                mc = None
                try:
                    mc = float(mb.max_courant)
                except (AttributeError, Exception):
                    mc = stats_dict.get("max_courant")
                return stats_dict, mc

            routing_stats, max_courant = await asyncio.to_thread(_read_diag)
        except Exception:
            pass

    # Phase 4d: assemble the unsupported-fields list from the active
    # backend's capability surface.  Per the audit, the legacy backend's
    # ``get_quality_continuity_error`` silently ignores its
    # ``pollutant_index`` argument, so ``quality_continuity_errors``
    # (per-pollutant breakdown) is meaningless there.
    engine_kind = session.engine_kind
    unsupported: list[str] = []
    if not routing_stats_supported:
        unsupported.append("routing_diagnostics")
        unsupported.append("max_courant")
    if engine_kind == "legacy":
        # Per audit Appendix A: legacy backend collapses every pollutant
        # into the same scalar continuity error.  The per-pollutant dict
        # is therefore unreliable on legacy regardless of pollutant count.
        unsupported.append("quality_continuity_errors")

    return MassBalanceResult(
        session_id=session_id,
        runoff_continuity_error=runoff_err,
        routing_continuity_error=routing_err,
        quality_continuity_error=quality_err,
        quality_continuity_errors=quality_errs,
        runoff_total=runoff_total,
        routing_total=routing_total,
        routing_stats=routing_stats,
        max_courant=max_courant,
        engine_kind=engine_kind,
        unsupported_fields=unsupported or None,
    )


@analysis_mcp.tool()
async def get_quality_losses(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Return per-pollutant evaporation and seepage mass losses.

    These are the quality mass-balance loss terms the routing continuity
    accounts for but which :func:`get_mass_balance` does not surface (it
    reports continuity *errors* and volumetric totals). For every modelled
    pollutant, returns the cumulative mass lost to evaporation and to
    seepage (model mass units).

    Requires the ``openswmm`` backend and a session in ``running`` or
    ``ended`` state.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Quality mass-balance loss terms")
    require_state(session, "running", "ended")

    mb = session.mass_balance
    pollutants = session.pollutants
    poll_count = await asyncio.to_thread(lambda: len(pollutants))

    losses: dict[str, dict[str, float]] = {}
    for p in range(poll_count):
        pid = await asyncio.to_thread(pollutants.get_id, p)
        # v1 MassBalance.quality_evap_loss / quality_seep_loss take a
        # pollutant key (index or id) and return a cumulative mass (float).
        evap = await asyncio.to_thread(mb.quality_evap_loss, p)
        seep = await asyncio.to_thread(mb.quality_seep_loss, p)
        losses[pid] = {"evap_loss": float(evap), "seep_loss": float(seep)}

    return {
        "session_id": session_id,
        "pollutant_count": poll_count,
        "losses": losses,
    }


@analysis_mcp.tool()
async def get_time_series(
    ctx: Context,
    session_id: str = "default",
    element_type: str = "node",
    element_id: str = "",
    variable: str = "depth",
    start_period: int = 0,
    end_period: int = -1,
    downsample: int = 1,
) -> TimeSeries:
    """Retrieve a time series of output results for a model element.

    Reads from the binary ``.out`` file produced by the simulation.  The
    ``start_period`` and ``end_period`` parameters select a slice of the
    reporting periods (0-indexed).  Use ``downsample`` to skip periods for
    large result sets (e.g. ``downsample=10`` returns every 10th value).

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    element_type:
        One of ``"node"``, ``"link"``, ``"subcatchment"``, or ``"system"``.
    element_id:
        The identifier of the element.  Ignored when *element_type* is
        ``"system"``.
    variable:
        The output variable to retrieve (e.g. ``"depth"``, ``"flow"``).
    start_period:
        First reporting period index (inclusive, 0-based).  Defaults to 0.
    end_period:
        Last reporting period index (exclusive).  ``-1`` means all periods.
    downsample:
        Take every *N*-th value.  Defaults to 1 (no downsampling).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Time-series output reader")

    etype = element_type.strip().lower()

    if etype not in ("node", "link", "subcatchment", "system"):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown element_type "
            f"'{element_type}'. Expected 'node', 'link', 'subcatchment', "
            f"or 'system'."
        )

    if etype != "system" and not element_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] element_id is required for element_type '{etype}'."
        )

    if downsample < 1:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] downsample must be >= 1, got {downsample}."
        )

    reader = await _ensure_output_reader(session)

    period_count = await asyncio.to_thread(lambda: reader.period_count)
    # get_node/link/subcatch_series uses an inclusive [start, end] index range.
    last_period = period_count - 1
    if end_period < 0 or end_period > last_period:
        end_period = last_period
    if start_period < 0:
        start_period = 0
    if start_period > end_period:
        return TimeSeries(
            element_type=etype,
            element_id=element_id or "system",
            variable=variable,
            timestamps=[],
            values=[],
            units="",
        )

    # Resolve variable to enum and element ID to integer index, then fetch the series.
    # OutputReader series methods take integer element indices, not string IDs.
    if etype == "node":
        var_enum = _resolve_node_var(variable)
        node_idx = await resolve_index(session.nodes, element_id, "Node")
        if node_idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{element_id}' not found.")
        raw = await asyncio.to_thread(
            reader.node_series, node_idx, var_enum, start=start_period, end=end_period
        )
    elif etype == "link":
        var_enum = _resolve_link_var(variable)
        link_idx = await resolve_index(session.links, element_id, "Link")
        if link_idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{element_id}' not found.")
        raw = await asyncio.to_thread(
            reader.link_series, link_idx, var_enum, start=start_period, end=end_period
        )
    elif etype == "subcatchment":
        var_enum = _resolve_subcatch_var(variable)
        sc_idx = await resolve_index(session.subcatchments, element_id, "Subcatchment")
        if sc_idx < 0:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{element_id}' not found."
            )
        raw = await asyncio.to_thread(
            reader.subcatchment_series,
            sc_idx,
            var_enum,
            start=start_period,
            end=end_period,
        )
    else:  # system
        var_enum = _resolve_system_var(variable)
        raw = await asyncio.to_thread(reader.system_series, var_enum, start=start_period, end=end_period)

    values = ndarray_to_list(raw)

    # Build timestamps
    timestamps = await asyncio.to_thread(_build_timestamps, reader, start_period, end_period, 1)

    # Apply downsampling
    if downsample > 1:
        values = values[::downsample]
        timestamps = timestamps[::downsample]

    return TimeSeries(
        element_type=etype,
        element_id=element_id or "system",
        variable=variable,
        timestamps=timestamps,
        values=values,
        units="",
    )


@analysis_mcp.tool()
async def get_flooding_summary(
    ctx: Context,
    session_id: str = "default",
    min_flood_volume: float = 0.0,
) -> list[FloodingSummaryItem]:
    """Summarise flooding across all nodes in the model.

    Returns a list of nodes that experienced flooding (volume > *min_flood_volume*),
    sorted by total flood volume in descending order.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    min_flood_volume:
        Minimum flood volume threshold.  Nodes with a total flood volume at
        or below this value are excluded.  Defaults to 0.0.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")

    require_new_engine(session, "Per-element flooding statistics")
    stats = session.statistics

    def _fetch_all():
        # Phase 4c: pull all four statistics as NumPy arrays in four C
        # calls total, then filter + sort in Python. Old path looped
        # ``stats.node_vol_flooded(i) / node_max_overflow(i) /
        # node_time_flooded(i) / node_max_depth(i)`` per node — 4 * N
        # C ABI crossings for a 5 000-node network. The bulk path is
        # ~4 crossings regardless of N.
        meta = session.meta
        ids = meta.node_ids
        if not ids:
            return []

        # v1 Statistics exposes each column as a numpy property — no
        # ``_bulk`` suffix and no per-index method.
        vol = stats.node_vol_flooded
        max_over = stats.node_max_overflow
        t_flood = stats.node_time_flooded
        max_depth = stats.node_max_depth

        # Build FloodingSummaryItem records for nodes that exceed the
        # threshold. Numpy comparison + boolean indexing would be faster
        # but the per-item Pydantic model construction dominates anyway,
        # so a clean Python loop reads better here.
        result: list[FloodingSummaryItem] = []
        for i, node_id in enumerate(ids):
            v = float(vol[i])
            if v <= min_flood_volume:
                continue
            try:
                result.append(
                    FloodingSummaryItem(
                        node_id=node_id,
                        max_overflow_rate=float(max_over[i]),
                        total_flood_volume=v,
                        time_flooded=float(t_flood[i]),
                        max_depth=float(max_depth[i]),
                    )
                )
            except Exception:
                continue
        result.sort(key=lambda x: x.total_flood_volume, reverse=True)
        return result

    return await asyncio.to_thread(_fetch_all)


@analysis_mcp.tool()
async def get_capacity_summary(
    ctx: Context,
    session_id: str = "default",
    max_filling_threshold: float = 1.0,
) -> list[CapacitySummaryItem]:
    """Summarise hydraulic capacity usage across all links in the model.

    Returns a list of links whose maximum depth-to-full-depth ratio exceeds
    *max_filling_threshold*, sorted by filling ratio in descending order.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    max_filling_threshold:
        Links with ``max_depth / full_depth`` above this value are included.
        Defaults to 1.0 (i.e. links that exceeded full capacity).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")

    require_new_engine(session, "Per-link capacity statistics")
    stats = session.statistics

    def _fetch_all():
        # Phase 4c+4e: every column now has a bulk getter. Pull all 5
        # statistics as NumPy arrays in 5 C calls regardless of N, then
        # filter + sort in Python. The Phase 4e additions
        # (link_max_filling_bulk / link_max_velocity_bulk /
        # link_surcharge_time_bulk / link_vol_flow_bulk) close the loop
        # opened in Phase 4c.
        meta = session.meta
        ids = meta.link_ids
        if not ids:
            return []

        # v1 Statistics exposes each column as a numpy property.
        filling_arr = stats.link_max_filling
        max_flow_arr = stats.link_max_flow
        max_vel_arr = stats.link_max_velocity
        t_surch_arr = stats.link_surcharge_time
        vol_flow_arr = stats.link_vol_flow

        result: list[CapacitySummaryItem] = []
        for i, link_id in enumerate(ids):
            filling = float(filling_arr[i])
            if filling <= max_filling_threshold:
                continue
            try:
                result.append(
                    CapacitySummaryItem(
                        link_id=link_id,
                        max_filling=filling,
                        max_flow=float(max_flow_arr[i]),
                        max_velocity=float(max_vel_arr[i]),
                        time_above_threshold=float(t_surch_arr[i]),
                        vol_flow=float(vol_flow_arr[i]),
                    )
                )
            except Exception:
                continue
        result.sort(key=lambda x: x.max_filling, reverse=True)
        return result

    return await asyncio.to_thread(_fetch_all)


@analysis_mcp.tool()
async def get_report_snapshot(
    ctx: Context,
    session_id: str = "default",
) -> ReportSnapshotModel:
    """Return the full post-simulation report as structured data.

    Assembles the programmatic equivalent of the SWMM ``.rpt`` file into a
    single structured response covering:

    * **Routing diagnostics** — time-step statistics and convergence metrics
      (average/min/max step, total steps, number and percentage of
      non-converging steps, average iterations, maximum Courant number)
    * **Runoff continuity** — rainfall, evaporation, infiltration, runoff, and
      storage change volumes with continuity error
    * **Flow routing continuity** — inflow components, flooding, outflow, and
      loss volumes with continuity error
    * **Quality continuity** — per-pollutant mass balance with seep and evap
      losses (empty when no pollutants are modelled)
    * **Node flooding summary** — all nodes that experienced overflow, sorted
      by total flood volume
    * **Storage volume summary** — all STORAGE-type nodes with depth and
      volume statistics
    * **Link flow summary** — all links with peak flow, velocity, filling
      ratio, total volume, and surcharge time
    * **Pump summary** — PUMP links with startup count, total on-time,
      volume pumped, and percentage time on
    * **Subcatchment runoff summary** — precipitation, runoff volume, peak
      rate, and runoff coefficient per subcatchment

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    """
    from dataclasses import asdict

    from openswmm.engine import get_report_snapshot as _engine_snapshot

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running", "ended")
    require_new_engine(session, "Report snapshot")

    snapshot = await asyncio.to_thread(_engine_snapshot, session.solver)
    return ReportSnapshotModel.model_validate(asdict(snapshot))


@analysis_mcp.tool()
async def get_pump_summary(
    ctx: Context,
    session_id: str = "default",
) -> list[PumpEntryModel]:
    """Return post-simulation performance statistics for all pump links.

    Equivalent to the Pumping Summary section of the SWMM ``.rpt`` file.
    Only links of type PUMP are returned; if the model has no pumps the
    list will be empty.

    Each entry includes:

    * ``link_id`` — pump identifier
    * ``pump_curve_idx`` — index of the pump curve used (``-1`` = ideal pump)
    * ``num_startups`` — total on/off cycles during the simulation
    * ``total_on_time`` — cumulative run time (seconds)
    * ``total_volume`` — total volume pumped
    * ``pct_time_on`` — percentage of simulation duration the pump was active

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    """
    from openswmm.engine import get_report_snapshot as _engine_snapshot

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running", "ended")
    require_new_engine(session, "Pump summary")

    snapshot = await asyncio.to_thread(_engine_snapshot, session.solver)
    return [PumpEntryModel.model_validate(vars(p)) for p in snapshot.pump_summary]


@analysis_mcp.tool()
async def compare_scenarios(
    ctx: Context,
    session_a: str = "",
    session_b: str = "",
    element_type: str = "node",
    variable: str = "depth",
) -> dict:
    """Compare time-series output between two simulation sessions.

    Retrieves the specified variable for all elements of the given type from
    both sessions and returns summary statistics of the differences (mean,
    max, min of the element-wise peak differences).

    Parameters
    ----------
    session_a:
        Session identifier for the baseline scenario.
    session_b:
        Session identifier for the comparison scenario.
    element_type:
        One of ``"node"``, ``"link"``, or ``"subcatchment"``.
    variable:
        The output variable to compare (e.g. ``"depth"``, ``"flow"``).
    """
    if not session_a:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] session_a is required.")
    if not session_b:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] session_b is required.")

    sm = get_session_manager(ctx)

    sess_a = await sm.get_session(session_a)
    sess_b = await sm.get_session(session_b)
    require_state(sess_a, "ended")
    require_state(sess_b, "ended")
    require_new_engine(sess_a, "Scenario comparison via output reader")
    require_new_engine(sess_b, "Scenario comparison via output reader")

    reader_a = await _ensure_output_reader(sess_a)
    reader_b = await _ensure_output_reader(sess_b)

    etype = element_type.strip().lower()

    # Determine element count and series accessor
    if etype == "node":
        var_enum = _resolve_node_var(variable)
        count_a = reader_a.node_count
        count_b = reader_b.node_count
        count = min(count_a, count_b)

        async def _get_id(reader, idx):
            return await asyncio.to_thread(lambda: reader.node_ids[idx])

        async def _get_series(reader, idx):
            n = await asyncio.to_thread(lambda: reader.period_count)
            return await asyncio.to_thread(reader.node_series, idx, var_enum, start=0, end=n - 1)

    elif etype == "link":
        var_enum = _resolve_link_var(variable)
        count_a = reader_a.link_count
        count_b = reader_b.link_count
        count = min(count_a, count_b)

        async def _get_id(reader, idx):
            return await asyncio.to_thread(lambda: reader.link_ids[idx])

        async def _get_series(reader, idx):
            n = await asyncio.to_thread(lambda: reader.period_count)
            return await asyncio.to_thread(reader.link_series, idx, var_enum, start=0, end=n - 1)

    elif etype == "subcatchment":
        var_enum = _resolve_subcatch_var(variable)
        count_a = reader_a.subcatchment_count
        count_b = reader_b.subcatchment_count
        count = min(count_a, count_b)

        async def _get_id(reader, idx):
            return await asyncio.to_thread(lambda: reader.subcatchment_ids[idx])

        async def _get_series(reader, idx):
            n = await asyncio.to_thread(lambda: reader.period_count)
            return await asyncio.to_thread(reader.subcatchment_series, idx, var_enum, start=0, end=n - 1)

    else:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown element_type "
            f"'{element_type}'. Expected 'node', 'link', or 'subcatchment'."
        )

    # Compute per-element peak differences
    peak_diffs: list[dict] = []
    abs_diffs: list[float] = []

    for i in range(count):
        eid_a = await _get_id(reader_a, i)
        eid_b = await _get_id(reader_b, i)

        # Only compare elements that share the same ID
        if eid_a != eid_b:
            continue

        try:
            series_a = ndarray_to_list(await _get_series(reader_a, i))
            series_b = ndarray_to_list(await _get_series(reader_b, i))
        except Exception:
            continue

        # Use the shorter length if they differ
        length = min(len(series_a), len(series_b))
        if length == 0:
            continue

        peak_a = max(series_a[:length])
        peak_b = max(series_b[:length])
        diff = peak_b - peak_a

        peak_diffs.append(
            {
                "element_id": eid_a,
                "peak_a": peak_a,
                "peak_b": peak_b,
                "difference": diff,
            }
        )
        abs_diffs.append(abs(diff))

    if not abs_diffs:
        return {
            "session_a": session_a,
            "session_b": session_b,
            "element_type": etype,
            "variable": variable,
            "elements_compared": 0,
            "summary": {},
            "details": [],
        }

    return {
        "session_a": session_a,
        "session_b": session_b,
        "element_type": etype,
        "variable": variable,
        "elements_compared": len(peak_diffs),
        "summary": {
            "mean_abs_diff": sum(abs_diffs) / len(abs_diffs),
            "max_abs_diff": max(abs_diffs),
            "min_abs_diff": min(abs_diffs),
            "mean_diff": sum(d["difference"] for d in peak_diffs) / len(peak_diffs),
            "max_diff": max(d["difference"] for d in peak_diffs),
            "min_diff": min(d["difference"] for d in peak_diffs),
        },
        "details": sorted(
            peak_diffs,
            key=lambda d: abs(d["difference"]),
            reverse=True,
        ),
    }


@analysis_mcp.tool()
async def export_results(
    ctx: Context,
    session_id: str = "default",
    output_path: str = "",
    format: str = "csv",
) -> ExportResult:
    """Export node and link time-series results to CSV or JSON.

    Writes one file containing all node and link output variables for every
    reporting period.  Useful for downstream analysis in spreadsheets or
    data-science tools.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    output_path:
        Destination file path.  Relative paths are resolved against the
        session's working directory.
    format:
        Output format: ``"csv"`` or ``"json"``.  Defaults to ``"csv"``.
    """
    if not output_path:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] output_path is required.")

    fmt = format.strip().lower()
    if fmt not in ("csv", "json"):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unsupported format '{format}'. "
            f"Expected 'csv' or 'json'."
        )

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Result export via output reader")

    reader = await _ensure_output_reader(session)

    # Resolve output path
    out = Path(output_path).expanduser()
    if not out.is_absolute():
        out = (session.working_dir / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    from openswmm.engine import OutLinkVar, OutNodeVar

    node_count = await asyncio.to_thread(lambda: reader.node_count)
    link_count = await asyncio.to_thread(lambda: reader.link_count)
    period_count = await asyncio.to_thread(lambda: reader.period_count)
    start_time = await asyncio.to_thread(lambda: reader.start_datetime)
    report_step = await asyncio.to_thread(lambda: reader.report_step.total_seconds())

    # Collect all node and link IDs (v1: bulk list properties).
    node_ids = list(await asyncio.to_thread(lambda: reader.node_ids))[:node_count]
    link_ids = list(await asyncio.to_thread(lambda: reader.link_ids))[:link_count]

    # Node variables to export (exclude POLLUT_BASE)
    node_vars = [m for m in OutNodeVar if m != OutNodeVar.POLLUT_BASE]
    link_vars = [m for m in OutLinkVar if m != OutLinkVar.POLLUT_BASE]

    # Pre-fetch all series into memory. Reader series methods take integer
    # element indices, not string IDs, so we iterate by index.
    # get_*_series uses an inclusive [start, end] range; last valid index = period_count - 1.
    last_period = period_count - 1
    node_series: dict[str, dict[str, list[float]]] = {}
    for i, nid in enumerate(node_ids):
        node_series[nid] = {}
        for var in node_vars:
            raw = await asyncio.to_thread(reader.node_series, i, var, start=0, end=last_period)
            node_series[nid][var.name.lower()] = ndarray_to_list(raw)

    link_series: dict[str, dict[str, list[float]]] = {}
    for i, lid in enumerate(link_ids):
        link_series[lid] = {}
        for var in link_vars:
            raw = await asyncio.to_thread(reader.link_series, i, var, start=0, end=last_period)
            link_series[lid][var.name.lower()] = ndarray_to_list(raw)

    record_count = 0

    if fmt == "csv":
        record_count = await asyncio.to_thread(
            _write_csv,
            out,
            period_count,
            start_time,
            report_step,
            node_ids,
            node_vars,
            node_series,
            link_ids,
            link_vars,
            link_series,
        )
    else:
        record_count = await asyncio.to_thread(
            _write_json,
            out,
            period_count,
            start_time,
            report_step,
            node_ids,
            node_vars,
            node_series,
            link_ids,
            link_vars,
            link_series,
        )

    return ExportResult(
        status="exported",
        path=str(out),
        format=fmt,
        record_count=record_count,
    )


# ---------------------------------------------------------------------------
# Export writers (run inside asyncio.to_thread)
# ---------------------------------------------------------------------------


def _write_csv(
    path: Path,
    period_count: int,
    start_time: float,
    report_step: float,
    node_ids: list[str],
    node_vars: list,
    node_series: dict,
    link_ids: list[str],
    link_vars: list,
    link_series: dict,
) -> int:
    """Write node and link series data to a CSV file."""
    record_count = 0

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)

        # Header
        header = ["period", "timestamp", "element_type", "element_id"]
        for var in node_vars:
            header.append(f"node_{var.name.lower()}")
        for var in link_vars:
            header.append(f"link_{var.name.lower()}")
        writer.writerow(header)

        # Node rows
        for nid in node_ids:
            for p in range(period_count):
                row = [
                    p,
                    (start_time + timedelta(seconds=p * report_step)).isoformat(),
                    "node",
                    nid,
                ]
                for var in node_vars:
                    vals = node_series[nid][var.name.lower()]
                    row.append(vals[p] if p < len(vals) else "")
                # Empty link columns
                row.extend([""] * len(link_vars))
                writer.writerow(row)
                record_count += 1

        # Link rows
        for lid in link_ids:
            for p in range(period_count):
                row = [
                    p,
                    (start_time + timedelta(seconds=p * report_step)).isoformat(),
                    "link",
                    lid,
                ]
                # Empty node columns
                row.extend([""] * len(node_vars))
                for var in link_vars:
                    vals = link_series[lid][var.name.lower()]
                    row.append(vals[p] if p < len(vals) else "")
                writer.writerow(row)
                record_count += 1

    return record_count


def _write_json(
    path: Path,
    period_count: int,
    start_time: float,
    report_step: float,
    node_ids: list[str],
    node_vars: list,
    node_series: dict,
    link_ids: list[str],
    link_vars: list,
    link_series: dict,
) -> int:
    """Write node and link series data to a JSON file."""
    records: list[dict] = []

    # Node records
    for nid in node_ids:
        entry = {
            "element_type": "node",
            "element_id": nid,
            "series": {},
        }
        for var in node_vars:
            entry["series"][var.name.lower()] = node_series[nid][var.name.lower()]
        records.append(entry)

    # Link records
    for lid in link_ids:
        entry = {
            "element_type": "link",
            "element_id": lid,
            "series": {},
        }
        for var in link_vars:
            entry["series"][var.name.lower()] = link_series[lid][var.name.lower()]
        records.append(entry)

    output = {
        "period_count": period_count,
        "start_time": start_time.isoformat() if hasattr(start_time, "isoformat") else start_time,
        "report_step": report_step,
        "elements": records,
    }

    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    return len(records)


# ===========================================================================
# Output reader breadth (Phase 2.5)
#
# Wraps the remaining OutputReader entry points that get_time_series doesn't
# already cover: per-period metadata, snapshot ("attribute") readers for a
# single object across all of its variables, system-level time series, and
# the pollutant count from the .out file header.
#
# State contract: all of these read the binary .out file written when the
# simulation reaches ENDED. require_state(session, "ended") enforces it;
# callers must run the simulation to completion (e.g. via
# lifecycle.run_simulation) before invoking these tools.
# ===========================================================================


def _node_attribute_keys(n_pollutants: int) -> list[str]:
    """Variable order in the row returned by reader.node_attributes.

    Index 0..5 are the engine base variables; indices 6..6+n_pollutants
    are pollutant concentrations (one column per defined pollutant).
    """
    base = ["depth", "head", "volume", "lateral_inflow", "total_inflow", "overflow"]
    return base + [f"pollutant_{i}" for i in range(n_pollutants)]


def _link_attribute_keys(n_pollutants: int) -> list[str]:
    base = ["flow", "depth", "velocity", "volume", "capacity"]
    return base + [f"pollutant_{i}" for i in range(n_pollutants)]


def _subcatch_attribute_keys(n_pollutants: int) -> list[str]:
    base = [
        "rainfall",
        "snow_depth",
        "evap",
        "infil",
        "runoff",
        "gw_flow",
        "gw_elev",
        "soil_moist",
    ]
    return base + [f"pollutant_{i}" for i in range(n_pollutants)]


@analysis_mcp.tool()
async def output_metadata(ctx: Context, session_id: str = "default") -> dict:
    """Return header metadata for the .out file (counts + timing + version).

    Combines several small reader getters into one call so an LLM can size
    a subsequent batch read in a single round-trip.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader metadata")

    reader = await _ensure_output_reader(session)
    (
        version,
        units,
        n_sub,
        n_node,
        n_link,
        n_poll,
        n_periods,
        start,
        step,
        err,
    ) = await asyncio.to_thread(
        lambda: (
            reader.version,
            int(reader.flow_units),
            reader.subcatchment_count,
            reader.node_count,
            reader.link_count,
            reader.pollutant_count,
            reader.period_count,
            reader.start_datetime.isoformat(),
            reader.report_step.total_seconds(),
            reader.error_code,
        )
    )
    return {
        "session_id": session_id,
        "version": version,
        "flow_units_code": units,
        "subcatchment_count": n_sub,
        "node_count": n_node,
        "link_count": n_link,
        "pollutant_count": n_poll,
        "period_count": n_periods,
        "start_date": start,
        "report_step_seconds": step,
        "error_code": err,
    }


@analysis_mcp.tool()
async def output_period_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of reporting periods written to the .out file."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader period count")

    reader = await _ensure_output_reader(session)
    n = await asyncio.to_thread(lambda: reader.period_count)
    return {"session_id": session_id, "period_count": n}


@analysis_mcp.tool()
async def output_pollutant_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of pollutants tracked in the .out file."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader pollutant count")

    reader = await _ensure_output_reader(session)
    n = await asyncio.to_thread(lambda: reader.pollutant_count)
    return {"session_id": session_id, "pollutant_count": n}


@analysis_mcp.tool()
async def output_period_time(ctx: Context, session_id: str = "default", period: int = 0) -> dict:
    """Return the elapsed time (project time units) for a reporting period.

    The value combines with ``start_date`` (from :func:`output_metadata`)
    to produce an absolute timestamp.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader period time")

    reader = await _ensure_output_reader(session)
    n_periods = await asyncio.to_thread(lambda: reader.period_count)
    if not 0 <= period < n_periods:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] period must be in [0, {n_periods}); got {period}."
        )
    elapsed = await asyncio.to_thread(lambda: float(reader.period_times[period].astype("float64") / 1e9))
    return {
        "session_id": session_id,
        "period": period,
        "elapsed_time": elapsed,
    }


@analysis_mcp.tool()
async def output_node_attribute(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
    period: int = 0,
) -> dict:
    """Return all variable values for a node at a single reporting period.

    Variables are returned as a dict keyed by name: ``depth``, ``head``,
    ``volume``, ``lateral_inflow``, ``total_inflow``, ``overflow``, plus
    ``pollutant_0`` .. ``pollutant_{n-1}`` when pollutants are tracked.

    Wraps ``swmm_output_get_node_attribute`` — the per-object snapshot
    accessor distinct from ``get_time_series`` (one variable over time)
    and ``get_node_result`` (one variable over all nodes at one period).
    """
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader node attribute")

    reader = await _ensure_output_reader(session)
    n_periods = await asyncio.to_thread(lambda: reader.period_count)
    if not 0 <= period < n_periods:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] period must be in [0, {n_periods}); got {period}."
        )

    node_idx = await resolve_index(session.nodes, node_id, "Node")
    if node_idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found.")

    n_poll = await asyncio.to_thread(lambda: reader.pollutant_count)
    attrs = await asyncio.to_thread(reader.node_attributes, node_idx, period)
    keys = _node_attribute_keys(n_poll)
    values = [float(v) for v in attrs.values()]
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": node_idx,
        "period": period,
        "attributes": dict(zip(keys, values[: len(keys)], strict=False)),
    }


@analysis_mcp.tool()
async def output_node_stats(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
) -> dict:
    """Return post-run node statistics aggregated from the .out file.

    Wraps the four engine-level accessors
    ``swmm_output_get_node_stat_max_depth`` /
    ``_max_overflow`` /
    ``_vol_flooded`` /
    ``_time_flooded``.

    These differ from :func:`get_flooding_summary` in two ways:

      * the aggregation is computed from the **binary output file**
        (so it works even after the engine handle is closed); and
      * the response covers a **single named node**, not a filtered
        list across the whole network.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Must be in ``ended`` state.
    node_id:
        String node identifier (e.g. ``"J1"``).  Required.

    Returns
    -------
    dict
        ``{"session_id": ..., "node_id": ..., "node_index": ...,
        "max_depth": float, "max_overflow": float,
        "total_flood_volume": float, "time_flooded_seconds": float,
        "time_flooded_hours": float}``.  ``time_flooded_hours`` is
        provided as a convenience because that is the convention SWMM's
        ``statsrpt`` uses for display.

    Raises
    ------
    ToolError
        Session not in ``ended`` state, backend is the legacy engine
        (this feature requires the new engine), ``node_id`` is empty,
        or the node does not exist.
    """
    if not node_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader node statistics")

    node_idx = await resolve_index(session.nodes, node_id, "Node")
    if node_idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found.")

    reader = await _ensure_output_reader(session)

    # Fetch the four aggregates in one ``to_thread`` so we pay a single
    # context switch instead of four.  Each underlying C call releases
    # the GIL while it scans the .out file.
    def _fetch() -> dict:
        return {
            # v1: reader.node_stats(node) returns an object with the four
            # per-node summary fields.
            "max_depth": float(reader.node_stats(node_idx).max_depth),
            "max_overflow": float(reader.node_stats(node_idx).max_overflow),
            "total_flood_volume": float(reader.node_stats(node_idx).vol_flooded),
            "time_flooded_seconds": float(reader.node_stats(node_idx).time_flooded),
        }
    stats = await asyncio.to_thread(_fetch)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": node_idx,
        **stats,
        "time_flooded_hours": stats["time_flooded_seconds"] / 3600.0,
    }


@analysis_mcp.tool()
async def output_link_attribute(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    period: int = 0,
) -> dict:
    """Return all variable values for a link at a single reporting period.

    Variables are returned as a dict keyed by name: ``flow``, ``depth``,
    ``velocity``, ``volume``, ``capacity``, plus ``pollutant_i`` columns
    when pollutants are tracked.
    """
    if not link_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] link_id must not be empty.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader link attribute")

    reader = await _ensure_output_reader(session)
    n_periods = await asyncio.to_thread(lambda: reader.period_count)
    if not 0 <= period < n_periods:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] period must be in [0, {n_periods}); got {period}."
        )

    link_idx = await resolve_index(session.links, link_id, "Link")
    if link_idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found.")

    n_poll = await asyncio.to_thread(lambda: reader.pollutant_count)
    attrs = await asyncio.to_thread(reader.link_attributes, link_idx, period)
    keys = _link_attribute_keys(n_poll)
    values = [float(v) for v in attrs.values()]
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": link_idx,
        "period": period,
        "attributes": dict(zip(keys, values[: len(keys)], strict=False)),
    }


@analysis_mcp.tool()
async def output_subcatch_attribute(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str = "",
    period: int = 0,
) -> dict:
    """Return all variable values for a subcatchment at a reporting period.

    Variables: ``rainfall``, ``snow_depth``, ``evap``, ``infil``, ``runoff``,
    ``gw_flow``, ``gw_elev``, ``soil_moist``, plus ``pollutant_i`` columns
    when pollutants are tracked.
    """
    if not subcatch_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] subcatch_id must not be empty.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader subcatchment attribute")

    reader = await _ensure_output_reader(session)
    n_periods = await asyncio.to_thread(lambda: reader.period_count)
    if not 0 <= period < n_periods:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] period must be in [0, {n_periods}); got {period}."
        )

    sc_idx = await resolve_index(session.subcatchments, subcatch_id, "Subcatchment")
    if sc_idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{subcatch_id}' not found.")

    n_poll = await asyncio.to_thread(lambda: reader.pollutant_count)
    attrs = await asyncio.to_thread(reader.subcatchment_attributes, sc_idx, period)
    keys = _subcatch_attribute_keys(n_poll)
    values = [float(v) for v in attrs.values()]
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": sc_idx,
        "period": period,
        "attributes": dict(zip(keys, values[: len(keys)], strict=False)),
    }


@analysis_mcp.tool()
async def output_system_result(
    ctx: Context,
    session_id: str = "default",
    variable: str = "rainfall",
    period: int = 0,
) -> dict:
    """Return a single system-level variable at a single reporting period.

    Cheaper than ``get_time_series`` when only one timestep is needed.
    ``variable`` is one of: ``temperature``, ``rainfall``, ``snow_depth``,
    ``evap``, ``infil``, ``runoff``, ``dw_inflow``, ``gw_inflow``,
    ``lat_inflow``, ``flooding``, ``outflow``, ``storage``, ``evap_total``,
    ``pet``.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader system result")

    reader = await _ensure_output_reader(session)
    n_periods = await asyncio.to_thread(lambda: reader.period_count)
    if not 0 <= period < n_periods:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] period must be in [0, {n_periods}); got {period}."
        )

    var_enum = _resolve_system_var(variable)
    value = await asyncio.to_thread(reader.system_result, period, var_enum)
    return {
        "session_id": session_id,
        "variable": variable,
        "period": period,
        "value": value,
    }


# ===========================================================================
# Output reader: per-period array readers (Phase 2.5 follow-up)
#
# Distinct semantic from the *_attribute tools above:
#   - *_attribute(elem_id, period)  -> all variables for ONE object at one period
#   - *_results(variable, period)   -> ONE variable across ALL objects at one period
#
# Useful for heatmap-style snapshots: "show me flow across the whole link
# network at period 42". Returns a list of {id, value} pairs so the caller
# can correlate with element identity without a separate lookup.
# ===========================================================================


@analysis_mcp.tool()
async def output_node_results(
    ctx: Context,
    session_id: str = "default",
    variable: str = "depth",
    period: int = 0,
) -> dict:
    """Return one node variable across all nodes at a single reporting period.

    ``variable`` is one of: ``depth``, ``head``, ``volume``,
    ``lateral_inflow``, ``total_inflow``, ``overflow``.

    Returns a list of ``{id, index, value}`` records ordered by node index
    (which matches the .out file's stored order).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader node results")

    reader = await _ensure_output_reader(session)
    n_periods = await asyncio.to_thread(lambda: reader.period_count)
    if not 0 <= period < n_periods:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] period must be in [0, {n_periods}); got {period}."
        )

    var_enum = _resolve_node_var(variable)
    n_nodes = await asyncio.to_thread(lambda: reader.node_count)
    arr = await asyncio.to_thread(reader.node_result, period, var_enum)
    values = ndarray_to_list(arr)

    def _ids() -> list[str]:
        return [reader.node_ids[i] for i in range(n_nodes)]

    ids = await asyncio.to_thread(_ids)
    records = [
        {"id": ids[i], "index": i, "value": values[i]} for i in range(min(n_nodes, len(values)))
    ]
    return {
        "session_id": session_id,
        "variable": variable,
        "period": period,
        "count": len(records),
        "results": records,
    }


@analysis_mcp.tool()
async def output_link_results(
    ctx: Context,
    session_id: str = "default",
    variable: str = "flow",
    period: int = 0,
) -> dict:
    """Return one link variable across all links at a single reporting period.

    ``variable`` is one of: ``flow``, ``depth``, ``velocity``, ``volume``,
    ``capacity``.

    Returns a list of ``{id, index, value}`` records.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader link results")

    reader = await _ensure_output_reader(session)
    n_periods = await asyncio.to_thread(lambda: reader.period_count)
    if not 0 <= period < n_periods:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] period must be in [0, {n_periods}); got {period}."
        )

    var_enum = _resolve_link_var(variable)
    n_links = await asyncio.to_thread(lambda: reader.link_count)
    arr = await asyncio.to_thread(reader.link_result, period, var_enum)
    values = ndarray_to_list(arr)

    def _ids() -> list[str]:
        return [reader.link_ids[i] for i in range(n_links)]

    ids = await asyncio.to_thread(_ids)
    records = [
        {"id": ids[i], "index": i, "value": values[i]} for i in range(min(n_links, len(values)))
    ]
    return {
        "session_id": session_id,
        "variable": variable,
        "period": period,
        "count": len(records),
        "results": records,
    }


@analysis_mcp.tool()
async def output_subcatch_results(
    ctx: Context,
    session_id: str = "default",
    variable: str = "runoff",
    period: int = 0,
) -> dict:
    """Return one subcatchment variable across all subcatchments at a period.

    ``variable`` is one of: ``rainfall``, ``snow_depth``, ``evap``, ``infil``,
    ``runoff``, ``gw_flow``, ``gw_elev``, ``soil_moist``.

    Returns a list of ``{id, index, value}`` records.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "ended")
    require_new_engine(session, "Output reader subcatchment results")

    reader = await _ensure_output_reader(session)
    n_periods = await asyncio.to_thread(lambda: reader.period_count)
    if not 0 <= period < n_periods:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] period must be in [0, {n_periods}); got {period}."
        )

    var_enum = _resolve_subcatch_var(variable)
    n_sub = await asyncio.to_thread(lambda: reader.subcatchment_count)
    arr = await asyncio.to_thread(reader.subcatchment_result, period, var_enum)
    values = ndarray_to_list(arr)

    def _ids() -> list[str]:
        return [reader.subcatchment_ids[i] for i in range(n_sub)]

    ids = await asyncio.to_thread(_ids)
    records = [
        {"id": ids[i], "index": i, "value": values[i]} for i in range(min(n_sub, len(values)))
    ]
    return {
        "session_id": session_id,
        "variable": variable,
        "period": period,
        "count": len(records),
        "results": records,
    }
