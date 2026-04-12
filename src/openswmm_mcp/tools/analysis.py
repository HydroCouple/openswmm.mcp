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
from pathlib import Path

from fastmcp import Context, FastMCP

from openswmm_mcp._util.formatting import ndarray_to_list
from openswmm_mcp.dependencies import get_session_manager, require_state
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import (
    CapacitySummaryItem,
    ExportResult,
    FloodingSummaryItem,
    MassBalanceResult,
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

    solver = session.solver
    out_path = await asyncio.to_thread(lambda: solver.out_path)

    reader = OutputReader()
    await asyncio.to_thread(reader.open, out_path)
    session.output_reader = reader
    return reader


def _build_timestamps(reader, start: int, end: int, step: int) -> list[float]:
    """Build a list of timestamps (as floats) for the selected period range.

    Each timestamp is ``start_time + period_index * report_step``.
    """
    start_time = reader.get_start_time()
    report_step = reader.get_report_step()
    timestamps = []
    for i in range(start, end, step):
        timestamps.append(start_time + i * report_step)
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

    stats = session.statistics
    etype = element_type.strip().lower()

    if etype == "node":
        try:
            return {
                "element_type": "node",
                "element_id": element_id,
                "max_depth": await asyncio.to_thread(stats.node_max_depth, element_id),
                "max_head": await asyncio.to_thread(stats.node_max_head, element_id),
                "max_lat_inflow": await asyncio.to_thread(stats.node_max_lat_inflow, element_id),
                "max_overflow": await asyncio.to_thread(stats.node_max_overflow, element_id),
                "vol_flooded": await asyncio.to_thread(stats.node_vol_flooded, element_id),
                "time_flooded": await asyncio.to_thread(stats.node_time_flooded, element_id),
            }
        except Exception as exc:
            raise ToolError(
                f"[{ErrorCode.ENGINE_ERROR}] Failed to get node statistics "
                f"for '{element_id}': {exc}"
            ) from exc

    elif etype == "link":
        try:
            return {
                "element_type": "link",
                "element_id": element_id,
                "max_flow": await asyncio.to_thread(stats.link_max_flow, element_id),
                "max_velocity": await asyncio.to_thread(stats.link_max_velocity, element_id),
                "max_depth": await asyncio.to_thread(stats.link_max_depth, element_id),
                "time_above_normal": await asyncio.to_thread(
                    stats.link_time_above_normal, element_id
                ),
            }
        except Exception as exc:
            raise ToolError(
                f"[{ErrorCode.ENGINE_ERROR}] Failed to get link statistics "
                f"for '{element_id}': {exc}"
            ) from exc

    elif etype == "subcatchment":
        try:
            return {
                "element_type": "subcatchment",
                "element_id": element_id,
                "max_runoff": await asyncio.to_thread(stats.subcatch_max_runoff, element_id),
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

    runoff_err = await asyncio.to_thread(mb.get_runoff_continuity_error)
    routing_err = await asyncio.to_thread(mb.get_routing_continuity_error)

    # Quality error -- only meaningful when pollutants are modelled
    quality_err: float | None = None
    pollutants = session.pollutants
    poll_count = await asyncio.to_thread(pollutants.count)
    if poll_count > 0:
        try:
            quality_err = await asyncio.to_thread(mb.get_quality_continuity_error, 0)
        except Exception:
            quality_err = None

    # Collect all runoff total components
    runoff_total: dict[str, float] = {}
    for member in RunoffTotal:
        try:
            value = await asyncio.to_thread(mb.get_runoff_total, member)
            runoff_total[member.name.lower()] = value
        except Exception:
            pass

    # Collect all routing total components
    routing_total: dict[str, float] = {}
    for member in RoutingTotal:
        try:
            value = await asyncio.to_thread(mb.get_routing_total, member)
            routing_total[member.name.lower()] = value
        except Exception:
            pass

    return MassBalanceResult(
        session_id=session_id,
        runoff_continuity_error=runoff_err,
        routing_continuity_error=routing_err,
        quality_continuity_error=quality_err,
        runoff_total=runoff_total,
        routing_total=routing_total,
    )


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

    period_count = await asyncio.to_thread(reader.get_period_count)
    if end_period < 0 or end_period > period_count:
        end_period = period_count
    if start_period < 0:
        start_period = 0
    if start_period >= end_period:
        return TimeSeries(
            element_type=etype,
            element_id=element_id or "system",
            variable=variable,
            timestamps=[],
            values=[],
            units="",
        )

    # Resolve variable to enum and fetch the series
    if etype == "node":
        var_enum = _resolve_node_var(variable)
        raw = await asyncio.to_thread(
            reader.get_node_series, element_id, var_enum, start_period, end_period
        )
    elif etype == "link":
        var_enum = _resolve_link_var(variable)
        raw = await asyncio.to_thread(
            reader.get_link_series, element_id, var_enum, start_period, end_period
        )
    elif etype == "subcatchment":
        var_enum = _resolve_subcatch_var(variable)
        raw = await asyncio.to_thread(
            reader.get_subcatch_series,
            element_id,
            var_enum,
            start_period,
            end_period,
        )
    else:  # system
        var_enum = _resolve_system_var(variable)
        raw = await asyncio.to_thread(reader.get_system_series, var_enum, start_period, end_period)

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

    stats = session.statistics
    nodes = session.nodes
    node_count = await asyncio.to_thread(nodes.count)

    results: list[FloodingSummaryItem] = []

    for i in range(node_count):
        node_id = await asyncio.to_thread(nodes.get_id, i)

        try:
            vol_flooded = await asyncio.to_thread(stats.node_vol_flooded, node_id)
        except Exception:
            continue

        if vol_flooded <= min_flood_volume:
            continue

        try:
            max_overflow = await asyncio.to_thread(stats.node_max_overflow, node_id)
            time_flooded = await asyncio.to_thread(stats.node_time_flooded, node_id)
            max_depth = await asyncio.to_thread(stats.node_max_depth, node_id)
        except Exception:
            continue

        results.append(
            FloodingSummaryItem(
                node_id=node_id,
                max_overflow_rate=max_overflow,
                total_flood_volume=vol_flooded,
                time_flooded=time_flooded,
                max_depth=max_depth,
            )
        )

    # Sort by total flood volume, largest first
    results.sort(key=lambda item: item.total_flood_volume, reverse=True)
    return results


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

    stats = session.statistics
    links = session.links
    link_count = await asyncio.to_thread(links.count)

    results: list[CapacitySummaryItem] = []

    for i in range(link_count):
        link_id = await asyncio.to_thread(links.get_id, i)

        try:
            max_depth = await asyncio.to_thread(stats.link_max_depth, link_id)
            full_depth = await asyncio.to_thread(links.get_max_depth, i)
        except Exception:
            continue

        # Avoid division by zero
        if full_depth is None or full_depth <= 0.0:
            continue

        filling = max_depth / full_depth

        if filling <= max_filling_threshold:
            continue

        try:
            max_flow = await asyncio.to_thread(stats.link_max_flow, link_id)
            max_velocity = await asyncio.to_thread(stats.link_max_velocity, link_id)
            time_above = await asyncio.to_thread(stats.link_time_above_normal, link_id)
        except Exception:
            continue

        results.append(
            CapacitySummaryItem(
                link_id=link_id,
                max_filling=filling,
                max_flow=max_flow,
                max_velocity=max_velocity,
                time_above_threshold=time_above,
            )
        )

    # Sort by filling ratio, largest first
    results.sort(key=lambda item: item.max_filling, reverse=True)
    return results


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

    reader_a = await _ensure_output_reader(sess_a)
    reader_b = await _ensure_output_reader(sess_b)

    etype = element_type.strip().lower()

    # Determine element count and series accessor
    if etype == "node":
        var_enum = _resolve_node_var(variable)
        count_a = await asyncio.to_thread(reader_a.get_node_count)
        count_b = await asyncio.to_thread(reader_b.get_node_count)
        count = min(count_a, count_b)

        async def _get_id(reader, idx):
            return await asyncio.to_thread(reader.get_node_id, idx)

        async def _get_series(reader, eid):
            period_count = await asyncio.to_thread(reader.get_period_count)
            return await asyncio.to_thread(reader.get_node_series, eid, var_enum, 0, period_count)

    elif etype == "link":
        var_enum = _resolve_link_var(variable)
        count_a = await asyncio.to_thread(reader_a.get_link_count)
        count_b = await asyncio.to_thread(reader_b.get_link_count)
        count = min(count_a, count_b)

        async def _get_id(reader, idx):
            return await asyncio.to_thread(reader.get_link_id, idx)

        async def _get_series(reader, eid):
            period_count = await asyncio.to_thread(reader.get_period_count)
            return await asyncio.to_thread(reader.get_link_series, eid, var_enum, 0, period_count)

    elif etype == "subcatchment":
        var_enum = _resolve_subcatch_var(variable)
        count_a = await asyncio.to_thread(reader_a.get_subcatch_count)
        count_b = await asyncio.to_thread(reader_b.get_subcatch_count)
        count = min(count_a, count_b)

        async def _get_id(reader, idx):
            return await asyncio.to_thread(reader.get_subcatch_id, idx)

        async def _get_series(reader, eid):
            period_count = await asyncio.to_thread(reader.get_period_count)
            return await asyncio.to_thread(
                reader.get_subcatch_series, eid, var_enum, 0, period_count
            )

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
            series_a = ndarray_to_list(await _get_series(reader_a, eid_a))
            series_b = ndarray_to_list(await _get_series(reader_b, eid_b))
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

    reader = await _ensure_output_reader(session)

    # Resolve output path
    out = Path(output_path).expanduser()
    if not out.is_absolute():
        out = (session.working_dir / out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    from openswmm.engine import OutLinkVar, OutNodeVar

    node_count = await asyncio.to_thread(reader.get_node_count)
    link_count = await asyncio.to_thread(reader.get_link_count)
    period_count = await asyncio.to_thread(reader.get_period_count)
    start_time = await asyncio.to_thread(reader.get_start_time)
    report_step = await asyncio.to_thread(reader.get_report_step)

    # Collect all node and link IDs
    node_ids = []
    for i in range(node_count):
        nid = await asyncio.to_thread(reader.get_node_id, i)
        node_ids.append(nid)

    link_ids = []
    for i in range(link_count):
        lid = await asyncio.to_thread(reader.get_link_id, i)
        link_ids.append(lid)

    # Node variables to export (exclude POLLUT_BASE)
    node_vars = [m for m in OutNodeVar if m != OutNodeVar.POLLUT_BASE]
    link_vars = [m for m in OutLinkVar if m != OutLinkVar.POLLUT_BASE]

    # Pre-fetch all series into memory
    node_series: dict[str, dict[str, list[float]]] = {}
    for nid in node_ids:
        node_series[nid] = {}
        for var in node_vars:
            raw = await asyncio.to_thread(reader.get_node_series, nid, var, 0, period_count)
            node_series[nid][var.name.lower()] = ndarray_to_list(raw)

    link_series: dict[str, dict[str, list[float]]] = {}
    for lid in link_ids:
        link_series[lid] = {}
        for var in link_vars:
            raw = await asyncio.to_thread(reader.get_link_series, lid, var, 0, period_count)
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
                    start_time + p * report_step,
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
                    start_time + p * report_step,
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
        "start_time": start_time,
        "report_step": report_step,
        "elements": records,
    }

    with open(path, "w") as f:
        json.dump(output, f, indent=2)

    return len(records)
