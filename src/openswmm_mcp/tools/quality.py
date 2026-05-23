"""Quality tools: landuse / buildup / washoff / treatment.

Complements ``spatial_quality.set_treatment`` (already in place) with the
remaining ``openswmm.engine.Quality`` surface:

* **Landuse** identity + sweep parameters.
* **Buildup** function setters / getters (per landuse + pollutant).
* **Washoff** function setters / getters.
* **Treatment** read-back + clear (set is already in spatial_quality).

The Python ``Quality`` accessor takes integer indices for landuse,
pollutant, and node. Tools accept either string ids (resolved via the
corresponding accessor) or integer indices for pollutant / node, while
landuse uses ``landuse_id`` strings.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

quality_mcp = FastMCP("quality")


# ---------------------------------------------------------------------------
# Buildup / washoff function-type maps.
# ---------------------------------------------------------------------------

_BUILDUP_FUNCS = {0: "none", 1: "power", 2: "exponential", 3: "saturation", 4: "external"}
_BUILDUP_FUNCS_REVERSE = {v: k for k, v in _BUILDUP_FUNCS.items()}

_WASHOFF_FUNCS = {0: "exponential", 1: "rating_curve", 2: "event_mean_conc"}
_WASHOFF_FUNCS_REVERSE = {v: k for k, v in _WASHOFF_FUNCS.items()}

_NORMALIZERS = {0: "per_area", 1: "per_curb"}
_NORMALIZERS_REVERSE = {v: k for k, v in _NORMALIZERS.items()}


def _resolve_buildup_func(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower().replace("-", "_")
    if key not in _BUILDUP_FUNCS_REVERSE:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown buildup func '{name}'. "
            f"Valid: none, power, exponential, saturation, external."
        )
    return _BUILDUP_FUNCS_REVERSE[key]


def _resolve_washoff_func(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower().replace("-", "_")
    if key not in _WASHOFF_FUNCS_REVERSE:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown washoff func '{name}'. "
            f"Valid: exponential, rating_curve, event_mean_conc."
        )
    return _WASHOFF_FUNCS_REVERSE[key]


def _resolve_normalizer(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower().replace("-", "_")
    if key not in _NORMALIZERS_REVERSE:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown normalizer '{name}'. "
            f"Valid: per_area, per_curb."
        )
    return _NORMALIZERS_REVERSE[key]


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Quality (landuse / buildup / washoff)")
    return session


async def _resolve_landuse(session: SimSession, landuse_id: str | int) -> int:
    if isinstance(landuse_id, int):
        return landuse_id
    if not landuse_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] landuse_id must not be empty."
        )
    idx = await asyncio.to_thread(session.quality.landuse_index, landuse_id)
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Landuse '{landuse_id}' not found."
        )
    return idx


async def _resolve_pollutant(session: SimSession, pollutant_id: str | int) -> int:
    if isinstance(pollutant_id, int):
        return pollutant_id
    if not pollutant_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] pollutant_id must not be empty."
        )
    idx = await asyncio.to_thread(session.pollutants.get_index, pollutant_id)
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Pollutant '{pollutant_id}' not found."
        )
    return idx


# ===========================================================================
# [LANDUSES]
# ===========================================================================


@quality_mcp.tool()
async def landuse_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of landuses defined in the model."""
    session = await _get_session(ctx, session_id)
    n = await asyncio.to_thread(session.quality.landuse_count)
    return {"session_id": session_id, "count": n}


@quality_mcp.tool()
async def landuse_add(
    ctx: Context, session_id: str = "default", landuse_id: str = ""
) -> dict:
    """Add a new landuse to the model (BUILDING state)."""
    if not landuse_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] landuse_id must not be empty."
        )
    session = await _get_session(ctx, session_id)
    await asyncio.to_thread(session.quality.landuse_add, landuse_id)
    idx = await asyncio.to_thread(session.quality.landuse_index, landuse_id)
    return {
        "status": "ok", "session_id": session_id,
        "id": landuse_id, "index": idx,
    }


@quality_mcp.tool()
async def landuse_id(
    ctx: Context, session_id: str = "default", index: int = 0
) -> dict:
    """Return the string id of the I{index}-th landuse."""
    session = await _get_session(ctx, session_id)
    s = await asyncio.to_thread(session.quality.landuse_id, index)
    return {"session_id": session_id, "index": index, "id": s}


@quality_mcp.tool()
async def landuse_index(
    ctx: Context, session_id: str = "default", landuse_id: str = ""
) -> dict:
    """Return the integer index for a landuse string id (-1 if not found)."""
    session = await _get_session(ctx, session_id)
    idx = await asyncio.to_thread(session.quality.landuse_index, landuse_id)
    return {"session_id": session_id, "id": landuse_id, "index": idx}


