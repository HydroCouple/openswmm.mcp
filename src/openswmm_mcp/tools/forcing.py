"""Forcing and control tools for the OpenSWMM MCP server.

Provides tools for applying runtime forcing overrides (rainfall, inflows,
boundary conditions, etc.) and manipulating control rules / link settings
during a running simulation.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import get_session_manager, require_new_engine, require_state
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
from openswmm_mcp.models import ForcingResult

logger = logging.getLogger(__name__)

forcing_mcp = FastMCP("forcing")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Valid (target_type, variable) -> Forcing method name (v1 names).
# Legacy backend's _LegacyForcing exposes both v0 (subcatch_*) and v1
# (subcatchment_*) names via alias so this mapping works on either.
_FORCING_DISPATCH: dict[tuple[str, str], str] = {
    ("node", "lateral_inflow"): "node_lat_inflow",
    ("node", "head"): "node_head_boundary",
    ("node", "quality"): "node_quality",
    ("link", "flow"): "link_flow",
    ("link", "setting"): "link_setting",
    ("subcatchment", "rainfall"): "subcatchment_rainfall",
    ("subcatchment", "evap"): "subcatchment_evap",
    ("subcatchment", "snowfall"): "subcatchment_snowfall",
    ("gage", "rainfall"): "gage_rainfall",
}

_VALID_VARIABLES_BY_TYPE: dict[str, list[str]] = {
    "node": ["lateral_inflow", "head", "quality"],
    "link": ["flow", "setting"],
    "subcatchment": ["rainfall", "evap", "snowfall"],
    "gage": ["rainfall"],
}


def _resolve_forcing_mode(mode: str) -> int:
    """Map a human-readable mode string to the ForcingMode enum value.

    Mirrors ``openswmm.engine.ForcingMode`` (and the C ``SWMM_ForcingMode``):
    ``REPLACE = 1``, ``ADD = 2`` — code ``0`` is the engine-internal
    "no forcing" state and must never be sent. Returned as a plain int so
    it cleanly accepts both the v1 ``ForcingMode`` IntEnum and the legacy
    adapter's int param (the legacy adapter ignores ``mode`` entirely).
    """
    mapping = {"replace": 1, "add": 2}
    key = mode.strip().lower()
    if key not in mapping:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid forcing mode '{mode}'. "
            f"Must be one of: {', '.join(mapping)}"
        )
    return mapping[key]


# v1 ForcingTarget enum codes — used by ``Forcing.clear(target, key)``.
_FORCING_TARGET_CODES: dict[str, int] = {
    "node": 0,  # ForcingTarget.NODE
    "link": 1,  # ForcingTarget.LINK
    "subcatchment": 2,  # ForcingTarget.SUBCATCH
    "gage": 3,  # ForcingTarget.GAGE
}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@forcing_mcp.tool()
async def set_forcing(
    ctx: Context,
    session_id: str = "default",
    target_type: str = "",
    element_id: str = "",
    variable: str = "",
    value: float = 0.0,
    mode: str = "replace",
    persist: bool = False,
) -> ForcingResult:
    """Apply a runtime forcing override to a model element.

    Overrides the value of a specific variable on a node, link, subcatchment,
    or rain gage for the current (and optionally future) timesteps.

    Parameters
    ----------
    session_id:
        Target simulation session.
    target_type:
        Element category: ``"node"``, ``"link"``, ``"subcatchment"``, or ``"gage"``.
    element_id:
        The name or index of the element to force.
    variable:
        The variable to override.  Valid choices depend on *target_type*:
        node (``"lateral_inflow"``, ``"head"``, ``"quality"``),
        link (``"flow"``, ``"setting"``),
        subcatchment (``"rainfall"``, ``"evap"``, ``"snowfall"``),
        gage (``"rainfall"``).
    value:
        The forcing value to apply.
    mode:
        ``"replace"`` (default) overwrites the computed value;
        ``"add"`` adds to it.
    persist:
        If ``True`` the override persists across timesteps; otherwise it
        resets after each step.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running")

    # Provenance for the C↔Python↔MCP parity matrix (build_matrix_provenance.py):
    # this dispatcher aggregates the per-element forcing C entry points.
    # wraps: swmm_forcing_node_lat_inflow swmm_forcing_node_head_boundary swmm_forcing_node_quality swmm_forcing_link_flow swmm_forcing_link_setting swmm_forcing_subcatch_rainfall swmm_forcing_subcatch_evap swmm_forcing_subcatch_snowfall swmm_forcing_gage_rainfall  # noqa: E501

    # Validate target_type
    target_lower = target_type.strip().lower()
    if target_lower not in _VALID_VARIABLES_BY_TYPE:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid target_type '{target_type}'. "
            f"Must be one of: {', '.join(_VALID_VARIABLES_BY_TYPE)}"
        )

    # Validate variable
    var_lower = variable.strip().lower()
    valid_vars = _VALID_VARIABLES_BY_TYPE[target_lower]
    if var_lower not in valid_vars:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid variable '{variable}' "
            f"for target_type '{target_type}'. "
            f"Must be one of: {', '.join(valid_vars)}"
        )

    dispatch_key = (target_lower, var_lower)
    method_name = _FORCING_DISPATCH[dispatch_key]

    forcing_mode = _resolve_forcing_mode(mode)
    persist_flag = bool(persist)

    # Forcing methods accept either int index or str id in v1; resolve to
    # int for consistency with legacy adapter which expects int.
    _accessor_map = {
        "node": "nodes",
        "link": "links",
        "subcatchment": "subcatchments",
        "gage": "gages",
    }
    accessor = getattr(session, _accessor_map[target_lower])
    # get_index raises ElementNotFoundError (KeyError subclass), never -1.
    element_idx = await resolve_index(accessor, element_id, target_lower.capitalize())

    forcing = session.forcing
    value_f = float(value)

    try:
        method = getattr(forcing, method_name)
        # v1 Forcing methods use keyword-only ``mode`` and ``persist``.
        if var_lower == "quality":
            # node_quality(node, pollutant, mass_rate, *, mode=..., persist=...)
            # — defaults pollutant index to 0.
            await asyncio.to_thread(
                lambda: method(element_idx, 0, value_f, mode=forcing_mode, persist=persist_flag)
            )
        else:
            await asyncio.to_thread(
                lambda: method(element_idx, value_f, mode=forcing_mode, persist=persist_flag)
            )
    except NotImplementedError as exc:
        raise ToolError(
            f"[{ErrorCode.NOT_SUPPORTED}] Forcing variable '{var_lower}' on "
            f"target '{target_lower}' is not supported by the legacy engine. "
            f"Open the session with engine='openswmm' to use this forcing."
        ) from exc
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to set forcing: {exc}") from exc

    return ForcingResult(
        status="applied",
        target_type=target_lower,
        element_id=element_id,
        variable=var_lower,
        value=value,
        mode=mode.strip().lower(),
        persist=persist,
    )


