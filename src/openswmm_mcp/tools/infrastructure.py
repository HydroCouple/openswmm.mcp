"""Infrastructure tools: transects, streets, inlets, LIDs, and LID usage.

Wraps :class:`openswmm.engine.Infrastructure` for design-time configuration
of the ``[TRANSECTS]``, ``[STREETS]``, ``[INLETS]``, ``[LID_CONTROLS]``, and
``[LID_USAGE]`` INP sections.

Previously the MCP server only exposed ``spatial_quality.add_lid`` (which is
actually a ``lid_usage_add`` — placing an LID instance on a subcatchment).
The full Infrastructure surface — defining LIDs, transects, streets, and
inlets, plus per-layer LID parameter setters — was unreachable. This module
closes that gap.

Module coverage (17 of 17 Python ``Infrastructure`` runtime methods):

* Transects: ``transect_count``, ``add_transect``, ``set_transect_roughness``,
  ``add_transect_station``.
* Streets: ``street_count``, ``add_street``, ``set_street_params``.
* Inlets: ``inlet_count``, ``add_inlet``, ``set_inlet_params``.
* LID controls: ``lid_count``, ``add_lid``, ``set_lid_surface``,
  ``set_lid_soil``, ``set_lid_storage``, ``set_lid_drain``.
* LID usage: ``add_lid_usage`` (accepts subcatch_id string).

State handling: the helper accepts any non-closed state. For ``building``
sessions it constructs ``Infrastructure(ModelBuilder)`` plus a
``Subcatchments`` accessor for ID resolution on ``add_lid_usage``;
otherwise the cached backend accessors are returned.

Coexistence with ``spatial_quality.add_lid``: that tool is left in place
and continues to wrap ``lid_usage_add`` with positional subcatch lookup.
The new ``infrastructure.add_lid`` is the LID-control definition tool
(distinct semantic — defines what an LID is, not where it's placed).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP
from openswmm.engine import Infrastructure, Subcatchments

from openswmm_mcp.dependencies import get_session_manager, require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

infrastructure_mcp = FastMCP("infrastructure")


# ---------------------------------------------------------------------------
# Enums (string -> engine integer code)
# ---------------------------------------------------------------------------


# Matches openswmm.engine._enums.LidType.
_LID_TYPES: dict[str, int] = {
    "bio_cell": 0,
    "rain_garden": 1,
    "green_roof": 2,
    "infil_trench": 3,
    "perm_pavement": 4,
    "rain_barrel": 5,
    "rooftop_disconn": 6,
    "vegetative_swale": 7,
}


def _resolve_lid_type(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower().replace("-", "_")
    if key not in _LID_TYPES:
        valid = ", ".join(sorted(_LID_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown lid_type '{name}'. Valid types: {valid}."
        )
    return _LID_TYPES[key]


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(
        session,
        "Infrastructure (transects / streets / inlets / LIDs)",
    )
    return session


async def _get_accessors(ctx: Context, session_id: str) -> tuple[SimSession, Any, Any]:
    """Return ``(session, infrastructure, subcatchments)`` for any non-closed state.

    ``subcatchments`` is supplied so :func:`add_lid_usage` can resolve a
    string subcatchment ID before calling the engine (which takes ``int``).
    """
    session = await _get_session(ctx, session_id)
    state = session.state
    if state == "closed":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is closed; "
            f"open or initialize it before calling Infrastructure tools."
        )

    if state == "building":
        builder = session.model_builder
        if builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in "
                f"'building' state but has no ModelBuilder attached."
            )
        return session, Infrastructure(builder), Subcatchments(builder)

    return session, session.infrastructure, session.subcatchments


async def _resolve_subcatch_idx(subcatchments: Any, subcatch_id: str | int) -> int:
    """Translate a string subcatchment ID to integer index; pass through ints."""
    if isinstance(subcatch_id, int):
        return subcatch_id
    if not subcatch_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] subcatch_id must not be empty.")
    idx = await asyncio.to_thread(subcatchments.get_index, subcatch_id)
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{subcatch_id}' not "
            f"found in this session."
        )
    return idx


# ===========================================================================
# [TRANSECTS]
# ===========================================================================


@infrastructure_mcp.tool()
async def transect_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of transects defined in the model."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(infra.transect_count)
    return {"session_id": session_id, "count": n}


@infrastructure_mcp.tool()
async def add_transect(ctx: Context, session_id: str = "default", transect_id: str = "") -> dict:
    """Create a new (empty) transect. Returns the assigned zero-based index.

    Populate the transect with :func:`set_transect_roughness` and
    :func:`add_transect_station` calls.
    """
    if not transect_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] transect_id must not be empty.")
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await asyncio.to_thread(infra.transect_add, transect_id)
    return {"status": "ok", "session_id": session_id, "id": transect_id, "index": idx}


@infrastructure_mcp.tool()
async def set_transect_roughness(
    ctx: Context,
    session_id: str = "default",
    transect_index: int = 0,
    n_left: float = 0.0,
    n_right: float = 0.0,
    n_channel: float = 0.0,
) -> dict:
    """Set Manning's roughness values for the three transect zones.

    ``n_left`` and ``n_right`` are the overbank roughness; ``n_channel``
    is the main channel.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.transect_set_roughness,
        transect_index,
        float(n_left),
        float(n_right),
        float(n_channel),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "transect_index": transect_index,
        "n_left": n_left,
        "n_right": n_right,
        "n_channel": n_channel,
    }


