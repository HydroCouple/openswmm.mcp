"""Subcatchments tools: fine-grained accessors beyond ``query.get_subcatchment_info``.

The aggregate ``query.get_subcatchment_info`` returns a batched property dict
and ``editing.set_subcatchment_properties`` handles the basic geometry +
roughness + outlet setters. This module adds:

* **Statistics** (3) — precipitation / runoff peaks (via ``stats`` sub-view).
* **Bulk arrays** (2) — runoff + quality across all subcatchments.
* **Current state** (6) — runoff, rainfall, evap, groundwater, snow_depth, infil.
* **Coverage** (2) — land-use coverage get/set via the ``coverage`` mapping.
* **Infiltration models** (8) — model getter + (Horton / Green-Ampt /
  Curve Number) parameter pairs (via ``infiltration`` sub-view).
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
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
from openswmm_mcp.session import SimSession

subcatchments_mcp = FastMCP("subcatchments")


_INFIL_MODELS = {
    0: "horton",
    1: "mod_horton",
    2: "green_ampt",
    3: "mod_green_ampt",
    4: "curve_number",
}


# AquiferParam codes (see openswmm.engine.AquiferParam IntEnum, 0..11).
_AQUIFER_PARAMS: dict[str, int] = {
    "porosity": 0,
    "wilting_point": 1,
    "field_capacity": 2,
    "conductivity": 3,
    "conduct_slope": 4,
    "tension_slope": 5,
    "upper_evap_frac": 6,
    "lower_evap_depth": 7,
    "lower_loss_coeff": 8,
    "bottom_elev": 9,
    "water_table_elev": 10,
    "upper_moisture": 11,
}


def _resolve_aquifer_param(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower()
    if key not in _AQUIFER_PARAMS:
        valid = ", ".join(sorted(_AQUIFER_PARAMS))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown aquifer param '{name}'. Valid params: {valid}."
        )
    return _AQUIFER_PARAMS[key]


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
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] subcatch_id must not be empty.")
    idx = await resolve_index(session.subcatchments, subcatch_id, "Subcatchment")
    if idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{subcatch_id}' not found.")
    return idx


def _zip_subcatch_results(session: SimSession, values: list[float]) -> list[dict[str, Any]]:
    subs = session.subcatchments
    n = len(subs)
    return [
        {"id": subs.get_id(i), "index": i, "value": values[i]}
        for i in range(min(n, len(values)))
    ]


# ===========================================================================
# Identity: tag get/set, bulk ids, outlet-subcatchment routing
# ===========================================================================


@subcatchments_mcp.tool()
async def get_tag(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return the free-form tag string for a subcatchment (empty if untagged).

    Tags come from the INP ``[TAGS]`` section and are keyed by index.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    tag = await asyncio.to_thread(lambda: session.subcatchments[idx].tag)
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "tag": tag,
    }


@subcatchments_mcp.tool()
async def set_tag(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    tag: str = "",
) -> dict:
    """Set (or clear) the free-form tag string for a subcatchment.

    An empty string clears the tag.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    tag_value = tag

    def _set() -> None:
        session.subcatchments[idx].tag = tag_value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "tag": tag_value,
    }


@subcatchments_mcp.tool()
async def get_ids_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return the IDs of all subcatchments in storage order as ``{count, ids}``."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(lambda: session.subcatchments.ids)
    ids = [str(x) for x in ndarray_to_list(arr)]
    return {"session_id": session_id, "count": len(ids), "ids": ids}


@subcatchments_mcp.tool()
async def set_outlet_subcatchment(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    outlet_subcatch_id: str | int = "",
) -> dict:
    """Route a subcatchment's runoff to another subcatchment.

    ``outlet_subcatch_id`` is the receiving subcatchment's name or index.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    outlet_idx = await _resolve_subcatch(session, outlet_subcatch_id)

    def _set() -> None:
        session.subcatchments[idx].set_outlet_subcatchment(outlet_idx)

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "outlet_subcatch_id": outlet_subcatch_id,
        "outlet_subcatch_index": outlet_idx,
    }


# ===========================================================================
# Statistics (v1: subcatchment.stats.<attr>)
# ===========================================================================


async def _read_stat(
    ctx: Context, session_id: str, subcatch_id: str | int, attr: str, key: str
) -> dict:
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    v = await asyncio.to_thread(
        lambda: getattr(session.subcatchments[idx].stats, attr)
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        key: float(v),
    }


@subcatchments_mcp.tool()
async def stat_precip(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return total precipitation volume for a subcatchment."""
    return await _read_stat(ctx, session_id, subcatch_id, "precip", "precipitation")


