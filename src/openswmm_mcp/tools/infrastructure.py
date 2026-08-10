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
* LID controls: ``lid_count``, ``add_lid``, and per-layer setters/getters for
  all six layers — ``{set,get}_lid_surface``, ``{set,get}_lid_soil``,
  ``{set,get}_lid_storage``, ``{set,get}_lid_drain``,
  ``{set,get}_lid_pavement``, ``{set,get}_lid_drainmat``.
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
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
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
    idx = await resolve_index(subcatchments, subcatch_id, "Subcatchment")
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Subcatchment '{subcatch_id}' not "
            f"found in this session."
        )
    return idx


async def _resolve_lid_idx(lids: Any, lid_index: str | int) -> int:
    """Translate a string LID-control ID to integer index; pass through ints."""
    if isinstance(lid_index, int):
        return lid_index
    if not lid_index:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] lid_index must not be empty.")
    return await resolve_index(lids, lid_index, "LID control")


# ===========================================================================
# [TRANSECTS]
# ===========================================================================


@infrastructure_mcp.tool()
async def transect_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of transects defined in the model."""
    # wraps: swmm_transect_count
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(lambda: len(infra.transects))
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
    idx = await asyncio.to_thread(infra.transects.add, transect_id)
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
        infra.transects.set_roughness,
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
        infra.transects.add_station,
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


@infrastructure_mcp.tool()
async def clear_stations(
    ctx: Context, session_id: str = "default", transect_index: int = 0
) -> dict:
    """Remove all (station, elevation) points from a transect's profile."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(infra.transects.clear_stations, transect_index)
    return {"status": "ok", "session_id": session_id, "transect_index": transect_index}


@infrastructure_mcp.tool()
async def station_count(ctx: Context, session_id: str = "default", transect_index: int = 0) -> dict:
    """Return the number of (station, elevation) points in a transect's profile."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(infra.transects.station_count, transect_index)
    return {"session_id": session_id, "transect_index": transect_index, "count": n}


@infrastructure_mcp.tool()
async def get_station(
    ctx: Context, session_id: str = "default", transect_index: int = 0, station_index: int = 0
) -> dict:
    """Read back a single (station, elevation) point from a transect's profile."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    station, elevation = await asyncio.to_thread(
        infra.transects.get_station, transect_index, station_index
    )
    return {
        "session_id": session_id,
        "transect_index": transect_index,
        "station_index": station_index,
        "station": station,
        "elevation": elevation,
    }


@infrastructure_mcp.tool()
async def get_transect_roughness(
    ctx: Context, session_id: str = "default", transect_index: int = 0
) -> dict:
    """Read back the three Manning's roughness values of a transect.

    Inverse of :func:`set_transect_roughness`. Returns ``n_left`` /
    ``n_right`` (overbank) and ``n_channel`` (main channel).
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    n_left, n_right, n_channel = await asyncio.to_thread(
        infra.transects.get_roughness, transect_index
    )
    return {
        "session_id": session_id,
        "transect_index": transect_index,
        "n_left": n_left,
        "n_right": n_right,
        "n_channel": n_channel,
    }


@infrastructure_mcp.tool()
async def get_bank_stations(
    ctx: Context, session_id: str = "default", transect_index: int = 0
) -> dict:
    """Read back a transect's left/right bank station positions."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    left, right = await asyncio.to_thread(infra.transects.get_bank_stations, transect_index)
    return {
        "session_id": session_id,
        "transect_index": transect_index,
        "left": left,
        "right": right,
    }


@infrastructure_mcp.tool()
async def set_bank_stations(
    ctx: Context,
    session_id: str = "default",
    transect_index: int = 0,
    left: float = 0.0,
    right: float = 0.0,
) -> dict:
    """Set a transect's left/right bank station positions.

    The bank stations delimit the main channel from the overbank zones
    (which use the left/right roughness from :func:`set_transect_roughness`).
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.transects.set_bank_stations, transect_index, float(left), float(right)
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "transect_index": transect_index,
        "left": left,
        "right": right,
    }


@infrastructure_mcp.tool()
async def get_encroachment_stations(
    ctx: Context, session_id: str = "default", transect_index: int = 0
) -> dict:
    """Read back a transect's left/right encroachment station positions."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    left, right = await asyncio.to_thread(infra.transects.get_encroachment_stations, transect_index)
    return {
        "session_id": session_id,
        "transect_index": transect_index,
        "left": left,
        "right": right,
    }