@forcing_mcp.tool()
async def set_link_quality(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    pollutant: str = "",
    value: float = 0.0,
    mode: str = "replace",
    persist: bool = False,
) -> dict:
    """Force a pollutant concentration on a link (RUNNING state only).

    Overrides the in-link concentration of a single pollutant for the
    current (and, with ``persist=True``, future) timesteps. The
    element-keyed :func:`set_forcing` covers node quality but not link
    quality, so this is the dedicated link-quality forcing tool.

    Link quality forcing is a v1-only capability and requires the
    ``openswmm`` backend.

    Parameters
    ----------
    session_id:
        Target simulation session.
    link_id:
        The name or index of the link.
    pollutant:
        The name or index of the pollutant.
    value:
        The concentration to apply (model concentration units).
    mode:
        ``"replace"`` (default) overwrites the computed value;
        ``"add"`` adds to it.
    persist:
        If ``True`` the override persists across timesteps; otherwise it
        resets after each step.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Link quality forcing")
    require_state(session, "running")

    forcing_mode = _resolve_forcing_mode(mode)
    persist_flag = bool(persist)
    value_f = float(value)

    # ``get_index`` raises ElementNotFoundError (a KeyError subclass) for an
    # unknown id; ``resolve_index`` translates that into a clean ToolError
    # (it never returns a -1 sentinel).
    link_idx = await resolve_index(session.links, link_id, "Link")
    pollut_idx = await resolve_index(session.pollutants, pollutant, "Pollutant")

    forcing = session.forcing

    # v1: Forcing.link_quality(link, pollutant, value, *, mode=..., persist=...).
    try:
        await asyncio.to_thread(
            lambda: forcing.link_quality(
                link_idx, pollut_idx, value_f, mode=forcing_mode, persist=persist_flag
            )
        )
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to set link quality forcing: {exc}"
        ) from exc

    return {
        "status": "applied",
        "session_id": session_id,
        "link_id": link_id,
        "pollutant": pollutant,
        "value": value,
        "mode": mode.strip().lower(),
        "persist": persist,
    }


@forcing_mcp.tool()
async def get_climate_evap_rate(ctx: Context, session_id: str = "default") -> dict:
    """Return the current climate-derived evaporation rate (read-only).

    Reports the broadcast potential-evapotranspiration rate the engine would
    apply in the absence of any PET forcing, including monthly adjustments,
    in user units (in/day for US projects, mm/day for SI). Intended for
    caller-side composition: read this rate, apply your own adjustment
    logic, and prescribe the result via ``forcing_set_forcing`` with
    ``target_type="subcatchment"`` and ``variable="evap"``.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Climate evaporation-rate read-back")
    require_state(session, "running")

    forcing = session.forcing
    rate = await asyncio.to_thread(forcing.climate_evap_rate)
    return {
        "session_id": session_id,
        "evap_rate": rate,
        "units": "in/day (US) or mm/day (SI)",
    }


