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
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import ForcingResult

logger = logging.getLogger(__name__)

forcing_mcp = FastMCP("forcing")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Valid (target_type, variable) -> Forcing method name
_FORCING_DISPATCH: dict[tuple[str, str], str] = {
    ("node", "lateral_inflow"): "node_lat_inflow",
    ("node", "head"): "node_head_boundary",
    ("node", "quality"): "node_quality",
    ("link", "flow"): "link_flow",
    ("link", "setting"): "link_setting",
    ("subcatchment", "rainfall"): "subcatch_rainfall",
    ("subcatchment", "evap"): "subcatch_evap",
    ("gage", "rainfall"): "gage_rainfall",
}

_VALID_VARIABLES_BY_TYPE: dict[str, list[str]] = {
    "node": ["lateral_inflow", "head", "quality"],
    "link": ["flow", "setting"],
    "subcatchment": ["rainfall", "evap"],
    "gage": ["rainfall"],
}


def _resolve_forcing_mode(mode: str) -> int:
    """Map a human-readable mode string to the ForcingMode enum value."""
    mapping = {"replace": 0, "add": 1}
    key = mode.strip().lower()
    if key not in mapping:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid forcing mode '{mode}'. "
            f"Must be one of: {', '.join(mapping)}"
        )
    return mapping[key]


def _resolve_forcing_target(persist: bool) -> int:
    """Map the persist boolean to the ForcingTarget enum value."""
    # ForcingTarget: RESET=0, PERSIST=1
    return 1 if persist else 0


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
        subcatchment (``"rainfall"``, ``"evap"``),
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
    forcing_target = _resolve_forcing_target(persist)

    # Forcing methods take integer element indices, not string IDs.
    _accessor_map = {
        "node": "nodes",
        "link": "links",
        "subcatchment": "subcatchments",
        "gage": "gages",
    }
    accessor = getattr(session, _accessor_map[target_lower])
    element_idx = await asyncio.to_thread(accessor.get_index, element_id)
    if element_idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] {target_lower.capitalize()} "
            f"'{element_id}' not found."
        )

    forcing = session.forcing

    try:
        method = getattr(forcing, method_name)
        if var_lower == "quality":
            # node_quality requires an additional pollutant_idx argument;
            # default to pollutant index 0
            await asyncio.to_thread(method, element_idx, 0, value, forcing_mode, forcing_target)
        else:
            await asyncio.to_thread(method, element_idx, value, forcing_mode, forcing_target)
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
        # clear(target_type_code, element_idx): NODE=0, LINK=1, SUBCATCH=2, GAGE=3
        _type_codes = {"node": 0, "link": 1, "subcatchment": 2, "gage": 3}
        _accessor_map = {"node": "nodes", "link": "links",
                         "subcatchment": "subcatchments", "gage": "gages"}
        type_lower = target_type.strip().lower()
        type_code = _type_codes.get(type_lower)
        if type_code is None:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown target_type '{target_type}'."
            )
        accessor = getattr(session, _accessor_map[type_lower])
        element_idx = await asyncio.to_thread(accessor.get_index, element_id)
        if element_idx < 0:
            raise ToolError(
                f"[{ErrorCode.ELEMENT_NOT_FOUND}] {type_lower.capitalize()} "
                f"'{element_id}' not found."
            )
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

    link_idx = await asyncio.to_thread(session.links.get_index, link_id)
    if link_idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found.")

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

    try:
        await asyncio.to_thread(controls.add_rule, rule_text)
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to add control rule: {exc}") from exc

    rule_count = await asyncio.to_thread(controls.count)

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

    gage_idx = await asyncio.to_thread(session.gages.get_index, gage_id)
    if gage_idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Gage '{gage_id}' not found.")

    forcing = session.forcing

    # ForcingMode.REPLACE = 0, ForcingTarget.PERSIST = 1
    try:
        await asyncio.to_thread(forcing.gage_rainfall, gage_idx, rainfall, 0, 1)
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