@subcatchments_mcp.tool()
async def stat_runoff_vol(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return total runoff volume for a subcatchment."""
    return await _read_stat(ctx, session_id, subcatch_id, "runoff_vol", "runoff_vol")


@subcatchments_mcp.tool()
async def stat_max_runoff(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return the peak runoff rate for a subcatchment."""
    return await _read_stat(ctx, session_id, subcatch_id, "max_runoff", "max_runoff")


# ===========================================================================
# Bulk arrays (v1: subcatchments.<attr> numpy property)
# ===========================================================================


@subcatchments_mcp.tool()
async def get_runoff_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current runoff rates for all subcatchments."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(lambda: session.subcatchments.runoffs)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_subcatch_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@subcatchments_mcp.tool()
async def get_quality_bulk(
    ctx: Context,
    session_id: str = "default",
    pollutant_index: int = 0,
) -> dict:
    """Return pollutant concentrations across all subcatchments for one pollutant."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.subcatchments.qualities, pollutant_index)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_subcatch_results, session, values)
    return {
        "session_id": session_id,
        "pollutant_index": pollutant_index,
        "count": len(records),
        "results": records,
    }


# ===========================================================================
# Current state readers (v1: subcatchment.<attr> direct property)
# ===========================================================================


async def _read_attr(
    ctx: Context, session_id: str, subcatch_id: str | int, attr: str, key: str
) -> dict:
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    v = await asyncio.to_thread(lambda: getattr(session.subcatchments[idx], attr))
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        key: float(v),
    }


@subcatchments_mcp.tool()
async def get_runoff(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current runoff rate for a subcatchment."""
    return await _read_attr(ctx, session_id, subcatch_id, "runoff", "runoff")


@subcatchments_mcp.tool()
async def get_rainfall(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current rainfall rate for a subcatchment."""
    return await _read_attr(ctx, session_id, subcatch_id, "rainfall", "rainfall")


@subcatchments_mcp.tool()
async def get_evap(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current evaporation rate for a subcatchment."""
    return await _read_attr(ctx, session_id, subcatch_id, "evap", "evap")


@subcatchments_mcp.tool()
async def get_groundwater(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current groundwater flow for a subcatchment."""
    return await _read_attr(ctx, session_id, subcatch_id, "groundwater", "groundwater")


@subcatchments_mcp.tool()
async def get_snow_depth(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current snow depth on a subcatchment."""
    return await _read_attr(ctx, session_id, subcatch_id, "snow_depth", "snow_depth")


@subcatchments_mcp.tool()
async def get_infil(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current infiltration rate for a subcatchment."""
    return await _read_attr(ctx, session_id, subcatch_id, "infil", "infil")


# ===========================================================================
# Groundwater / snow state injection (RUNNING-only; openswmm backend)
# ===========================================================================


@subcatchments_mcp.tool()
async def set_gw_state(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    theta: float = -1.0,
    lower_depth: float = -1.0,
) -> dict:
    """Inject the groundwater state on a subcatchment (running only).

    Overwrites the live upper-zone moisture and/or saturated-zone depth so a
    caller can warm-start or perturb groundwater mid-run. Pass a negative
    value to leave that field unchanged.

    Parameters
    ----------
    subcatch_id:
        Subcatchment name or index.
    theta:
        Upper-zone moisture content (0..porosity); negative leaves it as-is.
    lower_depth:
        Saturated-zone depth above the aquifer bottom in project length
        units; negative leaves it as-is.
    """
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    idx = await _resolve_subcatch(session, subcatch_id)
    th, ld = float(theta), float(lower_depth)
    await asyncio.to_thread(
        lambda: session.subcatchments[idx].set_gw_state(theta=th, lower_depth=ld)
    )
    new_theta, new_lower = await asyncio.to_thread(
        lambda: session.subcatchments[idx].get_gw_state()
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "theta": new_theta,
        "lower_depth": new_lower,
    }


@subcatchments_mcp.tool()
async def set_snow_state(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    surface: int = 2,
    swe: float = -1.0,
    free_water: float = -1.0,
    ati: float = -1000.0,
    cold_content: float = -1.0,
) -> dict:
    """Inject the snow-pack state on one snow surface (running only).

    Overwrites the live snow-pack state on a single snow subarea so a caller
    can warm-start or perturb the snowpack mid-run. Pass the documented
    sentinel (negative for depths, -1000 for ATI) to leave a field unchanged.

    Parameters
    ----------
    subcatch_id:
        Subcatchment name or index.
    surface:
        Snow subarea: 0 plowable, 1 impervious, 2 pervious (default).
    swe:
        Snow water equivalent in project depth units; negative leaves as-is.
    free_water:
        Free water in project depth units; negative leaves as-is.
    ati:
        Antecedent temperature index (deg F US, deg C SI); -1000 leaves as-is.
    cold_content:
        Cold content in project depth units of melt equivalent; negative
        leaves as-is.
    """
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    idx = await _resolve_subcatch(session, subcatch_id)
    surf = int(surface)
    args = (float(swe), float(free_water), float(ati), float(cold_content))
    await asyncio.to_thread(
        lambda: session.subcatchments[idx].set_snow_state(surf, *args)
    )
    new_swe, new_fw, new_ati, new_cc = await asyncio.to_thread(
        lambda: session.subcatchments[idx].get_snow_state(surf)
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "surface": surf,
        "swe": new_swe,
        "free_water": new_fw,
        "ati": new_ati,
        "cold_content": new_cc,
    }


# ===========================================================================
# Coverage (v1: subcatchment.coverage[landuse_key])
# ===========================================================================


@subcatchments_mcp.tool()
async def get_coverage(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    landuse_index: int = 0,
) -> dict:
    """Return the land-use coverage fraction (0..1) for a (subcatch, landuse) pair."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    cov = await asyncio.to_thread(
        lambda: session.subcatchments[idx].coverage[landuse_index]
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "landuse_index": landuse_index,
        "coverage": float(cov),
    }


@subcatchments_mcp.tool()
async def set_coverage(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    landuse_index: int = 0,
    fraction: float = 0.0,
) -> dict:
    """Set the land-use coverage fraction (0..1) for a (subcatch, landuse) pair."""
    if not 0.0 <= fraction <= 1.0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] fraction must be in [0, 1]; got {fraction}."
        )
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    value = float(fraction)

    def _set() -> None:
        session.subcatchments[idx].coverage[landuse_index] = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "landuse_index": landuse_index,
        "coverage": fraction,
    }