@infrastructure_mcp.tool()
async def add_transect_station(
    ctx: Context,
    session_id: str = "default",
    transect_index: int = 0,
    station: float = 0.0,
    elevation: float = 0.0,
) -> dict:
    """Append a single (station, elevation) point to a transect's profile."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.transect_add_station,
        transect_index,
        float(station),
        float(elevation),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "transect_index": transect_index,
        "station": station,
        "elevation": elevation,
    }


# ===========================================================================
# [STREETS]
# ===========================================================================


@infrastructure_mcp.tool()
async def street_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of street cross-sections in the model."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(infra.street_count)
    return {"session_id": session_id, "count": n}


@infrastructure_mcp.tool()
async def add_street(ctx: Context, session_id: str = "default", street_id: str = "") -> dict:
    """Create a new (empty) street cross-section. Returns its index."""
    if not street_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] street_id must not be empty.")
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await asyncio.to_thread(infra.street_add, street_id)
    return {"status": "ok", "session_id": session_id, "id": street_id, "index": idx}


@infrastructure_mcp.tool()
async def set_street_params(
    ctx: Context,
    session_id: str = "default",
    street_index: int = 0,
    t_crown: float = 0.0,
    h_curb: float = 0.0,
    sx: float = 0.0,
    n_road: float = 0.0,
    gutter_depres: float = 0.0,
    gutter_width: float = 0.0,
    sides: int = 1,
    back_width: float = 0.0,
    back_slope: float = 0.0,
    back_n: float = 0.0,
) -> dict:
    """Set the full geometry of a street cross-section.

    Parameters
    ----------
    t_crown:
        Crown (road centerline) elevation width.
    h_curb:
        Curb height.
    sx:
        Road cross slope (rise/run).
    n_road:
        Manning's M{n} for the road surface.
    gutter_depres, gutter_width:
        Gutter depression depth and width.
    sides:
        ``1`` = single-sided (one curb), ``2`` = double-sided.
    back_width, back_slope, back_n:
        Backing (behind-curb) geometry — width, slope, and Manning's M{n}.
    """
    if sides not in (1, 2):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] sides must be 1 or 2; got {sides}.")
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.street_set_params,
        street_index,
        float(t_crown),
        float(h_curb),
        float(sx),
        float(n_road),
        float(gutter_depres),
        float(gutter_width),
        sides,
        float(back_width),
        float(back_slope),
        float(back_n),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "street_index": street_index,
    }


# ===========================================================================
# [INLETS]
# ===========================================================================


@infrastructure_mcp.tool()
async def inlet_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of inlets defined in the model."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(infra.inlet_count)
    return {"session_id": session_id, "count": n}


@infrastructure_mcp.tool()
async def add_inlet(
    ctx: Context,
    session_id: str = "default",
    inlet_id: str = "",
    inlet_type: str = "",
) -> dict:
    """Create a new inlet. Returns the assigned index.

    ``inlet_type`` is a string identifying the inlet geometry family
    (e.g. ``"GRATE"``, ``"CURB"``, ``"SLOTTED"``, ``"CUSTOM"`` —
    consult the engine docs for the exact set).
    """
    if not inlet_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] inlet_id must not be empty.")
    if not inlet_type:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] inlet_type must not be empty.")
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await asyncio.to_thread(infra.inlet_add, inlet_id, inlet_type)
    return {
        "status": "ok",
        "session_id": session_id,
        "id": inlet_id,
        "inlet_type": inlet_type,
        "index": idx,
    }


@infrastructure_mcp.tool()
async def set_inlet_params(
    ctx: Context,
    session_id: str = "default",
    inlet_index: int = 0,
    length: float = 0.0,
    width: float = 0.0,
    grate_type: str = "",
    open_area: float = 0.0,
    splash_veloc: float = 0.0,
) -> dict:
    """Set the operational parameters for an inlet.

    ``grate_type`` identifies a grate-style family (e.g. ``"P_BAR-50"``);
    consult the engine for the available identifiers. ``open_area`` is
    the open-area fraction; ``splash_veloc`` is the splash-over velocity.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.inlet_set_params,
        inlet_index,
        float(length),
        float(width),
        grate_type,
        float(open_area),
        float(splash_veloc),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "inlet_index": inlet_index,
        "length": length,
        "width": width,
        "grate_type": grate_type,
    }


