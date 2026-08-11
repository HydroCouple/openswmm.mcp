"""Subcatchments tools: fine-grained accessors beyond ``query.get_subcatchment_info``.

The aggregate ``query.get_subcatchment_info`` returns a batched property dict
and ``editing.set_subcatchment_properties`` handles the basic geometry +
roughness + outlet setters. This module adds:

* **Statistics** (3) — precipitation / runoff peaks (via ``stats`` sub-view).
* **Bulk arrays** (2) — runoff + quality across all subcatchments.
* **Current state** (6) — runoff, rainfall, evap, groundwater, snow_depth, infil.
* **Coverage** (3) — land-use coverage get/set via the ``coverage`` mapping,
  plus the bulk ``coverages()`` reader.
* **Infiltration models** (8) — model getter + (Horton / Green-Ampt /
  Curve Number) parameter pairs (via ``infiltration`` sub-view).
* **Quality** (3) — ponded quality get/set + per-subcatchment quality.
* **Initial loading** (2) — ``[LOADINGS]`` initial buildup get/set.
* **Aquifer definitions** (6) — add / id / numeric params / evap pattern
  (via ``session.aquifers``).
* **Snowpack definitions** (9) — add / count / id, the three snow-melt
  surfaces, and the REMOVAL row (via ``session.snowpacks``).
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


# The three ``[SNOWPACKS]`` snow-melt surfaces, in the same 0/1/2 order used
# by ``set_snow_state``.
_SNOW_SURFACES = ("plowable", "impervious", "pervious")

_SNOW_SURFACE_KEYS = ("cmin", "cmax", "tbase", "fwfrac", "sd0", "fw0", "last")

_SNOW_REMOVAL_KEYS = ("dsnow", "fout", "fimp", "fperv", "fimelt", "fsubcatch")


def _resolve_snow_surface(surface: str | int) -> str:
    if isinstance(surface, int):
        if not 0 <= surface < len(_SNOW_SURFACES):
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] surface code must be 0 (plowable), "
                f"1 (impervious) or 2 (pervious); got {surface}."
            )
        return _SNOW_SURFACES[surface]
    key = surface.strip().lower()
    if key not in _SNOW_SURFACES:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown snow surface '{surface}'. "
            f"Valid: plowable, impervious, pervious (or the codes 0, 1, 2)."
        )
    return key


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
        {"id": subs.get_id(i), "index": i, "value": values[i]} for i in range(min(n, len(values)))
    ]


# ===========================================================================
# Identity: tag get/set, bulk ids, outlet-subcatchment routing
# ===========================================================================


@subcatchments_mcp.tool()
async def get_tag(ctx: Context, session_id: str = "default", subcatch_id: str | int = "") -> dict:
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
    v = await asyncio.to_thread(lambda: getattr(session.subcatchments[idx].stats, attr))
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
    # wraps: swmm_subcatch_get_stat_runoff_vol
    return await _read_stat(ctx, session_id, subcatch_id, "runoff_vol", "runoff_vol")


@subcatchments_mcp.tool()
async def stat_max_runoff(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return the peak runoff rate for a subcatchment."""
    # wraps: swmm_subcatch_get_stat_max_runoff
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
    # wraps: swmm_subcatch_get_evap
    return await _read_attr(ctx, session_id, subcatch_id, "evap", "evap")


