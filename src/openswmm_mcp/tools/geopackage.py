# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Caleb Buahin
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
GeoPackage Tools
================

Tools for querying SWMM GeoPackage databases — simulation results,
observed data import/export, and multi-run scenario comparison.

:author: Caleb Buahin
:copyright: Copyright (c) 2026 Caleb Buahin
:license: Apache-2.0
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP

from ..errors import ErrorCode, ToolError

geopackage_mcp = FastMCP("OpenSWMM GeoPackage Tools")

# Lazy import — GeoPackage is optional
_GeoPackage = None


def _get_gpkg_class():
    global _GeoPackage
    if _GeoPackage is None:
        try:
            from openswmm.engine import GeoPackage

            _GeoPackage = GeoPackage
        except ImportError:
            raise ToolError(
                ErrorCode.ENGINE_ERROR,
                "GeoPackage support not available. "
                "Rebuild openswmm with -DOPENSWMM_WITH_GEOPACKAGE=ON.",
            )
    return _GeoPackage


# In-memory registry of open GeoPackage connections
_gpkg_sessions: dict[str, Any] = {}


@geopackage_mcp.tool()
async def open_geopackage(
    ctx: Context,
    path: str,
    session_id: str = "gpkg_default",
) -> dict:
    """Open a GeoPackage file for querying results or observed data.

    :param path: Path to the .gpkg file.
    :param session_id: Session identifier for this GeoPackage connection.
    :returns: Summary of the GeoPackage contents.
    """
    GeoPackage = _get_gpkg_class()

    if session_id in _gpkg_sessions:
        raise ToolError(ErrorCode.INVALID_STATE, f"GeoPackage session '{session_id}' already open")

    gpkg = await asyncio.to_thread(GeoPackage, path)
    _gpkg_sessions[session_id] = gpkg

    sim_count = await asyncio.to_thread(gpkg.simulation_count)
    sim_ids = await asyncio.to_thread(gpkg.simulation_ids)
    var_count = await asyncio.to_thread(gpkg.variable_count)
    obs_count = await asyncio.to_thread(gpkg.observed_series_count)

    return {
        "session_id": session_id,
        "path": path,
        "simulation_count": sim_count,
        "simulation_ids": sim_ids,
        "variable_count": var_count,
        "observed_series_count": obs_count,
    }


@geopackage_mcp.tool()
async def list_simulations(
    ctx: Context,
    session_id: str = "gpkg_default",
) -> list[dict]:
    """List all simulation runs in the GeoPackage.

    :param session_id: GeoPackage session identifier.
    :returns: List of simulation metadata dicts.
    """
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    sim_ids = await asyncio.to_thread(gpkg.simulation_ids)
    results = []
    for sid in sim_ids:
        counts = await asyncio.to_thread(gpkg.object_counts, sid)
        results.append({"simulation_id": sid, **counts})
    return results


@geopackage_mcp.tool()
async def get_result_timeseries(
    ctx: Context,
    session_id: str = "gpkg_default",
    simulation_id: str = "",
    element_type: str = "NODE",
    element_id: str = "",
    variable: str = "depth",
) -> dict:
    """Read a simulation result timeseries from the GeoPackage.

    :param session_id: GeoPackage session identifier.
    :param simulation_id: Simulation run ID (use list_simulations to find).
    :param element_type: "NODE", "LINK", "SUBCATCH", or "SYSTEM".
    :param element_id: Element identifier (e.g., "J1").
    :param variable: Variable name (e.g., "depth", "flow", "runoff").
    :returns: Dict with times and values arrays.
    """
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    times, values = await asyncio.to_thread(
        gpkg.read_result_ts, simulation_id, element_type.upper(), element_id, variable
    )

    from .._util.formatting import ndarray_to_list

    return {
        "simulation_id": simulation_id,
        "element_type": element_type,
        "element_id": element_id,
        "variable": variable,
        "count": len(times),
        "times": ndarray_to_list(times),
        "values": ndarray_to_list(values),
    }


@geopackage_mcp.tool()
async def get_result_summary(
    ctx: Context,
    session_id: str = "gpkg_default",
    simulation_id: str = "",
    element_type: str = "NODE",
    element_id: str = "",
    variable: str = "max_depth",
) -> dict:
    """Read a summary statistic from the GeoPackage.

    :param session_id: GeoPackage session identifier.
    :param simulation_id: Simulation run ID.
    :param element_type: "NODE", "LINK", or "SUBCATCH".
    :param element_id: Element identifier.
    :param variable: Summary variable (e.g., "max_depth", "max_flow").
    :returns: Dict with the statistic value.
    """
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    value = await asyncio.to_thread(
        gpkg.read_summary, simulation_id, element_type.upper(), element_id, variable
    )

    return {
        "simulation_id": simulation_id,
        "element_type": element_type,
        "element_id": element_id,
        "variable": variable,
        "value": value,
    }