# ===========================================================================
# [LID_CONTROLS]
# ===========================================================================


@infrastructure_mcp.tool()
async def lid_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of LID controls defined in the model."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(infra.lid_count)
    return {"session_id": session_id, "count": n}


@infrastructure_mcp.tool()
async def add_lid(
    ctx: Context,
    session_id: str = "default",
    lid_id: str = "",
    lid_type: str = "bio_cell",
) -> dict:
    """Define a new LID control (not a usage).

    ``lid_type`` accepts a string (``bio_cell``, ``rain_garden``,
    ``green_roof``, ``infil_trench``, ``perm_pavement``, ``rain_barrel``,
    ``rooftop_disconn``, ``vegetative_swale``) or the integer code (0..7).

    Distinct from ``spatial_quality.add_lid`` which is actually
    ``lid_usage_add`` — placing an LID instance on a subcatchment.
    This tool *defines* what an LID is; the layer setters
    (:func:`set_lid_surface`, :func:`set_lid_soil`, :func:`set_lid_storage`,
    :func:`set_lid_drain`) configure its hydraulic behaviour. Then use
    :func:`add_lid_usage` to attach instances to subcatchments.
    """
    if not lid_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] lid_id must not be empty.")
    type_int = _resolve_lid_type(lid_type)
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await asyncio.to_thread(infra.lid_add, lid_id, type_int)
    return {
        "status": "ok",
        "session_id": session_id,
        "id": lid_id,
        "lid_type": lid_type,
        "index": idx,
    }


