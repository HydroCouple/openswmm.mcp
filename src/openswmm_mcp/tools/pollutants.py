"""Pollutants tools: fine-grained accessors beyond ``query.get_pollutant_info``.

The Python ``Pollutants`` accessor exposes 23 methods covering definition
(decay, concentrations in rainfall / groundwater / RDII / initial),
metadata (units, molecular weight, snow-only flag, co-pollutant), and
runtime quality injection at nodes / links.

This module wraps the surface as 22 MCP tools. The aggregate
``query.get_pollutant_info`` covers a read-only snapshot; this module adds
the individual setters for design-time configuration plus the runtime
injection setters (``set_node_quality`` / ``set_link_quality``) gated on
the ``running`` state.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

pollutants_mcp = FastMCP("pollutants")


_POLLUT_UNITS = {0: "mg_per_l", 1: "ug_per_l", 2: "count_per_l"}
_POLLUT_UNITS_REVERSE = {v: k for k, v in _POLLUT_UNITS.items()}


def _resolve_units(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower().replace("/", "_per_").replace("#", "count")
    if key not in _POLLUT_UNITS_REVERSE:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown units '{name}'. "
            f"Valid: mg_per_l, ug_per_l, count_per_l."
        )
    return _POLLUT_UNITS_REVERSE[key]


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Pollutants (fine-grained accessors)")
    return session


def _pollutants_accessor(session: SimSession):
    """Return the right Pollutants accessor for the session's state.

    In ``building`` state the session has no backend; construct against
    the ModelBuilder. Otherwise use the cached backend accessor.
    """
    from openswmm.engine import Pollutants

    if session.state == "building":
        if session.model_builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Building session has no ModelBuilder."
            )
        return Pollutants(session.model_builder)
    return session.pollutants


def _nodes_accessor(session: SimSession):
    """Return Nodes accessor for either state."""
    from openswmm.engine import Nodes

    if session.state == "building":
        return Nodes(session.model_builder)
    return session.nodes


def _links_accessor(session: SimSession):
    """Return Links accessor for either state."""
    from openswmm.engine import Links

    if session.state == "building":
        return Links(session.model_builder)
    return session.links


async def _resolve_pollutant(
    session: SimSession, pollutant_id: str | int
) -> int:
    if isinstance(pollutant_id, int):
        return pollutant_id
    if not pollutant_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] pollutant_id must not be empty."
        )
    idx = await asyncio.to_thread(
        _pollutants_accessor(session).get_index, pollutant_id
    )
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Pollutant '{pollutant_id}' not found."
        )
    return idx


# ===========================================================================
# Identity + creation
# ===========================================================================


@pollutants_mcp.tool()
async def count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of pollutants defined in the model."""
    session = await _get_session(ctx, session_id)
    n = await asyncio.to_thread(_pollutants_accessor(session).count)
    return {"session_id": session_id, "count": n}


@pollutants_mcp.tool()
async def add(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str = "",
    units: str = "mg_per_l",
) -> dict:
    """Add a new pollutant to the model (BUILDING state).

    ``units``: ``mg_per_l`` (0), ``ug_per_l`` (1), or ``count_per_l`` (2).
    """
    if not pollutant_id:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] pollutant_id must not be empty."
        )
    units_int = _resolve_units(units)
    session = await _get_session(ctx, session_id)
    # Pollutants.add works in building state via the ModelBuilder accessor.
    # Use the accessor on whatever the session exposes (ModelBuilder or solver).
    from openswmm.engine import Pollutants

    if session.state == "building":
        if session.model_builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Building session has no ModelBuilder."
            )
        accessor = Pollutants(session.model_builder)
    else:
        accessor = session.pollutants
    await asyncio.to_thread(accessor.add, pollutant_id, units_int)
    idx = await asyncio.to_thread(accessor.get_index, pollutant_id)
    return {
        "status": "ok", "session_id": session_id,
        "id": pollutant_id, "index": idx, "units": units,
    }


# ===========================================================================
# Property getters
# ===========================================================================