@subcatchments_mcp.tool()
async def get_groundwater(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current groundwater flow for a subcatchment."""
    # wraps: swmm_subcatch_get_groundwater
    return await _read_attr(ctx, session_id, subcatch_id, "groundwater", "groundwater")


@subcatchments_mcp.tool()
async def get_snow_depth(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current snow depth on a subcatchment."""
    # wraps: swmm_subcatch_get_snow_depth
    return await _read_attr(ctx, session_id, subcatch_id, "snow_depth", "snow_depth")


@subcatchments_mcp.tool()
async def get_infil(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return the current infiltration rate for a subcatchment."""
    # wraps: swmm_subcatch_get_infil
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
async def get_aquifer(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return the aquifer index assigned to a subcatchment (-1 if none)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    aq = await asyncio.to_thread(lambda: session.subcatchments[idx].aquifer)
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "aquifer_index": -1 if aq is None else aq,
    }


@subcatchments_mcp.tool()
async def set_aquifer(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    aquifer: str | int = -1,
) -> dict:
    """Assign (or detach) the aquifer for a subcatchment.

    Parameters
    ----------
    aquifer:
        Aquifer name or index. Pass ``-1`` (or an empty string) to detach
        the aquifer (no groundwater).
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    value: str | int | None = None if aquifer in (-1, "", None) else aquifer

    def _set() -> None:
        session.subcatchments[idx].aquifer = value

    await asyncio.to_thread(_set)
    new_aq = await asyncio.to_thread(lambda: session.subcatchments[idx].aquifer)
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "aquifer_index": -1 if new_aq is None else new_aq,
    }


@subcatchments_mcp.tool()
async def get_gw_node(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return the node index receiving a subcatchment's groundwater (-1 if none)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    nd = await asyncio.to_thread(lambda: session.subcatchments[idx].gw_node)
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "gw_node_index": -1 if nd is None else nd,
    }


@subcatchments_mcp.tool()
async def set_gw_node(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    node: str | int = -1,
) -> dict:
    """Set (or detach) the node receiving a subcatchment's groundwater flow.

    Parameters
    ----------
    node:
        Node name or index. Pass ``-1`` (or an empty string) to detach.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    value: str | int | None = None if node in (-1, "", None) else node

    def _set() -> None:
        session.subcatchments[idx].gw_node = value

    await asyncio.to_thread(_set)
    new_nd = await asyncio.to_thread(lambda: session.subcatchments[idx].gw_node)
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "gw_node_index": -1 if new_nd is None else new_nd,
    }


_GW_PARAM_KEYS = ("surf_elev", "a1", "b1", "a2", "b2", "a3", "tw", "hstar")


@subcatchments_mcp.tool()
async def get_gw_params(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return the ``[GROUNDWATER]`` flow parameters for a subcatchment.

    Keys: ``surf_elev``, ``a1``, ``b1``, ``a2``, ``b2``, ``a3``, ``tw``,
    ``hstar``. The subcatchment must have an aquifer assigned.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    params = await asyncio.to_thread(lambda: session.subcatchments[idx].gw_params)
    out = {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
    }
    out.update({k: float(v) for k, v in zip(_GW_PARAM_KEYS, params)})
    return out


@subcatchments_mcp.tool()
async def set_gw_params(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    surf_elev: float = 0.0,
    a1: float = 0.0,
    b1: float = 0.0,
    a2: float = 0.0,
    b2: float = 0.0,
    a3: float = 0.0,
    tw: float = 0.0,
    hstar: float = 0.0,
) -> dict:
    """Set the ``[GROUNDWATER]`` flow parameters for a subcatchment.

    Token order matches the INP ``[GROUNDWATER]`` section. The subcatchment
    must have an aquifer assigned.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    vals = (
        float(surf_elev),
        float(a1),
        float(b1),
        float(a2),
        float(b2),
        float(a3),
        float(tw),
        float(hstar),
    )

    def _set() -> None:
        session.subcatchments[idx].set_gw_params(*vals)

    await asyncio.to_thread(_set)
    new_params = await asyncio.to_thread(lambda: session.subcatchments[idx].gw_params)
    out = {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
    }
    out.update({k: float(v) for k, v in zip(_GW_PARAM_KEYS, new_params)})
    return out


@subcatchments_mcp.tool()
async def set_infil_model(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    model: int = 0,
) -> dict:
    """Switch the active infiltration model for a subcatchment.

    Parameters
    ----------
    model:
        ``InfilModel`` integer code (e.g. 0=HORTON, per the engine enum).
        Per-model parameter sub-arrays are preserved.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    code = int(model)

    def _set() -> None:
        session.subcatchments[idx].infiltration.model = code

    await asyncio.to_thread(_set)
    new_model = await asyncio.to_thread(lambda: int(session.subcatchments[idx].infiltration.model))
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "model": new_model,
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
    await asyncio.to_thread(lambda: session.subcatchments[idx].set_snow_state(surf, *args))
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
    # wraps: swmm_subcatch_get_coverage
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    cov = await asyncio.to_thread(lambda: session.subcatchments[idx].coverage[landuse_index])
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
    # wraps: swmm_subcatch_set_coverage
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


@subcatchments_mcp.tool()
async def get_coverages(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
) -> dict:
    """Return every land-use coverage for a subcatchment in one call.

    Bulk peer of ``get_coverage``: ``coverages[i]`` is the coverage of
    land-use index ``i``, in PERCENT (0-100) as stored in the INP
    ``[COVERAGES]`` section. Resolve the land-use names with
    ``quality_landuse_id``.
    """
    # wraps: swmm_subcatch_get_coverages
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    values = await asyncio.to_thread(lambda: session.subcatchments[idx].coverages())
    coverages = [float(v) for v in values]
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "count": len(coverages),
        "coverages": coverages,
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
    code = await asyncio.to_thread(lambda: int(session.subcatchments[idx].infiltration.model))
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
    await asyncio.to_thread(lambda: session.subcatchments[idx].infiltration.set_horton(*args))
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
    await asyncio.to_thread(lambda: session.subcatchments[idx].infiltration.set_green_ampt(*args))
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
    """Return the SCS Curve Number and drying time for a subcatchment.

    ``drying_time`` is the third ``[INFILTRATION]`` column -- days for a fully
    saturated soil to dry -- and is what ``set_infil_curve_number`` preserves
    when it is not given one.
    """
    # wraps: swmm_subcatch_get_infil_curve_number
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    infil = session.subcatchments[idx].infiltration
    cn, drying_time = await asyncio.to_thread(
        lambda: (infil.curve_number, infil.curve_number_drying_time)
    )
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "curve_number": float(cn),
        "drying_time": float(drying_time),
    }


@subcatchments_mcp.tool()
async def set_infil_curve_number(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    curve_number: float = 0.0,
    drying_time: float | None = None,
) -> dict:
    """Set the SCS Curve Number for a subcatchment.

    The engine writes both ``[INFILTRATION]`` columns in one call. Leave
    ``drying_time`` unset to keep the subcatchment's current value and change
    only the curve number.
    """
    # wraps: swmm_subcatch_set_infil_curve_number
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    infil = session.subcatchments[idx].infiltration
    cn = float(curve_number)
    dry = None if drying_time is None else float(drying_time)

    def _apply() -> float:
        applied_dry = infil.curve_number_drying_time if dry is None else dry
        infil.set_curve_number(cn, applied_dry)
        return applied_dry

    applied_dry = await asyncio.to_thread(_apply)
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "curve_number": curve_number,
        "drying_time": float(applied_dry),
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
    conc = await asyncio.to_thread(lambda: session.subcatchments[idx].quality(pollutant_index))
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
    value = await asyncio.to_thread(lambda: session.aquifers.get_param(aquifer_id, param_code))
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
    await asyncio.to_thread(lambda: session.aquifers.set_param(aquifer_id, param_code, value_f))
    return {
        "status": "ok",
        "session_id": session_id,
        "aquifer_id": aquifer_id,
        "param": param,
        "param_code": param_code,
        "value": value,
    }


# ===========================================================================
# Zero-depression-storage impervious area (v1: subcatchment.zero_imperv_pct)
# ===========================================================================


@subcatchments_mcp.tool()
async def get_zero_imperv_pct(
    ctx: Context, session_id: str = "default", subcatch_id: str | int = ""
) -> dict:
    """Return the ``[SUBAREAS] PctZero`` value for a subcatchment.

    The percentage (0-100) of the impervious area that has no depression
    storage.
    """
    # wraps: swmm_subcatch_get_zero_imperv_pct
    return await _read_attr(ctx, session_id, subcatch_id, "zero_imperv_pct", "zero_imperv_pct")


@subcatchments_mcp.tool()
async def set_zero_imperv_pct(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    pct: float = 0.0,
) -> dict:
    """Set the ``[SUBAREAS] PctZero`` value for a subcatchment.

    ``pct`` is the percentage (0-100) of the impervious area having no
    depression storage.
    """
    # wraps: swmm_subcatch_set_zero_imperv_pct
    if not 0.0 <= pct <= 100.0:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] pct must be in [0, 100]; got {pct}.")
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    value = float(pct)

    def _set() -> None:
        session.subcatchments[idx].zero_imperv_pct = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "zero_imperv_pct": pct,
    }


# ===========================================================================
# Initial pollutant loading (v1: subcatchment.loadings[pollutant])
# ===========================================================================


@subcatchments_mcp.tool()
async def get_initial_loading(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    pollutant_id: str | int = 0,
) -> dict:
    """Return the ``[LOADINGS]`` initial pollutant buildup on a subcatchment.

    The mass per unit area present at simulation start (0.0 when unset),
    which overrides the DRY_DAYS-derived buildup. ``pollutant_id`` is a
    pollutant name or index.
    """
    # wraps: swmm_subcatch_get_initial_loading
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    mass = await asyncio.to_thread(lambda: session.subcatchments[idx].loadings[pollutant_id])
    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "pollutant_id": pollutant_id,
        "initial_loading": float(mass),
    }


@subcatchments_mcp.tool()
async def set_initial_loading(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    pollutant_id: str | int = 0,
    initial_loading: float = 0.0,
) -> dict:
    """Set the ``[LOADINGS]`` initial pollutant buildup on a subcatchment.

    ``initial_loading`` is the buildup mass per unit area present at
    simulation start. ``pollutant_id`` is a pollutant name or index.
    """
    # wraps: swmm_subcatch_set_initial_loading
    session = await _get_session(ctx, session_id)
    idx = await _resolve_subcatch(session, subcatch_id)
    value = float(initial_loading)

    def _set() -> None:
        session.subcatchments[idx].loadings[pollutant_id] = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": idx,
        "pollutant_id": pollutant_id,
        "initial_loading": initial_loading,
    }


# ===========================================================================
# Aquifer definitions (v1: session.aquifers.add / get_id / evap pattern)
# ===========================================================================


@subcatchments_mcp.tool()
async def aquifer_add(ctx: Context, session_id: str = "default", aquifer_id: str = "") -> dict:
    """Add a new ``[AQUIFERS]`` entry with default parameters.

    Returns the new aquifer's zero-based index. Configure it with
    ``aquifer_set_param`` / ``aquifer_set_evap_pattern``, then attach it to a
    subcatchment with ``set_aquifer``.
    """
    # wraps: swmm_aquifer_add
    if not aquifer_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] aquifer_id must not be empty.")
    session = await _get_session(ctx, session_id)
    idx = await asyncio.to_thread(session.aquifers.add, aquifer_id)
    return {"status": "ok", "session_id": session_id, "id": aquifer_id, "index": idx}


@subcatchments_mcp.tool()
async def aquifer_id(ctx: Context, session_id: str = "default", index: int = 0) -> dict:
    """Return the string id of the ``index``-th aquifer."""
    # wraps: swmm_aquifer_id
    session = await _get_session(ctx, session_id)
    s = await asyncio.to_thread(session.aquifers.get_id, index)
    return {"session_id": session_id, "index": index, "id": s}


@subcatchments_mcp.tool()
async def aquifer_get_evap_pattern(
    ctx: Context, session_id: str = "default", aquifer_id: str | int = ""
) -> dict:
    """Return an aquifer's upper-zone evaporation pattern name (empty if none).

    The trailing ``ETupat`` column of the ``[AQUIFERS]`` line — a MONTHLY
    ``[PATTERNS]`` name scaling the upper-zone evaporation fraction. The 12
    numeric columns are reached via ``aquifer_get_param``.
    """
    # wraps: swmm_aquifer_get_evap_pattern
    session = await _get_session(ctx, session_id)
    name = await asyncio.to_thread(session.aquifers.get_evap_pattern, aquifer_id)
    return {"session_id": session_id, "aquifer_id": aquifer_id, "pattern_id": name}


@subcatchments_mcp.tool()
async def aquifer_set_evap_pattern(
    ctx: Context,
    session_id: str = "default",
    aquifer_id: str | int = "",
    pattern_id: str = "",
) -> dict:
    """Set (or clear) an aquifer's upper-zone evaporation pattern.

    ``pattern_id`` is a MONTHLY ``[PATTERNS]`` name; an empty string clears
    it. Pre-start-only — the engine raises while the simulation is running.
    """
    # wraps: swmm_aquifer_set_evap_pattern
    session = await _get_session(ctx, session_id)
    name = pattern_id
    await asyncio.to_thread(lambda: session.aquifers.set_evap_pattern(aquifer_id, name))
    return {
        "status": "ok",
        "session_id": session_id,
        "aquifer_id": aquifer_id,
        "pattern_id": name,
    }


# ===========================================================================
# Snowpack definitions (v1: session.snowpacks)
#
# Distinct from ``climate_set_snowmelt_config`` / ``climate_set_areal_depletion``
# (the [SNOWMELT] section and ADC curves) and from ``set_snow_state`` (run-time
# state): these author the [SNOWPACKS] definitions themselves.
# ===========================================================================


@subcatchments_mcp.tool()
async def snowpack_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of ``[SNOWPACKS]`` definitions in the model."""
    # wraps: swmm_snowpack_count
    session = await _get_session(ctx, session_id)
    n = await asyncio.to_thread(lambda: len(session.snowpacks))
    return {"session_id": session_id, "count": n}


@subcatchments_mcp.tool()
async def snowpack_id(ctx: Context, session_id: str = "default", index: int = 0) -> dict:
    """Return the string id of the ``index``-th snowpack."""
    # wraps: swmm_snowpack_id
    session = await _get_session(ctx, session_id)
    s = await asyncio.to_thread(session.snowpacks.get_id, index)
    return {"session_id": session_id, "index": index, "id": s}


@subcatchments_mcp.tool()
async def snowpack_add(ctx: Context, session_id: str = "default", snowpack_id: str = "") -> dict:
    """Add a new ``[SNOWPACKS]`` definition with zeroed parameters.

    Returns the new snowpack's zero-based index. Configure the three
    snow-melt surfaces with ``snowpack_set_surface`` and the redistribution
    row with ``snowpack_set_removal``.
    """
    # wraps: swmm_snowpack_add
    if not snowpack_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] snowpack_id must not be empty.")
    session = await _get_session(ctx, session_id)
    idx = await asyncio.to_thread(session.snowpacks.add, snowpack_id)
    return {"status": "ok", "session_id": session_id, "id": snowpack_id, "index": idx}