@geopackage_mcp.tool()
async def import_observed_data(
    ctx: Context,
    session_id: str = "gpkg_default",
    name: str = "",
    variable: str = "flow",
    element_type: str = "",
    element_id: str = "",
    timestamps: list[str] | None = None,
    values: list[float] | None = None,
    source: str = "",
    units: str = "",
) -> dict:
    """Import observed/sensor data into the GeoPackage for calibration.

    :param session_id: GeoPackage session identifier.
    :param name: Unique series name (e.g., "USGS_01585200_flow").
    :param variable: Variable measured (e.g., "flow", "depth").
    :param element_type: Model element type to link to (or "" for unlinked).
    :param element_id: Model element ID to link to (or "").
    :param timestamps: List of ISO 8601 timestamp strings.
    :param values: List of measured values.
    :param source: Data source description.
    :param units: Measurement units.
    :returns: Dict with series_id and count.
    """
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")
    if not timestamps or not values:
        raise ToolError(ErrorCode.BAD_PARAM, "timestamps and values are required")
    if len(timestamps) != len(values):
        raise ToolError(ErrorCode.BAD_PARAM, "timestamps and values must have same length")

    series_id = await asyncio.to_thread(
        gpkg.create_observed_series, name, variable, element_type, element_id, source, units
    )

    # Bulk write with transaction
    await asyncio.to_thread(gpkg.begin)
    try:
        await asyncio.to_thread(gpkg.write_observed_values, series_id, timestamps, values)
        await asyncio.to_thread(gpkg.commit)
    except Exception:
        await asyncio.to_thread(gpkg.rollback)
        raise

    return {
        "series_id": series_id,
        "name": name,
        "count": len(values),
    }


@geopackage_mcp.tool()
async def compare_sim_vs_observed(
    ctx: Context,
    session_id: str = "gpkg_default",
    simulation_id: str = "",
    element_type: str = "NODE",
    element_id: str = "",
    variable: str = "depth",
    observed_series_id: int = 0,
) -> dict:
    """Compare simulated results against observed data.

    :param session_id: GeoPackage session identifier.
    :param simulation_id: Simulation run to compare.
    :param element_type: Element type.
    :param element_id: Element ID.
    :param variable: Variable to compare.
    :param observed_series_id: Observed series ID.
    :returns: Comparison statistics (RMSE, NSE, bias).
    """
    import numpy as np

    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    # Read simulated
    sim_times, sim_values = await asyncio.to_thread(
        gpkg.read_result_ts, simulation_id, element_type.upper(), element_id, variable
    )

    # Read observed
    obs_timestamps, obs_values = await asyncio.to_thread(
        gpkg.read_observed_values, observed_series_id
    )

    n_sim = len(sim_values)
    n_obs = len(obs_values)

    # Simple statistics on the shorter series
    n = min(n_sim, n_obs)
    if n == 0:
        return {"error": "No overlapping data"}

    sim = sim_values[:n]
    obs = obs_values[:n]

    residuals = sim - obs
    rmse = float(np.sqrt(np.mean(residuals**2)))
    bias = float(np.mean(residuals))
    obs_mean = float(np.mean(obs))
    ss_res = float(np.sum(residuals**2))
    ss_tot = float(np.sum((obs - obs_mean) ** 2))
    nse = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")

    return {
        "simulation_id": simulation_id,
        "element_type": element_type,
        "element_id": element_id,
        "variable": variable,
        "n_paired": n,
        "rmse": round(rmse, 6),
        "bias": round(bias, 6),
        "nse": round(nse, 4),
        "sim_mean": round(float(np.mean(sim)), 6),
        "obs_mean": round(obs_mean, 6),
    }


@geopackage_mcp.tool()
async def is_registered(ctx: Context) -> dict:
    """Check whether the GeoPackage plugin is registered.

    :returns: Dict with the boolean ``registered`` flag.
    """
    from openswmm.engine import _geopackage

    registered = await asyncio.to_thread(_geopackage.is_registered)
    return {"registered": bool(registered)}