# Model-global climate forcing: (variable -> (setter, getter or None)).
# These are not element-keyed, so they get a dedicated tool rather than
# riding set_forcing's (target_type, element_id) dispatch.
_CLIMATE_SETTERS: dict[str, str] = {
    "temperature": "climate_temperature",
    "wind": "climate_wind",
    "evap": "climate_evap",
}


@forcing_mcp.tool()
async def set_climate_forcing(
    ctx: Context,
    session_id: str = "default",
    variable: str = "",
    value: float = 0.0,
    mode: str = "replace",
    persist: bool = False,
) -> dict:
    """Apply a model-global climate forcing override.

    Overrides a climate input that applies to the whole model (not a single
    element): air temperature, wind speed, or potential evaporation. These
    feed snowmelt, evaporation, and other climate-driven processes from the
    next step on.

    Climate forcing is a v1-only capability and requires the ``openswmm``
    backend.

    Parameters
    ----------
    session_id:
        Target simulation session.
    variable:
        ``"temperature"`` (air temperature, project units),
        ``"wind"`` (wind speed, project units), or
        ``"evap"`` (potential evaporation rate, in/day US or mm/day SI).
    value:
        The forcing value to apply.
    mode:
        ``"replace"`` (default) overwrites the climate-derived value;
        ``"add"`` adds to it.
    persist:
        If ``True`` the override persists across timesteps; otherwise it
        resets after each step.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Climate forcing")
    require_state(session, "running")

    # Provenance for the parity matrix (build_matrix_provenance.py):
    # wraps: swmm_forcing_climate_temperature swmm_forcing_climate_wind swmm_forcing_climate_evap

    var_lower = variable.strip().lower()
    method_name = _CLIMATE_SETTERS.get(var_lower)
    if method_name is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid climate variable '{variable}'. "
            f"Must be one of: {', '.join(_CLIMATE_SETTERS)}"
        )

    forcing_mode = _resolve_forcing_mode(mode)
    persist_flag = bool(persist)
    value_f = float(value)

    try:
        method = getattr(session.forcing, method_name)
        await asyncio.to_thread(lambda: method(value_f, mode=forcing_mode, persist=persist_flag))
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to set climate forcing: {exc}") from exc

    return {
        "status": "applied",
        "session_id": session_id,
        "variable": var_lower,
        "value": value,
        "mode": mode.strip().lower(),
        "persist": persist,
    }


@forcing_mcp.tool()
async def set_climate_dry_only(
    ctx: Context,
    session_id: str = "default",
    flag: bool = True,
) -> dict:
    """Toggle the climate "evaporate only during dry weather" rule.

    When enabled, evaporation is suppressed during rainfall periods. Requires
    the ``openswmm`` backend; takes effect on the next step.

    Parameters
    ----------
    session_id:
        Target simulation session.
    flag:
        ``True`` suppresses evaporation during rainfall; ``False`` allows it.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Climate dry-only toggle")
    require_state(session, "running")

    flag_b = bool(flag)
    try:
        forcing = session.forcing
        await asyncio.to_thread(lambda: forcing.climate_dry_only(flag_b))
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to set dry-only flag: {exc}") from exc

    return {"status": "applied", "session_id": session_id, "dry_only": flag_b}