@infrastructure_mcp.tool()
async def set_lid_surface(
    ctx: Context,
    session_id: str = "default",
    lid_index: int = 0,
    storage: float = 0.0,
    roughness: float = 0.0,
    slope: float = 0.0,
) -> dict:
    """Set LID surface-layer parameters: storage depth, roughness, slope."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.lid_set_surface,
        lid_index,
        float(storage),
        float(roughness),
        float(slope),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "lid_index": lid_index,
        "storage": storage,
        "roughness": roughness,
        "slope": slope,
    }


@infrastructure_mcp.tool()
async def set_lid_soil(
    ctx: Context,
    session_id: str = "default",
    lid_index: int = 0,
    thick: float = 0.0,
    porosity: float = 0.0,
    fc: float = 0.0,
    wp: float = 0.0,
    ksat: float = 0.0,
    kslope: float = 0.0,
) -> dict:
    """Set LID soil-layer parameters.

    Parameters
    ----------
    thick:
        Soil thickness.
    porosity, fc, wp:
        Porosity, field capacity, and wilting point (all volume fractions).
    ksat:
        Saturated hydraulic conductivity.
    kslope:
        Conductivity slope (Green-Ampt parameter).
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.lid_set_soil,
        lid_index,
        float(thick),
        float(porosity),
        float(fc),
        float(wp),
        float(ksat),
        float(kslope),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "lid_index": lid_index,
    }


@infrastructure_mcp.tool()
async def set_lid_storage(
    ctx: Context,
    session_id: str = "default",
    lid_index: int = 0,
    thick: float = 0.0,
    void_frac: float = 0.0,
    ksat: float = 0.0,
) -> dict:
    """Set LID storage-layer parameters: thickness, void fraction, k_sat."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.lid_set_storage,
        lid_index,
        float(thick),
        float(void_frac),
        float(ksat),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "lid_index": lid_index,
    }


@infrastructure_mcp.tool()
async def set_lid_drain(
    ctx: Context,
    session_id: str = "default",
    lid_index: int = 0,
    coeff: float = 0.0,
    expon: float = 0.0,
    offset: float = 0.0,
) -> dict:
    """Set LID underdrain parameters: discharge coefficient, exponent, offset.

    Drain flow follows Q = coeff * h^expon for head above offset.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.lid_set_drain,
        lid_index,
        float(coeff),
        float(expon),
        float(offset),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "lid_index": lid_index,
    }


# ===========================================================================
# [LID_USAGE]
# ===========================================================================


@infrastructure_mcp.tool()
async def add_lid_usage(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str | int = "",
    lid_index: int = 0,
    number: int = 1,
    area: float = 0.0,
    width: float = 0.0,
    init_sat: float = 0.0,
    from_imperv: float = 1.0,
) -> dict:
    """Attach ``number`` instances of an LID control to a subcatchment.

    Parameters
    ----------
    subcatch_id:
        Target subcatchment (string ID or integer index).
    lid_index:
        Index of the LID control to deploy (from :func:`add_lid`).
    number:
        Count of identical units to place.
    area, width:
        Surface area and top width of each unit.
    init_sat:
        Initial saturation fraction in [0.0, 1.0].
    from_imperv:
        Fraction of the subcatchment's impervious area routed to the LID
        (also [0.0, 1.0]).

    Validation: area must be positive; ``init_sat`` and ``from_imperv``
    must lie in [0.0, 1.0]; ``number`` must be >= 1.
    """
    if area <= 0.0:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] area must be positive; got {area}.")
    if not 0.0 <= init_sat <= 1.0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] init_sat must be in [0, 1]; got {init_sat}."
        )
    if not 0.0 <= from_imperv <= 1.0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] from_imperv must be in [0, 1]; got {from_imperv}."
        )
    if number < 1:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] number must be >= 1; got {number}.")

    _, infra, subcatchments = await _get_accessors(ctx, session_id)
    sc_idx = await _resolve_subcatch_idx(subcatchments, subcatch_id)
    await asyncio.to_thread(
        infra.lid_usage_add,
        sc_idx,
        lid_index,
        number,
        float(area),
        float(width),
        float(init_sat),
        float(from_imperv),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "subcatch_index": sc_idx,
        "lid_index": lid_index,
        "number": number,
        "area": area,
    }