@infrastructure_mcp.tool()
async def set_encroachment_stations(
    ctx: Context,
    session_id: str = "default",
    transect_index: int = 0,
    left: float = 0.0,
    right: float = 0.0,
) -> dict:
    """Set a transect's left/right encroachment station positions."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.transects.set_encroachment_stations, transect_index, float(left), float(right)
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "transect_index": transect_index,
        "left": left,
        "right": right,
    }


@infrastructure_mcp.tool()
async def get_modifiers(ctx: Context, session_id: str = "default", transect_index: int = 0) -> dict:
    """Read back a transect's roughness / station / elevation modifier factors."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    n_factor, x_factor, y_factor = await asyncio.to_thread(
        infra.transects.get_modifiers, transect_index
    )
    return {
        "session_id": session_id,
        "transect_index": transect_index,
        "n_factor": n_factor,
        "x_factor": x_factor,
        "y_factor": y_factor,
    }


@infrastructure_mcp.tool()
async def set_modifiers(
    ctx: Context,
    session_id: str = "default",
    transect_index: int = 0,
    n_factor: float = 0.0,
    x_factor: float = 0.0,
    y_factor: float = 0.0,
) -> dict:
    """Set a transect's modifier factors.

    ``n_factor`` scales roughness, ``x_factor`` scales station distances,
    and ``y_factor`` scales elevations.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(
        infra.transects.set_modifiers,
        transect_index,
        float(n_factor),
        float(x_factor),
        float(y_factor),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "transect_index": transect_index,
        "n_factor": n_factor,
        "x_factor": x_factor,
        "y_factor": y_factor,
    }


@infrastructure_mcp.tool()
async def get_comments(ctx: Context, session_id: str = "default", transect_index: int = 0) -> dict:
    """Read back the comment text attached to a transect."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    text = await asyncio.to_thread(infra.transects.get_comments, transect_index)
    return {"session_id": session_id, "transect_index": transect_index, "text": text}


@infrastructure_mcp.tool()
async def set_comments(
    ctx: Context, session_id: str = "default", transect_index: int = 0, text: str = ""
) -> dict:
    """Set the comment text attached to a transect."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(infra.transects.set_comments, transect_index, text)
    return {
        "status": "ok",
        "session_id": session_id,
        "transect_index": transect_index,
        "text": text,
    }


@infrastructure_mcp.tool()
async def remove_transect(
    ctx: Context, session_id: str = "default", transect: str | int = ""
) -> dict:
    """Remove a transect by string ID or integer index."""
    if isinstance(transect, str) and not transect:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] transect must not be empty.")
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(infra.transects.remove, transect)
    return {"status": "ok", "session_id": session_id, "transect": transect}


# ===========================================================================
# [STREETS]
# ===========================================================================


@infrastructure_mcp.tool()
async def street_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of street cross-sections in the model."""
    # wraps: swmm_street_count
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(lambda: len(infra.streets))
    return {"session_id": session_id, "count": n}


@infrastructure_mcp.tool()
async def add_street(ctx: Context, session_id: str = "default", street_id: str = "") -> dict:
    """Create a new (empty) street cross-section. Returns its index."""
    if not street_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] street_id must not be empty.")
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await asyncio.to_thread(infra.streets.add, street_id)
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
        infra.streets.set_params,
        street_index,
        t_crown=float(t_crown),
        h_curb=float(h_curb),
        sx=float(sx),
        n_road=float(n_road),
        gutter_depres=float(gutter_depres),
        gutter_width=float(gutter_width),
        sides=sides,
        back_width=float(back_width),
        back_slope=float(back_slope),
        back_n=float(back_n),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "street_index": street_index,
    }