@forcing_mcp.tool()
async def get_climate_state(ctx: Context, session_id: str = "default") -> dict:
    """Read back the current climate inputs (read-only).

    Returns the air temperature, wind speed, dry-only flag, and the
    climate-derived evaporation rate the engine is currently using (after any
    forcing). Requires the ``openswmm`` backend.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Climate state read-back")
    require_state(session, "running")

    forcing = session.forcing
    temp = await asyncio.to_thread(forcing.get_climate_temperature)
    wind = await asyncio.to_thread(forcing.get_climate_wind_speed)
    dry_only = await asyncio.to_thread(forcing.get_climate_dry_only)
    evap_rate = await asyncio.to_thread(forcing.climate_evap_rate)
    return {
        "session_id": session_id,
        "temperature": temp,
        "wind_speed": wind,
        "dry_only": bool(dry_only),
        "evap_rate": evap_rate,
        "units": "temperature/wind in project units; evap_rate in/day (US) or mm/day (SI)",
    }


@forcing_mcp.tool()
async def clear_forcing(
    ctx: Context,
    session_id: str = "default",
    target_type: str | None = None,
    element_id: str | None = None,
) -> dict:
    """Clear forcing overrides.

    If both *target_type* and *element_id* are ``None`` (the default), all
    forcing overrides across the entire model are removed.  Otherwise, only
    the forcing on the specified element is cleared.

    Parameters
    ----------
    session_id:
        Target simulation session.
    target_type:
        Element category (``"node"``, ``"link"``, ``"subcatchment"``, ``"gage"``).
        Required when clearing a single element.
    element_id:
        The name or index of the element whose forcing should be removed.
        Required when clearing a single element.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running")

    forcing = session.forcing

    try:
        if target_type is None and element_id is None:
            await asyncio.to_thread(forcing.clear_all)
            return {
                "status": "cleared",
                "session_id": session_id,
                "scope": "all",
            }
        if target_type is None or element_id is None:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Both 'target_type' and "
                "'element_id' must be provided to clear a specific element, "
                "or omit both to clear all forcing."
            )
        if session.engine_kind != "openswmm":
            raise ToolError(
                f"[{ErrorCode.NOT_SUPPORTED}] Per-element forcing clear is not "
                f"supported by the legacy engine; values reset automatically "
                f"on each timestep. Use clear_forcing() with no arguments to "
                f"clear all overrides at once."
            )
        # v1 ``Forcing.clear(target, key)`` takes a ForcingTarget enum code
        # (NODE / LINK / SUBCATCH / GAGE) and an element key.
        _accessor_map = {
            "node": "nodes",
            "link": "links",
            "subcatchment": "subcatchments",
            "gage": "gages",
        }
        type_lower = target_type.strip().lower()
        type_code = _FORCING_TARGET_CODES.get(type_lower)
        if type_code is None:
            raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Unknown target_type '{target_type}'.")
        accessor = getattr(session, _accessor_map[type_lower])
        element_idx = await resolve_index(accessor, element_id, type_lower.capitalize())
        await asyncio.to_thread(forcing.clear, type_code, element_idx)
        return {
            "status": "cleared",
            "session_id": session_id,
            "scope": "element",
            "target_type": target_type,
            "element_id": element_id,
        }
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to clear forcing: {exc}") from exc


@forcing_mcp.tool()
async def set_link_control(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    setting: float = 0.0,
) -> dict:
    """Set the control setting on a link.

    Directly overrides a link's control setting (e.g. pump speed, orifice
    opening fraction) for the current timestep.

    Parameters
    ----------
    session_id:
        Target simulation session.
    link_id:
        The name or index of the link.
    setting:
        The new control setting value.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running")
    require_new_engine(session, "Link control rules")

    link_idx = await resolve_index(session.links, link_id, "Link")

    controls = session.controls

    try:
        await asyncio.to_thread(controls.set_link_setting, link_idx, setting)
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to set link control: {exc}") from exc

    return {
        "status": "applied",
        "session_id": session_id,
        "link_id": link_id,
        "setting": setting,
    }


@forcing_mcp.tool()
async def add_control_rule(
    ctx: Context,
    session_id: str = "default",
    rule_text: str = "",
) -> dict:
    """Add a new control rule to the running simulation.

    The rule is specified in SWMM rule syntax and takes effect immediately.

    Parameters
    ----------
    session_id:
        Target simulation session.
    rule_text:
        The control rule in SWMM rule syntax (e.g.
        ``"RULE R1\\nIF NODE J1 DEPTH > 5\\nTHEN PUMP P1 STATUS = ON"``).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running")
    require_new_engine(session, "Adding control rules at runtime")

    controls = session.controls

    def _append() -> int:
        # v1 Controls is a MutableSequence — use append for new rules.
        controls.append(rule_text)
        return len(controls)

    try:
        rule_count = await asyncio.to_thread(_append)
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to add control rule: {exc}") from exc

    return {
        "status": "added",
        "session_id": session_id,
        "rule_index": rule_count,  # 1-based index of the newly added rule
        "total_rules": rule_count,
    }