@subcatchments_mcp.tool()
async def snowpack_get_surface(
    ctx: Context,
    session_id: str = "default",
    snowpack_id: str | int = "",
    surface: str | int = "pervious",
) -> dict:
    """Read one snow-melt surface of a snowpack definition.

    ``surface`` is ``plowable``, ``impervious`` or ``pervious`` (or the codes
    0, 1, 2). Returns the seven ``[SNOWPACKS]`` values — see
    ``snowpack_set_surface`` for their meaning.
    """
    # wraps: swmm_snowpack_get_plowable swmm_snowpack_get_impervious swmm_snowpack_get_pervious
    surf = _resolve_snow_surface(surface)
    session = await _get_session(ctx, session_id)
    params = await asyncio.to_thread(
        lambda: getattr(session.snowpacks, f"get_{surf}")(snowpack_id)
    )
    out = {
        "session_id": session_id,
        "snowpack_id": snowpack_id,
        "surface": surf,
    }
    out.update({k: float(params[k]) for k in _SNOW_SURFACE_KEYS})
    return out


@subcatchments_mcp.tool()
async def snowpack_set_surface(
    ctx: Context,
    session_id: str = "default",
    snowpack_id: str | int = "",
    surface: str | int = "pervious",
    cmin: float = 0.0,
    cmax: float = 0.0,
    tbase: float = 0.0,
    fwfrac: float = 0.0,
    sd0: float = 0.0,
    fw0: float = 0.0,
    last: float = 0.0,
) -> dict:
    """Set one snow-melt surface of a snowpack definition (pre-start-only).

    Parameters
    ----------
    surface:
        ``plowable``, ``impervious`` or ``pervious`` (or the codes 0, 1, 2).
    cmin, cmax:
        Minimum / maximum melt coefficient (in or mm per hr per degree).
    tbase:
        Snow-melt base temperature (deg F or C).
    fwfrac:
        Free-water capacity as a fraction of snow depth.
    sd0:
        Initial snow depth (in or mm water equivalent).
    fw0:
        Initial free water (in or mm).
    last:
        PLOWABLE: the fraction of the impervious area that is plowable.
        IMPERVIOUS / PERVIOUS: the snow depth above which there is 100%
        cover (in or mm).
    """
    # wraps: swmm_snowpack_set_plowable swmm_snowpack_set_impervious swmm_snowpack_set_pervious
    surf = _resolve_snow_surface(surface)
    session = await _get_session(ctx, session_id)
    values = {
        "cmin": float(cmin),
        "cmax": float(cmax),
        "tbase": float(tbase),
        "fwfrac": float(fwfrac),
        "sd0": float(sd0),
        "fw0": float(fw0),
        "last": float(last),
    }
    await asyncio.to_thread(
        lambda: getattr(session.snowpacks, f"set_{surf}")(snowpack_id, **values)
    )
    out = {
        "status": "ok",
        "session_id": session_id,
        "snowpack_id": snowpack_id,
        "surface": surf,
    }
    out.update(values)
    return out