@infrastructure_mcp.tool()
async def get_street_params(
    ctx: Context, session_id: str = "default", street_index: int = 0
) -> dict:
    """Read back a street cross-section's geometric parameters.

    Inverse of :func:`set_street_params`. Returns a ``params`` dict with
    keys ``t_crown``, ``h_curb``, ``sx``, ``n_road``, ``gutter_depres``,
    ``gutter_width``, ``sides``, ``back_width``, ``back_slope``, ``back_n``.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    params = await asyncio.to_thread(infra.streets.get_params, street_index)
    return {
        "session_id": session_id,
        "street_index": street_index,
        "params": params,
    }


# ===========================================================================
# [INLETS]
# ===========================================================================


@infrastructure_mcp.tool()
async def inlet_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of inlets defined in the model."""
    # wraps: swmm_inlet_count
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(lambda: len(infra.inlets))
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
    idx = await asyncio.to_thread(infra.inlets.add, inlet_id, inlet_type)
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
        infra.inlets.set_params,
        inlet_index,
        length=float(length),
        width=float(width),
        grate_type=grate_type,
        open_area=float(open_area),
        splash_veloc=float(splash_veloc),
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
    # wraps: swmm_lid_count
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(lambda: len(infra.lids))
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
    idx = await asyncio.to_thread(infra.lids.add, lid_id, type_int)
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
        infra.lids.set_surface,
        lid_index,
        storage=float(storage),
        roughness=float(roughness),
        slope=float(slope),
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
        infra.lids.set_soil,
        lid_index,
        thick=float(thick),
        porosity=float(porosity),
        fc=float(fc),
        wp=float(wp),
        ksat=float(ksat),
        kslope=float(kslope),
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
        infra.lids.set_storage,
        lid_index,
        thick=float(thick),
        void_frac=float(void_frac),
        ksat=float(ksat),
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
        infra.lids.set_drain,
        lid_index,
        coeff=float(coeff),
        expon=float(expon),
        offset=float(offset),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "lid_index": lid_index,
    }


@infrastructure_mcp.tool()
async def set_lid_pavement(
    ctx: Context,
    session_id: str = "default",
    lid_index: str | int = 0,
    thick: float = 0.0,
    void_ratio: float = 0.0,
    frac_imperv: float = 0.0,
    ksat: float = 0.0,
    clog_factor: float = 0.0,
    regen_days: float = 0.0,
) -> dict:
    """Set LID porous-pavement layer parameters (``perm_pavement`` LIDs).

    Parameters
    ----------
    lid_index:
        Target LID control (string ID or integer index).
    thick:
        Pavement layer thickness.
    void_ratio:
        Void volume / solids volume (a *ratio*, not a fraction).
    frac_imperv:
        Impervious surface fraction of the pavement in [0.0, 1.0].
    ksat:
        Saturated hydraulic conductivity of the pavement.
    clog_factor, regen_days:
        Clogging factor and the pavement regeneration interval in days.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await _resolve_lid_idx(infra.lids, lid_index)
    # wraps: swmm_lid_set_pavement
    await asyncio.to_thread(
        infra.lids.set_pavement,
        idx,
        thick=float(thick),
        void_ratio=float(void_ratio),
        frac_imperv=float(frac_imperv),
        ksat=float(ksat),
        clog_factor=float(clog_factor),
        regen_days=float(regen_days),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "lid_index": idx,
    }


@infrastructure_mcp.tool()
async def set_lid_drainmat(
    ctx: Context,
    session_id: str = "default",
    lid_index: str | int = 0,
    thick: float = 0.0,
    void_frac: float = 0.0,
    roughness: float = 0.0,
) -> dict:
    """Set LID drainage-mat layer parameters (``green_roof`` LIDs).

    ``lid_index`` accepts a string LID ID or an integer index. The mat is
    described by its thickness, void fraction, and Manning's roughness.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await _resolve_lid_idx(infra.lids, lid_index)
    # wraps: swmm_lid_set_drainmat
    await asyncio.to_thread(
        infra.lids.set_drainmat,
        idx,
        thick=float(thick),
        void_frac=float(void_frac),
        roughness=float(roughness),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "lid_index": idx,
    }


@infrastructure_mcp.tool()
async def get_lid_surface(
    ctx: Context,
    session_id: str = "default",
    lid_index: str | int = 0,
) -> dict:
    """Read LID surface-layer parameters. Inverse of :func:`set_lid_surface`.

    Returns ``storage``, ``roughness``, ``slope`` — the same keys
    :func:`set_lid_surface` accepts.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await _resolve_lid_idx(infra.lids, lid_index)
    # wraps: swmm_lid_get_surface
    layer = await asyncio.to_thread(infra.lids.get_surface, idx)
    return {"session_id": session_id, "lid_index": idx, **layer}


@infrastructure_mcp.tool()
async def get_lid_soil(
    ctx: Context,
    session_id: str = "default",
    lid_index: str | int = 0,
) -> dict:
    """Read LID soil-layer parameters. Inverse of :func:`set_lid_soil`.

    Returns ``thick``, ``porosity``, ``fc``, ``wp``, ``ksat``, ``kslope``.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await _resolve_lid_idx(infra.lids, lid_index)
    # wraps: swmm_lid_get_soil
    layer = await asyncio.to_thread(infra.lids.get_soil, idx)
    return {"session_id": session_id, "lid_index": idx, **layer}