@forcing_mcp.tool()
async def set_rainfall_override(
    ctx: Context,
    session_id: str = "default",
    gage_id: str = "",
    rainfall: float = 0.0,
) -> dict:
    """Override rainfall on a rain gage with a persistent replacement value.

    This is a convenience shortcut that applies a ``REPLACE`` + ``PERSIST``
    forcing on the specified rain gage.  Use :func:`clear_forcing` to remove
    the override later.

    Parameters
    ----------
    session_id:
        Target simulation session.
    gage_id:
        The name or index of the rain gage.
    rainfall:
        The rainfall intensity to apply (in the model's rainfall units).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "running")

    gage_idx = await resolve_index(session.gages, gage_id, "Gage")

    forcing = session.forcing
    rain_value = float(rainfall)

    # v1: mode/persist are keyword-only; REPLACE (ForcingMode code 1) +
    # persist=True for a sticky override.
    try:
        await asyncio.to_thread(
            lambda: forcing.gage_rainfall(
                gage_idx, rain_value, mode=_resolve_forcing_mode("replace"), persist=True
            )
        )
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to set rainfall override: {exc}"
        ) from exc

    return {
        "status": "applied",
        "session_id": session_id,
        "gage_id": gage_id,
        "rainfall": rainfall,
        "mode": "replace",
        "persist": True,
    }


# ===========================================================================
# Phase 4 — persistent-forcing convenience tool
#
# The generic ``set_forcing`` accepts ``persist=True``, but the parameter
# is buried among several others.  For LLM workflows that explicitly want
# a sticky override (rather than one-shot per-step injection), this
# dedicated tool surfaces persistence as the headline behaviour.
# ===========================================================================


@forcing_mcp.tool()
async def set_persistent_forcing(
    ctx: Context,
    session_id: str = "default",
    target_type: str = "",
    element_id: str = "",
    variable: str = "",
    value: float = 0.0,
    mode: str = "replace",
) -> ForcingResult:
    """Apply a forcing override that **persists across timesteps**.

    Equivalent to :func:`set_forcing` with ``persist=True``, surfaced as
    its own tool so an LLM doesn't have to know about the persist flag
    to get a sticky override.  Use :func:`clear_forcing` to remove the
    override later.

    Sticky overrides are a v1-only capability — they require the new
    engine.  On the legacy backend the call is rejected with
    ``NOT_SUPPORTED`` because legacy resets API values every step
    automatically.

    Parameters
    ----------
    target_type:
        ``"node"`` / ``"link"`` / ``"subcatchment"`` / ``"gage"``.
    element_id:
        Element ID (string) or numeric index as a string.
    variable:
        Variable to override.  Same set as :func:`set_forcing`:
        node ``lateral_inflow`` / ``head`` / ``quality``;
        link ``flow`` / ``setting``;
        subcatchment ``rainfall`` / ``evap``;
        gage ``rainfall``.
    value:
        The forced value (units match the variable).
    mode:
        ``"replace"`` (default) overwrites the computed value;
        ``"add"`` adds to it.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    if session.engine_kind != "openswmm":
        raise ToolError(
            f"[{ErrorCode.NOT_SUPPORTED}] Persistent forcing requires the "
            f"openswmm backend; on legacy, values reset on each timestep. "
            f"Use set_forcing() with persist=False or switch backends."
        )

    # Delegate to set_forcing with persist=True so we don't duplicate the
    # validation + dispatch logic.
    return await set_forcing(
        ctx,
        session_id=session_id,
        target_type=target_type,
        element_id=element_id,
        variable=variable,
        value=value,
        mode=mode,
        persist=True,
    )