@subcatchments_mcp.tool()
async def snowpack_get_removal(
    ctx: Context, session_id: str = "default", snowpack_id: str | int = ""
) -> dict:
    """Read a snowpack's REMOVAL row (snow redistribution fractions)."""
    # wraps: swmm_snowpack_get_removal
    session = await _get_session(ctx, session_id)
    params = await asyncio.to_thread(session.snowpacks.get_removal, snowpack_id)
    out = {"session_id": session_id, "snowpack_id": snowpack_id}
    out.update({k: float(params[k]) for k in _SNOW_REMOVAL_KEYS})
    return out


@subcatchments_mcp.tool()
async def snowpack_set_removal(
    ctx: Context,
    session_id: str = "default",
    snowpack_id: str | int = "",
    dsnow: float = 0.0,
    fout: float = 0.0,
    fimp: float = 0.0,
    fperv: float = 0.0,
    fimelt: float = 0.0,
    fsubcatch: float = 0.0,
) -> dict:
    """Set a snowpack's REMOVAL row (pre-start-only).

    Parameters
    ----------
    dsnow:
        Snow depth above which removal begins (in or mm).
    fout, fimp, fperv, fimelt, fsubcatch:
        Fractions of the removed snow routed out of the watershed, to the
        impervious area, to the pervious area, converted to immediate melt,
        and transferred to another subcatchment. Name the destination
        subcatchment with ``snowpack_set_removal_subcatch``.
    """
    # wraps: swmm_snowpack_set_removal
    session = await _get_session(ctx, session_id)
    values = {
        "dsnow": float(dsnow),
        "fout": float(fout),
        "fimp": float(fimp),
        "fperv": float(fperv),
        "fimelt": float(fimelt),
        "fsubcatch": float(fsubcatch),
    }
    await asyncio.to_thread(lambda: session.snowpacks.set_removal(snowpack_id, **values))
    out = {"status": "ok", "session_id": session_id, "snowpack_id": snowpack_id}
    out.update(values)
    return out