@quality_mcp.tool()
async def get_sweep_interval(
    ctx: Context, session_id: str = "default", landuse_id: str | int = ""
) -> dict:
    """Return the days-between-street-sweeps for a landuse."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_landuse(session, landuse_id)
    v = await asyncio.to_thread(session.quality.landuse_get_sweep_interval, idx)
    return {
        "session_id": session_id, "landuse_id": landuse_id,
        "landuse_index": idx, "sweep_interval_days": float(v),
    }


@quality_mcp.tool()
async def set_sweep_interval(
    ctx: Context, session_id: str = "default",
    landuse_id: str | int = "", days: float = 0.0,
) -> dict:
    """Set the days-between-street-sweeps for a landuse."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_landuse(session, landuse_id)
    await asyncio.to_thread(
        session.quality.landuse_set_sweep_interval, idx, float(days)
    )
    return {
        "status": "ok", "session_id": session_id, "landuse_id": landuse_id,
        "landuse_index": idx, "sweep_interval_days": days,
    }


@quality_mcp.tool()
async def get_sweep_removal(
    ctx: Context, session_id: str = "default", landuse_id: str | int = ""
) -> dict:
    """Return the sweep removal fraction (0..1) for a landuse."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_landuse(session, landuse_id)
    v = await asyncio.to_thread(session.quality.landuse_get_sweep_removal, idx)
    return {
        "session_id": session_id, "landuse_id": landuse_id,
        "landuse_index": idx, "removal_fraction": float(v),
    }


@quality_mcp.tool()
async def set_sweep_removal(
    ctx: Context, session_id: str = "default",
    landuse_id: str | int = "", fraction: float = 0.0,
) -> dict:
    """Set the sweep removal fraction for a landuse (must be in [0, 1])."""
    if not 0.0 <= fraction <= 1.0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] fraction must be in [0, 1]; "
            f"got {fraction}."
        )
    session = await _get_session(ctx, session_id)
    idx = await _resolve_landuse(session, landuse_id)
    await asyncio.to_thread(
        session.quality.landuse_set_sweep_removal, idx, float(fraction)
    )
    return {
        "status": "ok", "session_id": session_id, "landuse_id": landuse_id,
        "landuse_index": idx, "removal_fraction": fraction,
    }


# ===========================================================================
# [BUILDUP] / [WASHOFF]
# ===========================================================================


@quality_mcp.tool()
async def buildup_get(
    ctx: Context, session_id: str = "default",
    landuse_id: str | int = "", pollutant_id: str | int = "",
) -> dict:
    """Return the buildup function parameters for a (landuse, pollutant) pair."""
    session = await _get_session(ctx, session_id)
    l_idx = await _resolve_landuse(session, landuse_id)
    p_idx = await _resolve_pollutant(session, pollutant_id)
    func_code, c1, c2, c3, norm_code = await asyncio.to_thread(
        session.quality.buildup_get, l_idx, p_idx
    )
    return {
        "session_id": session_id, "landuse_id": landuse_id, "landuse_index": l_idx,
        "pollutant_id": pollutant_id, "pollutant_index": p_idx,
        "function_code": int(func_code),
        "function": _BUILDUP_FUNCS.get(int(func_code), "unknown"),
        "c1": c1, "c2": c2, "c3": c3,
        "normalizer_code": int(norm_code),
        "normalizer": _NORMALIZERS.get(int(norm_code), "unknown"),
    }


@quality_mcp.tool()
async def buildup_set(
    ctx: Context, session_id: str = "default",
    landuse_id: str | int = "", pollutant_id: str | int = "",
    function: str = "none", c1: float = 0.0, c2: float = 0.0, c3: float = 0.0,
    normalizer: str = "per_area",
) -> dict:
    """Set the buildup function for a (landuse, pollutant) pair.

    ``function``: ``none`` / ``power`` / ``exponential`` / ``saturation`` /
    ``external``. ``normalizer``: ``per_area`` / ``per_curb``.
    """
    func_int = _resolve_buildup_func(function)
    norm_int = _resolve_normalizer(normalizer)
    session = await _get_session(ctx, session_id)
    l_idx = await _resolve_landuse(session, landuse_id)
    p_idx = await _resolve_pollutant(session, pollutant_id)
    await asyncio.to_thread(
        session.quality.buildup_set, l_idx, p_idx,
        func_int, float(c1), float(c2), float(c3), norm_int,
    )
    return {
        "status": "ok", "session_id": session_id,
        "landuse_id": landuse_id, "landuse_index": l_idx,
        "pollutant_id": pollutant_id, "pollutant_index": p_idx,
        "function": function, "c1": c1, "c2": c2, "c3": c3,
        "normalizer": normalizer,
    }


@quality_mcp.tool()
async def washoff_get(
    ctx: Context, session_id: str = "default",
    landuse_id: str | int = "", pollutant_id: str | int = "",
) -> dict:
    """Return the washoff function parameters for a (landuse, pollutant) pair."""
    session = await _get_session(ctx, session_id)
    l_idx = await _resolve_landuse(session, landuse_id)
    p_idx = await _resolve_pollutant(session, pollutant_id)
    result = await asyncio.to_thread(
        session.quality.washoff_get, l_idx, p_idx
    )
    # washoff_get returns at least (func_code, c1, c2, sweep_eff, bmp_eff)
    # depending on engine version; serialize defensively.
    if isinstance(result, (list, tuple)):
        func_code = int(result[0]) if len(result) > 0 else 0
        c1 = float(result[1]) if len(result) > 1 else 0.0
        c2 = float(result[2]) if len(result) > 2 else 0.0
        sweep_eff = float(result[3]) if len(result) > 3 else 0.0
        bmp_eff = float(result[4]) if len(result) > 4 else 0.0
    else:
        func_code, c1, c2, sweep_eff, bmp_eff = 0, 0.0, 0.0, 0.0, 0.0
    return {
        "session_id": session_id, "landuse_id": landuse_id, "landuse_index": l_idx,
        "pollutant_id": pollutant_id, "pollutant_index": p_idx,
        "function_code": func_code,
        "function": _WASHOFF_FUNCS.get(func_code, "unknown"),
        "c1": c1, "c2": c2,
        "sweep_efficiency": sweep_eff, "bmp_efficiency": bmp_eff,
    }


@quality_mcp.tool()
async def washoff_set(
    ctx: Context, session_id: str = "default",
    landuse_id: str | int = "", pollutant_id: str | int = "",
    function: str = "exponential", c1: float = 0.0, c2: float = 0.0,
    sweep_efficiency: float = 0.0, bmp_efficiency: float = 0.0,
) -> dict:
    """Set the washoff function for a (landuse, pollutant) pair.

    ``function``: ``exponential`` / ``rating_curve`` / ``event_mean_conc``.
    """
    func_int = _resolve_washoff_func(function)
    session = await _get_session(ctx, session_id)
    l_idx = await _resolve_landuse(session, landuse_id)
    p_idx = await _resolve_pollutant(session, pollutant_id)
    await asyncio.to_thread(
        session.quality.washoff_set, l_idx, p_idx,
        func_int, float(c1), float(c2),
        float(sweep_efficiency), float(bmp_efficiency),
    )
    return {
        "status": "ok", "session_id": session_id,
        "landuse_id": landuse_id, "landuse_index": l_idx,
        "pollutant_id": pollutant_id, "pollutant_index": p_idx,
        "function": function, "c1": c1, "c2": c2,
        "sweep_efficiency": sweep_efficiency, "bmp_efficiency": bmp_efficiency,
    }


# ===========================================================================
# Treatment read + clear (set lives in spatial_quality already)
# ===========================================================================


@quality_mcp.tool()
async def treatment_get(
    ctx: Context, session_id: str = "default",
    node_id: str = "", pollutant_id: str | int = "",
) -> dict:
    """Return the treatment expression text for a (node, pollutant) pair."""
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")
    session = await _get_session(ctx, session_id)
    node_idx = await asyncio.to_thread(session.nodes.get_index, node_id)
    if node_idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found."
        )
    pollut_idx = await _resolve_pollutant(session, pollutant_id)
    expr = await asyncio.to_thread(
        session.quality.treatment_get, node_idx, pollut_idx
    )
    return {
        "session_id": session_id, "node_id": node_id, "node_index": node_idx,
        "pollutant_id": pollutant_id, "pollutant_index": pollut_idx,
        "expression": expr or "",
    }


@quality_mcp.tool()
async def treatment_clear(
    ctx: Context, session_id: str = "default",
    node_id: str = "", pollutant_id: str | int = "",
) -> dict:
    """Remove the treatment expression for a (node, pollutant) pair."""
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")
    session = await _get_session(ctx, session_id)
    node_idx = await asyncio.to_thread(session.nodes.get_index, node_id)
    if node_idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found."
        )
    pollut_idx = await _resolve_pollutant(session, pollutant_id)
    await asyncio.to_thread(
        session.quality.treatment_clear, node_idx, pollut_idx
    )
    return {
        "status": "ok", "session_id": session_id,
        "node_id": node_id, "node_index": node_idx,
        "pollutant_id": pollutant_id, "pollutant_index": pollut_idx,
    }
