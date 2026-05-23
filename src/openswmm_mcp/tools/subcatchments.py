"""Subcatchments tools: fine-grained accessors beyond ``query.get_subcatchment_info``.

The aggregate ``query.get_subcatchment_info`` returns a batched property dict
and ``editing.set_subcatchment_properties`` handles the basic geometry +
roughness + outlet setters. This module adds:

* **Statistics** (3) — precipitation / runoff peaks.
* **Bulk arrays** (2) — runoff + quality across all subcatchments.
* **Current state** (6) — runoff, rainfall, evap, groundwater, snow_depth, infil.
* **Coverage** (2) — land-use coverage get/set.
* **Infiltration models** (8) — model getter + (Horton / Green-Ampt /
  Curve Number) parameter pairs.
* **Quality** (3) — ponded quality get/set + per-subcatchment quality.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP

from openswmm_mcp._util.formatting import ndarray_to_list
from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

subcatchments_mcp = FastMCP("subcatchments")


_INFIL_MODELS = {
    0: "horton",
    1: "mod_horton",
    2: "green_ampt",
    3: "mod_green_ampt",
    4: "curve_number",
}


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Subcatchments (fine-grained accessors)")
    return session


async def _resolve_subcatch(session: SimSession, subcatch_id: str | int) -> int:
    if isinstance(subcatch_id, int):
        return subcatch_id
    if not subcatch_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] subcatch_id must not be empty."
        )
    idx = await asyncio.to_thread(session.subcatchments.get_index, subcatch_id)
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{subcatch_id}' not found."
        )
    return idx


def _zip_subcatch_results(
    session: SimSession, values: list[float]
) -> list[dict[str, Any]]:
    n = session.subcatchments.count()
    return [
        {"id": session.subcatchments.get_id(i), "index": i, "value": values[i]}
        for i in range(min(n, len(values)))
    ]


# ===========================================================================
# Statistics
# ===========================================================================


async def _subcatch_stat(
    ctx: Context, session_id: str, subcatch_id: str | int,
    attr: str, key: str,
) -> dict:
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    v = await asyncio.to_thread(getattr(session.subcatchments, attr), idx)
    return {
        "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx, key: float(v),
    }


@subcatchments_mcp.tool()
async def stat_precip(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return total precipitation volume for a subcatchment."""
    return await _subcatch_stat(
        ctx, session_id, subcatch_id, "get_stat_precip", "precipitation",
    )