@subcatchments_mcp.tool()
async def snowpack_get_removal_subcatch(
    ctx: Context, session_id: str = "default", snowpack_id: str | int = ""
) -> dict:
    """Return the destination subcatchment for a snowpack's ``fsubcatch``
    removal fraction (empty if none)."""
    # wraps: swmm_snowpack_get_removal_subcatch
    session = await _get_session(ctx, session_id)
    name = await asyncio.to_thread(session.snowpacks.get_removal_subcatch, snowpack_id)
    return {"session_id": session_id, "snowpack_id": snowpack_id, "subcatch_id": name}


@subcatchments_mcp.tool()
async def snowpack_set_removal_subcatch(
    ctx: Context,
    session_id: str = "default",
    snowpack_id: str | int = "",
    subcatch_id: str = "",
) -> dict:
    """Set (or clear) the destination subcatchment for a snowpack's
    ``fsubcatch`` removal fraction.

    An empty ``subcatch_id`` clears it. Pre-start-only.
    """
    # wraps: swmm_snowpack_set_removal_subcatch
    session = await _get_session(ctx, session_id)
    name = subcatch_id
    await asyncio.to_thread(lambda: session.snowpacks.set_removal_subcatch(snowpack_id, name))
    return {
        "status": "ok",
        "session_id": session_id,
        "snowpack_id": snowpack_id,
        "subcatch_id": name,
    }