async def _scalar_get(
    ctx, session_id, pollutant_id, attr, key, value_type
):
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)
    v = await asyncio.to_thread(getattr(accessor, attr), idx)
    return {
        "session_id": session_id, "pollutant_id": pollutant_id,
        "pollutant_index": idx, key: value_type(v),
    }


async def _scalar_set(
    ctx, session_id, pollutant_id, attr, value, key, value_type
):
    if value is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] {key} is required."
        )
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)
    await asyncio.to_thread(
        getattr(accessor, attr), idx, value_type(value)
    )
    return {
        "status": "ok", "session_id": session_id,
        "pollutant_id": pollutant_id, "pollutant_index": idx,
        key: value_type(value),
    }


@pollutants_mcp.tool()
async def get_units(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration units for a pollutant (mg/L / ug/L / #/L)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    code = await asyncio.to_thread(_pollutants_accessor(session).get_units, idx)
    return {
        "session_id": session_id, "pollutant_id": pollutant_id,
        "pollutant_index": idx, "units_code": int(code),
        "units": _POLLUT_UNITS.get(int(code), "unknown"),
    }


@pollutants_mcp.tool()
async def get_kdecay(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the first-order decay coefficient (1/day) for a pollutant."""
    return await _scalar_get(
        ctx, session_id, pollutant_id, "get_kdecay", "kdecay", float
    )


@pollutants_mcp.tool()
async def set_kdecay(
    ctx: Context, session_id: str = "default",
    pollutant_id: str | int = "", kdecay: float = 0.0,
) -> dict:
    """Set the first-order decay coefficient (1/day)."""
    return await _scalar_set(
        ctx, session_id, pollutant_id, "set_kdecay", kdecay, "kdecay", float
    )


@pollutants_mcp.tool()
async def get_rain_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration of this pollutant in rainfall."""
    return await _scalar_get(
        ctx, session_id, pollutant_id, "get_rain_conc", "rain_conc", float
    )


@pollutants_mcp.tool()
async def set_rain_conc(
    ctx: Context, session_id: str = "default",
    pollutant_id: str | int = "", rain_conc: float = 0.0,
) -> dict:
    """Set the rainfall concentration."""
    return await _scalar_set(
        ctx, session_id, pollutant_id, "set_rain_conc", rain_conc,
        "rain_conc", float,
    )


@pollutants_mcp.tool()
async def get_gw_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration of this pollutant in groundwater."""
    return await _scalar_get(
        ctx, session_id, pollutant_id, "get_gw_conc", "gw_conc", float
    )


@pollutants_mcp.tool()
async def set_gw_conc(
    ctx: Context, session_id: str = "default",
    pollutant_id: str | int = "", gw_conc: float = 0.0,
) -> dict:
    """Set the groundwater concentration."""
    return await _scalar_set(
        ctx, session_id, pollutant_id, "set_gw_conc", gw_conc,
        "gw_conc", float,
    )


@pollutants_mcp.tool()
async def get_rdii_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration of this pollutant in RDII."""
    return await _scalar_get(
        ctx, session_id, pollutant_id, "get_rdii_conc", "rdii_conc", float
    )


@pollutants_mcp.tool()
async def set_rdii_conc(
    ctx: Context, session_id: str = "default",
    pollutant_id: str | int = "", rdii_conc: float = 0.0,
) -> dict:
    """Set the RDII concentration."""
    return await _scalar_set(
        ctx, session_id, pollutant_id, "set_rdii_conc", rdii_conc,
        "rdii_conc", float,
    )


@pollutants_mcp.tool()
async def get_init_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the initial concentration throughout the system."""
    return await _scalar_get(
        ctx, session_id, pollutant_id, "get_init_conc", "init_conc", float
    )


@pollutants_mcp.tool()
async def set_init_conc(
    ctx: Context, session_id: str = "default",
    pollutant_id: str | int = "", init_conc: float = 0.0,
) -> dict:
    """Set the initial system-wide concentration."""
    return await _scalar_set(
        ctx, session_id, pollutant_id, "set_init_conc", init_conc,
        "init_conc", float,
    )


@pollutants_mcp.tool()
async def get_mwt(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the molecular weight of a pollutant (g/mol)."""
    return await _scalar_get(
        ctx, session_id, pollutant_id, "get_mwt", "molecular_weight", float
    )


@pollutants_mcp.tool()
async def set_mwt(
    ctx: Context, session_id: str = "default",
    pollutant_id: str | int = "", molecular_weight: float = 0.0,
) -> dict:
    """Set the molecular weight (g/mol)."""
    return await _scalar_set(
        ctx, session_id, pollutant_id, "set_mwt", molecular_weight,
        "molecular_weight", float,
    )


@pollutants_mcp.tool()
async def get_snow_only(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the snow-only flag for a pollutant (True = transported only in snow)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    flag = await asyncio.to_thread(_pollutants_accessor(session).get_snow_only, idx)
    return {
        "session_id": session_id, "pollutant_id": pollutant_id,
        "pollutant_index": idx, "snow_only": bool(flag),
    }


@pollutants_mcp.tool()
async def set_snow_only(
    ctx: Context, session_id: str = "default",
    pollutant_id: str | int = "", snow_only: bool = False,
) -> dict:
    """Set the snow-only flag for a pollutant."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    await asyncio.to_thread(
        _pollutants_accessor(session).set_snow_only, idx, bool(snow_only)
    )
    return {
        "status": "ok", "session_id": session_id, "pollutant_id": pollutant_id,
        "pollutant_index": idx, "snow_only": snow_only,
    }


@pollutants_mcp.tool()
async def get_co_pollutant(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the co-pollutant index assigned to this pollutant (-1 = none)."""
    return await _scalar_get(
        ctx, session_id, pollutant_id, "get_co_pollutant",
        "co_pollutant_index", int,
    )


@pollutants_mcp.tool()
async def set_co_pollutant(
    ctx: Context, session_id: str = "default",
    pollutant_id: str | int = "", co_pollutant_index: int = -1,
) -> dict:
    """Assign a co-pollutant (set to -1 to clear).

    Co-pollutants link two pollutants for coupled transport; useful for
    decay products or pollutant pairs that move together in the system.
    """
    return await _scalar_set(
        ctx, session_id, pollutant_id, "set_co_pollutant",
        co_pollutant_index, "co_pollutant_index", int,
    )


# ===========================================================================
# Runtime quality injection (RUNNING state)
# ===========================================================================


@pollutants_mcp.tool()
async def set_node_quality(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
    pollutant_id: str | int = "",
    concentration: float = 0.0,
) -> dict:
    """Override a node's pollutant concentration mid-simulation.

    Runs against the running simulation. Pass the node_id (string) and the
    pollutant_id (string or int index); the value is the override
    concentration in the pollutant's units.
    """
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    node_idx = await asyncio.to_thread(session.nodes.get_index, node_id)
    if node_idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found."
        )
    pollut_idx = await _resolve_pollutant(session, pollutant_id)
    await asyncio.to_thread(
        _pollutants_accessor(session).set_node_quality,
        node_idx, pollut_idx, float(concentration),
    )
    return {
        "status": "ok", "session_id": session_id,
        "node_id": node_id, "node_index": node_idx,
        "pollutant_id": pollutant_id, "pollutant_index": pollut_idx,
        "concentration": concentration,
    }


@pollutants_mcp.tool()
async def set_link_quality(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    pollutant_id: str | int = "",
    concentration: float = 0.0,
) -> dict:
    """Override a link's pollutant concentration mid-simulation."""
    if not link_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] link_id must not be empty.")
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    link_idx = await asyncio.to_thread(session.links.get_index, link_id)
    if link_idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found."
        )
    pollut_idx = await _resolve_pollutant(session, pollutant_id)
    await asyncio.to_thread(
        _pollutants_accessor(session).set_link_quality,
        link_idx, pollut_idx, float(concentration),
    )
    return {
        "status": "ok", "session_id": session_id,
        "link_id": link_id, "link_index": link_idx,
        "pollutant_id": pollutant_id, "pollutant_index": pollut_idx,
        "concentration": concentration,
    }