@subcatchments_mcp.tool()
async def stat_runoff_vol(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return total runoff volume for a subcatchment."""
    return await _subcatch_stat(
        ctx, session_id, subcatch_id, "get_stat_runoff_vol", "runoff_vol",
    )


@subcatchments_mcp.tool()
async def stat_max_runoff(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return the peak runoff rate for a subcatchment."""
    return await _subcatch_stat(
        ctx, session_id, subcatch_id, "get_stat_max_runoff", "max_runoff",
    )


# ===========================================================================
# Bulk arrays
# ===========================================================================


@subcatchments_mcp.tool()
async def get_runoff_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current runoff rates for all subcatchments."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.subcatchments.get_runoff_bulk)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_subcatch_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@subcatchments_mcp.tool()
async def get_quality_bulk(
    ctx: Context, session_id: str = "default", pollutant_index: int = 0,
) -> dict:
    """Return pollutant concentrations across all subcatchments for one pollutant."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(
        session.subcatchments.get_quality_bulk, pollutant_index
    )
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_subcatch_results, session, values)
    return {
        "session_id": session_id, "pollutant_index": pollutant_index,
        "count": len(records), "results": records,
    }


# ===========================================================================
# Current state readers (RUNNING / ENDED)
# ===========================================================================


async def _state_reader(
    ctx: Context, session_id: str, subcatch_id: str | int,
    attr: str, key: str,
) -> dict:
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    v = await asyncio.to_thread(getattr(session.subcatchments, attr), idx)
    return {
        "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx, key: float(v),
    }


@subcatchments_mcp.tool()
async def get_runoff(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return the current runoff rate for a subcatchment."""
    return await _state_reader(
        ctx, session_id, subcatch_id, "get_runoff", "runoff",
    )


@subcatchments_mcp.tool()
async def get_rainfall(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return the current rainfall rate for a subcatchment."""
    return await _state_reader(
        ctx, session_id, subcatch_id, "get_rainfall", "rainfall",
    )


@subcatchments_mcp.tool()
async def get_evap(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return the current evaporation rate for a subcatchment."""
    return await _state_reader(
        ctx, session_id, subcatch_id, "get_evap", "evap",
    )


@subcatchments_mcp.tool()
async def get_groundwater(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return the current groundwater flow for a subcatchment."""
    return await _state_reader(
        ctx, session_id, subcatch_id, "get_groundwater", "groundwater",
    )


@subcatchments_mcp.tool()
async def get_snow_depth(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return the current snow depth on a subcatchment."""
    return await _state_reader(
        ctx, session_id, subcatch_id, "get_snow_depth", "snow_depth",
    )


@subcatchments_mcp.tool()
async def get_infil(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return the current infiltration rate for a subcatchment."""
    return await _state_reader(
        ctx, session_id, subcatch_id, "get_infil", "infil",
    )


# ===========================================================================
# Coverage (land use fractions)
# ===========================================================================


@subcatchments_mcp.tool()
async def get_coverage(
    ctx: Context, session_id: str = "default",
    subcatch_id: str | int = "", landuse_index: int = 0,
) -> dict:
    """Return the land-use coverage fraction (0..1) for a (subcatch, landuse) pair."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    cov = await asyncio.to_thread(
        session.subcatchments.get_coverage, idx, landuse_index
    )
    return {
        "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx, "landuse_index": landuse_index,
        "coverage": float(cov),
    }


@subcatchments_mcp.tool()
async def set_coverage(
    ctx: Context, session_id: str = "default",
    subcatch_id: str | int = "", landuse_index: int = 0,
    fraction: float = 0.0,
) -> dict:
    """Set the land-use coverage fraction (0..1) for a (subcatch, landuse) pair."""
    if not 0.0 <= fraction <= 1.0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] fraction must be in [0, 1]; "
            f"got {fraction}."
        )
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    await asyncio.to_thread(
        session.subcatchments.set_coverage, idx, landuse_index, float(fraction)
    )
    return {
        "status": "ok", "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx, "landuse_index": landuse_index,
        "coverage": fraction,
    }


# ===========================================================================
# Infiltration models
# ===========================================================================


@subcatchments_mcp.tool()
async def get_infil_model(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return the infiltration model type for a subcatchment.

    Model codes: 0=HORTON, 1=MOD_HORTON, 2=GREEN_AMPT, 3=MOD_GREEN_AMPT,
    4=CURVE_NUMBER.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    code = await asyncio.to_thread(session.subcatchments.get_infil_model, idx)
    return {
        "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx, "model_code": int(code),
        "model": _INFIL_MODELS.get(int(code), "unknown"),
    }


@subcatchments_mcp.tool()
async def get_infil_horton(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return Horton infiltration params ``(f0, fmin, decay, dry_time)``."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    f0, fmin, decay, dry_time = await asyncio.to_thread(
        session.subcatchments.get_infil_horton, idx
    )
    return {
        "session_id": session_id, "subcatch_id": subcatch_id, "subcatch_index": idx,
        "f0": f0, "fmin": fmin, "decay": decay, "dry_time": dry_time,
    }


@subcatchments_mcp.tool()
async def set_infil_horton(
    ctx: Context, session_id: str = "default",
    subcatch_id: str | int = "",
    f0: float = 0.0, fmin: float = 0.0, decay: float = 0.0, dry_time: float = 0.0,
) -> dict:
    """Set Horton infiltration params for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    await asyncio.to_thread(
        session.subcatchments.set_infil_horton, idx,
        float(f0), float(fmin), float(decay), float(dry_time),
    )
    return {
        "status": "ok", "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "f0": f0, "fmin": fmin, "decay": decay, "dry_time": dry_time,
    }


@subcatchments_mcp.tool()
async def get_infil_green_ampt(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return Green-Ampt params ``(suction, conductivity, initial_deficit)``."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    suction, ksat, deficit = await asyncio.to_thread(
        session.subcatchments.get_infil_green_ampt, idx
    )
    return {
        "session_id": session_id, "subcatch_id": subcatch_id, "subcatch_index": idx,
        "suction": suction, "conductivity": ksat, "initial_deficit": deficit,
    }


@subcatchments_mcp.tool()
async def set_infil_green_ampt(
    ctx: Context, session_id: str = "default",
    subcatch_id: str | int = "",
    suction: float = 0.0, conductivity: float = 0.0, initial_deficit: float = 0.0,
) -> dict:
    """Set Green-Ampt infiltration params for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    await asyncio.to_thread(
        session.subcatchments.set_infil_green_ampt, idx,
        float(suction), float(conductivity), float(initial_deficit),
    )
    return {
        "status": "ok", "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "suction": suction, "conductivity": conductivity,
        "initial_deficit": initial_deficit,
    }


@subcatchments_mcp.tool()
async def get_infil_curve_number(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = "",
) -> dict:
    """Return the SCS Curve Number for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    cn = await asyncio.to_thread(
        session.subcatchments.get_infil_curve_number, idx
    )
    return {
        "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx, "curve_number": float(cn),
    }


@subcatchments_mcp.tool()
async def set_infil_curve_number(
    ctx: Context, session_id: str = "default",
    subcatch_id: str | int = "", curve_number: float = 0.0,
) -> dict:
    """Set the SCS Curve Number for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    await asyncio.to_thread(
        session.subcatchments.set_infil_curve_number, idx, float(curve_number)
    )
    return {
        "status": "ok", "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx, "curve_number": curve_number,
    }


# ===========================================================================
# Quality
# ===========================================================================


@subcatchments_mcp.tool()
async def get_quality(
    ctx: Context, session_id: str = "default",
    subcatch_id: str | int = "", pollutant_index: int = 0,
) -> dict:
    """Return the runoff pollutant concentration for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    conc = await asyncio.to_thread(
        session.subcatchments.get_quality, idx, pollutant_index
    )
    return {
        "session_id": session_id, "subcatch_id": subcatch_id, "subcatch_index": idx,
        "pollutant_index": pollutant_index, "concentration": float(conc),
    }


@subcatchments_mcp.tool()
async def get_ponded_quality(
    ctx: Context, session_id: str = "default",
    subcatch_id: str | int = "", pollutant_index: int = 0,
) -> dict:
    """Return the ponded pollutant mass on a subcatchment surface."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    mass = await asyncio.to_thread(
        session.subcatchments.get_ponded_quality, idx, pollutant_index
    )
    return {
        "session_id": session_id, "subcatch_id": subcatch_id, "subcatch_index": idx,
        "pollutant_index": pollutant_index, "ponded_mass": float(mass),
    }


@subcatchments_mcp.tool()
async def set_ponded_quality(
    ctx: Context, session_id: str = "default",
    subcatch_id: str | int = "", pollutant_index: int = 0,
    ponded_mass: float = 0.0,
) -> dict:
    """Set the ponded pollutant mass on a subcatchment surface."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    await asyncio.to_thread(
        session.subcatchments.set_ponded_quality,
        idx, pollutant_index, float(ponded_mass),
    )
    return {
        "status": "ok", "session_id": session_id, "subcatch_id": subcatch_id,
        "subcatch_index": idx, "pollutant_index": pollutant_index,
        "ponded_mass": ponded_mass,
    }