@infrastructure_mcp.tool()
async def get_lid_storage(
    ctx: Context,
    session_id: str = "default",
    lid_index: str | int = 0,
) -> dict:
    """Read LID storage-layer parameters. Inverse of :func:`set_lid_storage`.

    Returns ``thick``, ``void_frac``, ``ksat``.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await _resolve_lid_idx(infra.lids, lid_index)
    # wraps: swmm_lid_get_storage
    layer = await asyncio.to_thread(infra.lids.get_storage, idx)
    return {"session_id": session_id, "lid_index": idx, **layer}


@infrastructure_mcp.tool()
async def get_lid_drain(
    ctx: Context,
    session_id: str = "default",
    lid_index: str | int = 0,
) -> dict:
    """Read LID underdrain parameters. Inverse of :func:`set_lid_drain`.

    Returns ``coeff``, ``expon``, ``offset``.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await _resolve_lid_idx(infra.lids, lid_index)
    # wraps: swmm_lid_get_drain
    layer = await asyncio.to_thread(infra.lids.get_drain, idx)
    return {"session_id": session_id, "lid_index": idx, **layer}


@infrastructure_mcp.tool()
async def get_lid_pavement(
    ctx: Context,
    session_id: str = "default",
    lid_index: str | int = 0,
) -> dict:
    """Read LID porous-pavement parameters. Inverse of :func:`set_lid_pavement`.

    Returns ``thick``, ``void_ratio``, ``frac_imperv``, ``ksat``,
    ``clog_factor``, ``regen_days``.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await _resolve_lid_idx(infra.lids, lid_index)
    # wraps: swmm_lid_get_pavement
    layer = await asyncio.to_thread(infra.lids.get_pavement, idx)
    return {"session_id": session_id, "lid_index": idx, **layer}


@infrastructure_mcp.tool()
async def get_lid_drainmat(
    ctx: Context,
    session_id: str = "default",
    lid_index: str | int = 0,
) -> dict:
    """Read LID drainage-mat parameters. Inverse of :func:`set_lid_drainmat`.

    Returns ``thick``, ``void_frac``, ``roughness``.
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    idx = await _resolve_lid_idx(infra.lids, lid_index)
    # wraps: swmm_lid_get_drainmat
    layer = await asyncio.to_thread(infra.lids.get_drainmat, idx)
    return {"session_id": session_id, "lid_index": idx, **layer}


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
        infra.lids.usage_add,
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


@infrastructure_mcp.tool()
async def lid_usage_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of ``[LID_USAGE]`` placement rows across all subcatchments."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    n = await asyncio.to_thread(infra.lids.usage_count)
    return {"session_id": session_id, "count": n}


@infrastructure_mcp.tool()
async def lid_usage_get(ctx: Context, session_id: str = "default", usage_index: int = 0) -> dict:
    """Read one ``[LID_USAGE]`` placement row by global index.

    Returns the owning subcatchment/LID indices and the placement
    parameters (``number``, ``area``, ``width``, ``init_sat``,
    ``from_imperv``, ``to_perv``, ``from_perv``).
    """
    _, infra, _ = await _get_accessors(ctx, session_id)
    row = await asyncio.to_thread(infra.lids.usage_get, int(usage_index))
    return {
        "session_id": session_id,
        "usage_index": int(usage_index),
        **row,
    }


@infrastructure_mcp.tool()
async def lid_usage_remove(ctx: Context, session_id: str = "default", usage_index: int = 0) -> dict:
    """Remove one ``[LID_USAGE]`` placement row by global index."""
    _, infra, _ = await _get_accessors(ctx, session_id)
    await asyncio.to_thread(infra.lids.usage_remove, int(usage_index))
    n = await asyncio.to_thread(infra.lids.usage_count)
    return {
        "status": "ok",
        "session_id": session_id,
        "usage_index": int(usage_index),
        "count": n,
    }