@geopackage_mcp.tool()
async def register(
    ctx: Context,
    key: str = "",
    org: str = "",
    email: str = "",
    deploy: str = "",
) -> dict:
    """Register the GeoPackage plugin.

    :param key: License key, or "" if not required.
    :param org: Organisation name, or "".
    :param email: Contact e-mail, or "".
    :param deploy: Deployment identifier, or "".
    :returns: Dict with the boolean ``registered`` result.
    """
    from openswmm.engine import _geopackage

    ok = await asyncio.to_thread(_geopackage.register, key, org, email, deploy)
    return {"registered": bool(ok)}


@geopackage_mcp.tool()
async def last_error(
    ctx: Context,
    session_id: str = "gpkg_default",
) -> dict:
    """Return the most recent error message from the GeoPackage library.

    :param session_id: GeoPackage session identifier.
    :returns: Dict with the ``last_error`` string ("" if none).
    """
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    message = await asyncio.to_thread(lambda: gpkg.last_error)
    return {"session_id": session_id, "last_error": message}


@geopackage_mcp.tool()
async def query_int(
    ctx: Context,
    session_id: str = "gpkg_default",
    sql: str = "",
) -> dict:
    """Run a read-only SQL query and return the first integer result.

    :param session_id: GeoPackage session identifier.
    :param sql: SELECT query string.
    :returns: Dict with the integer ``value``.
    """
    if not sql:
        raise ToolError(ErrorCode.BAD_PARAM, "sql is required")
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    value = await asyncio.to_thread(gpkg.query_int, sql)
    return {"session_id": session_id, "value": int(value)}


@geopackage_mcp.tool()
async def query_double(
    ctx: Context,
    session_id: str = "gpkg_default",
    sql: str = "",
) -> dict:
    """Run a read-only SQL query and return the first double result.

    :param session_id: GeoPackage session identifier.
    :param sql: SELECT query string.
    :returns: Dict with the float ``value``.
    """
    if not sql:
        raise ToolError(ErrorCode.BAD_PARAM, "sql is required")
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    value = await asyncio.to_thread(gpkg.query_double, sql)
    return {"session_id": session_id, "value": float(value)}


@geopackage_mcp.tool()
async def topology_edge_count(
    ctx: Context,
    session_id: str = "gpkg_default",
    simulation_id: str = "",
) -> dict:
    """Return the number of topology edges for a simulation.

    :param session_id: GeoPackage session identifier.
    :param simulation_id: Simulation run ID.
    :returns: Dict with the integer ``edge_count``.
    """
    if not simulation_id:
        raise ToolError(ErrorCode.BAD_PARAM, "simulation_id is required")
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    count = await asyncio.to_thread(gpkg.topology_edge_count, simulation_id)
    return {
        "session_id": session_id,
        "simulation_id": simulation_id,
        "edge_count": int(count),
    }


@geopackage_mcp.tool()
async def write_observed_value(
    ctx: Context,
    session_id: str = "gpkg_default",
    series_id: int = 0,
    timestamp: str = "",
    value: float = 0.0,
    flag: str = "",
) -> dict:
    """Write a single observed data point to an existing series.

    Use ``import_observed_data`` to create a series and bulk-load points;
    this tool appends one point to a series that already exists.

    :param session_id: GeoPackage session identifier.
    :param series_id: Series ID returned by import_observed_data.
    :param timestamp: ISO 8601 timestamp string.
    :param value: Measured value.
    :param flag: Quality flag (e.g. "A", "P"), or "" for none.
    :returns: Dict confirming the write.
    """
    if not timestamp:
        raise ToolError(ErrorCode.BAD_PARAM, "timestamp is required")
    gpkg = _gpkg_sessions.get(session_id)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    await asyncio.to_thread(
        gpkg.write_observed_value, int(series_id), timestamp, float(value), flag
    )
    return {
        "session_id": session_id,
        "series_id": int(series_id),
        "timestamp": timestamp,
        "value": float(value),
        "status": "ok",
    }


@geopackage_mcp.tool()
async def close_geopackage(
    ctx: Context,
    session_id: str = "gpkg_default",
) -> dict:
    """Close a GeoPackage connection.

    :param session_id: GeoPackage session identifier.
    """
    gpkg = _gpkg_sessions.pop(session_id, None)
    if gpkg is None:
        raise ToolError(ErrorCode.NOT_FOUND, f"GeoPackage session '{session_id}' not found")

    await asyncio.to_thread(gpkg.close)
    return {"session_id": session_id, "status": "closed"}
