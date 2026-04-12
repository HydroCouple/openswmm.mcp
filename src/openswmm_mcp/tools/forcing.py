"""Forcing and control tools for the OpenSWMM MCP server.

Provides tools for applying runtime forcing overrides (rainfall, inflows,
boundary conditions, etc.) and manipulating control rules / link settings
during a running simulation.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import get_session_manager, require_state
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

    forcing = session.forcing

    try:
        method = getattr(forcing, method_name)
        if var_lower == "quality":
            # node_quality requires an additional pollutant_idx argument;
            # default to pollutant index 0
            await asyncio.to_thread(method, element_id, 0, value, forcing_mode, forcing_target)
        else:
            await asyncio.to_thread(method, element_id, value, forcing_mode, forcing_target)
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
        else:
            if target_type is None or element_id is None:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Both 'target_type' and "
                    "'element_id' must be provided to clear a specific element, "
                    "or omit both to clear all forcing."
                )
            await asyncio.to_thread(forcing.clear, target_type, element_id)
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

    controls = session.controls

    try:
        await asyncio.to_thread(controls.set_link_setting, link_id, setting)
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

    controls = session.controls

    try:
        rule_index = await asyncio.to_thread(controls.add_rule, rule_text)
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to add control rule: {exc}") from exc

    rule_count = await asyncio.to_thread(controls.count)

    return {
        "status": "added",
        "session_id": session_id,
        "rule_index": rule_index,
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

    forcing = session.forcing

    # ForcingMode.REPLACE = 0, ForcingTarget.PERSIST = 1
    try:
        await asyncio.to_thread(forcing.gage_rainfall, gage_id, rainfall, 0, 1)
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