# ===========================================================================
# Infiltration models (v1: subcatchment.infiltration sub-view)
# ===========================================================================


@subcatchments_mcp.tool()
async def get_infil_model(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the infiltration model type for a subcatchment.

    Model codes: 0=HORTON, 1=MOD_HORTON, 2=GREEN_AMPT, 3=MOD_GREEN_AMPT,
    4=CURVE_NUMBER.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    # v1 InfiltrationView.model returns an InfilModel IntEnum.
    code = await asyncio.to_thread(
        lambda: int(session.subcatchments[idx].infiltration.model)
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "model_code": code,
        "model": _INFIL_MODELS.get(code, "unknown"),
    }


@subcatchments_mcp.tool()
async def get_infil_horton(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return Horton infiltration params ``(f0, fmin, decay, dry_time)``."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    f0, fmin, decay, dry_time = await asyncio.to_thread(
        lambda: session.subcatchments[idx].infiltration.horton
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "f0": f0,
        "fmin": fmin,
        "decay": decay,
        "dry_time": dry_time,
    }


@subcatchments_mcp.tool()
async def set_infil_horton(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    f0: float = 0.0,
    fmin: float = 0.0,
    decay: float = 0.0,
    dry_time: float = 0.0,
) -> dict:
    """Set Horton infiltration params for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    args = (float(f0), float(fmin), float(decay), float(dry_time))
    await asyncio.to_thread(
        lambda: session.subcatchments[idx].infiltration.set_horton(*args)
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "f0": f0,
        "fmin": fmin,
        "decay": decay,
        "dry_time": dry_time,
    }


@subcatchments_mcp.tool()
async def get_infil_green_ampt(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return Green-Ampt params ``(suction, conductivity, initial_deficit)``."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    suction, ksat, deficit = await asyncio.to_thread(
        lambda: session.subcatchments[idx].infiltration.green_ampt
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "suction": suction,
        "conductivity": ksat,
        "initial_deficit": deficit,
    }


@subcatchments_mcp.tool()
async def set_infil_green_ampt(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    suction: float = 0.0,
    conductivity: float = 0.0,
    initial_deficit: float = 0.0,
) -> dict:
    """Set Green-Ampt infiltration params for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    args = (float(suction), float(conductivity), float(initial_deficit))
    await asyncio.to_thread(
        lambda: session.subcatchments[idx].infiltration.set_green_ampt(*args)
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "suction": suction,
        "conductivity": conductivity,
        "initial_deficit": initial_deficit,
    }


@subcatchments_mcp.tool()
async def get_infil_curve_number(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the SCS Curve Number for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    cn = await asyncio.to_thread(
        lambda: session.subcatchments[idx].infiltration.curve_number
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "curve_number": float(cn),
    }


@subcatchments_mcp.tool()
async def set_infil_curve_number(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    curve_number: float = 0.0,
) -> dict:
    """Set the SCS Curve Number for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    cn = float(curve_number)
    await asyncio.to_thread(
        lambda: session.subcatchments[idx].infiltration.set_curve_number(cn)
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "curve_number": curve_number,
    }


# ===========================================================================
# Quality (v1: subcatchment.quality(p), ponded_quality(p), set_ponded_quality(p, m))
# ===========================================================================


@subcatchments_mcp.tool()
async def get_quality(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    pollutant_index: int = 0,
) -> dict:
    """Return the runoff pollutant concentration for a subcatchment."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    conc = await asyncio.to_thread(
        lambda: session.subcatchments[idx].quality(pollutant_index)
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "pollutant_index": pollutant_index,
        "concentration": float(conc),
    }


@subcatchments_mcp.tool()
async def get_ponded_quality(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    pollutant_index: int = 0,
) -> dict:
    """Return the ponded pollutant mass on a subcatchment surface."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    mass = await asyncio.to_thread(
        lambda: session.subcatchments[idx].ponded_quality(pollutant_index)
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "pollutant_index": pollutant_index,
        "ponded_mass": float(mass),
    }


@subcatchments_mcp.tool()
async def set_ponded_quality(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    pollutant_index: int = 0,
    ponded_mass: float = 0.0,
) -> dict:
    """Set the ponded pollutant mass on a subcatchment surface."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    mass_value = float(ponded_mass)
    await asyncio.to_thread(
        lambda: session.subcatchments[idx].set_ponded_quality(pollutant_index, mass_value)
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "pollutant_index": pollutant_index,
        "ponded_mass": ponded_mass,
    }


# ===========================================================================
# Aquifer parameters (v1: session.aquifers.get_param / set_param)
# ===========================================================================


@subcatchments_mcp.tool()
async def aquifer_get_param(
    ctx: Context,
    session_id: str = "default",
    aquifer_id: str | int = "",
    param: str | int = "porosity",
) -> dict:
    """Return an aquifer parameter value (input-file units).

    ``aquifer_id`` is an aquifer name or index (``[AQUIFERS]`` section).
    ``param`` is one of: porosity, wilting_point, field_capacity,
    conductivity, conduct_slope, tension_slope, upper_evap_frac,
    lower_evap_depth, lower_loss_coeff, bottom_elev, water_table_elev,
    upper_moisture (or the integer code 0..11).
    """
    param_code = _resolve_aquifer_param(param)
    session = await _get_session(ctx, session_id)
    value = await asyncio.to_thread(
        lambda: session.aquifers.get_param(aquifer_id, param_code)
    )
    return {
        "session_id": session_id,
        "aquifer_id": aquifer_id,
        "param": param,
        "param_code": param_code,
        "value": float(value),
    }


@subcatchments_mcp.tool()
async def aquifer_set_param(
    ctx: Context,
    session_id: str = "default",
    aquifer_id: str | int = "",
    param: str | int = "porosity",
    value: float = 0.0,
) -> dict:
    """Set an aquifer parameter value (input-file units).

    ``aquifer_id`` is an aquifer name or index. ``param`` accepts the same
    tokens as ``aquifer_get_param``. Flux-coefficient parameters take effect
    on the next step mid-run; structural / initial-condition parameters are
    pre-start-only and the engine raises while the simulation is running.
    """
    param_code = _resolve_aquifer_param(param)
    session = await _get_session(ctx, session_id)
    value_f = float(value)
    await asyncio.to_thread(
        lambda: session.aquifers.set_param(aquifer_id, param_code, value_f)
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "aquifer_id": aquifer_id,
        "param": param,
        "param_code": param_code,
        "value": value,
    }
