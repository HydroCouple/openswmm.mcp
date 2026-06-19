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

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
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
            raise ToolError(f"[{ErrorCode.INVALID_STATE}] Building session has no ModelBuilder.")
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


async def _resolve_pollutant(session: SimSession, pollutant_id: str | int) -> int:
    # ``get_index`` raises ElementNotFoundError (a KeyError subclass) for an
    # unknown id rather than returning a -1 sentinel; ``resolve_index``
    # handles int passthrough, the empty-string guard, and that translation.
    return await resolve_index(_pollutants_accessor(session), pollutant_id, "Pollutant")


# ===========================================================================
# Identity + creation
# ===========================================================================


@pollutants_mcp.tool()
async def count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of pollutants defined in the model."""
    session = await _get_session(ctx, session_id)
    accessor = _pollutants_accessor(session)
    n = await asyncio.to_thread(lambda: len(accessor))
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
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] pollutant_id must not be empty.")
    units_int = _resolve_units(units)
    session = await _get_session(ctx, session_id)
    accessor = _pollutants_accessor(session)

    def _add() -> int:
        # v1: Pollutants.add(name, units) returns a Pollutant wrapper.
        new_p = accessor.add(pollutant_id, units_int)
        return new_p.index

    idx = await asyncio.to_thread(_add)
    return {
        "status": "ok",
        "session_id": session_id,
        "id": pollutant_id,
        "index": idx,
        "units": units,
    }


# ===========================================================================
# Property getters
# ===========================================================================


async def _attr_get(ctx, session_id, pollutant_id, attr, key, value_type):
    """Read a v1 property off the Pollutant wrapper."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)
    v = await asyncio.to_thread(lambda: getattr(accessor[idx], attr))
    return {
        "session_id": session_id,
        "pollutant_id": pollutant_id,
        "pollutant_index": idx,
        key: value_type(v),
    }


async def _attr_set(ctx, session_id, pollutant_id, attr, value, key, value_type):
    """Write a v1 property on the Pollutant wrapper."""
    if value is None:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {key} is required.")
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)
    typed = value_type(value)

    def _set() -> None:
        setattr(accessor[idx], attr, typed)

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "pollutant_id": pollutant_id,
        "pollutant_index": idx,
        key: typed,
    }


@pollutants_mcp.tool()
async def get_units(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration units for a pollutant (mg/L / ug/L / #/L)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)
    # v1 Pollutant.units returns a ConcentrationUnits IntEnum.
    code = await asyncio.to_thread(lambda: int(accessor[idx].units))
    return {
        "session_id": session_id,
        "pollutant_id": pollutant_id,
        "pollutant_index": idx,
        "units_code": code,
        "units": _POLLUT_UNITS.get(code, "unknown"),
    }


@pollutants_mcp.tool()
async def get_kdecay(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the first-order decay coefficient (1/day) for a pollutant."""
    return await _attr_get(ctx, session_id, pollutant_id, "kdecay", "kdecay", float)


@pollutants_mcp.tool()
async def set_kdecay(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    kdecay: float = 0.0,
) -> dict:
    """Set the first-order decay coefficient (1/day)."""
    return await _attr_set(ctx, session_id, pollutant_id, "kdecay", kdecay, "kdecay", float)


@pollutants_mcp.tool()
async def get_rain_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration of this pollutant in rainfall."""
    return await _attr_get(ctx, session_id, pollutant_id, "rain_conc", "rain_conc", float)


@pollutants_mcp.tool()
async def set_rain_conc(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    rain_conc: float = 0.0,
) -> dict:
    """Set the rainfall concentration."""
    return await _attr_set(
        ctx, session_id, pollutant_id, "rain_conc", rain_conc, "rain_conc", float
    )


@pollutants_mcp.tool()
async def get_gw_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration of this pollutant in groundwater."""
    return await _attr_get(ctx, session_id, pollutant_id, "gw_conc", "gw_conc", float)


@pollutants_mcp.tool()
async def set_gw_conc(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    gw_conc: float = 0.0,
) -> dict:
    """Set the groundwater concentration."""
    return await _attr_set(
        ctx, session_id, pollutant_id, "gw_conc", gw_conc, "gw_conc", float
    )


@pollutants_mcp.tool()
async def get_rdii_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration of this pollutant in RDII."""
    return await _attr_get(ctx, session_id, pollutant_id, "rdii_conc", "rdii_conc", float)


@pollutants_mcp.tool()
async def set_rdii_conc(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    rdii_conc: float = 0.0,
) -> dict:
    """Set the RDII concentration."""
    return await _attr_set(
        ctx, session_id, pollutant_id, "rdii_conc", rdii_conc, "rdii_conc", float
    )


@pollutants_mcp.tool()
async def get_dwf_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the concentration of this pollutant in dry-weather flow."""
    return await _attr_get(ctx, session_id, pollutant_id, "dwf_conc", "dwf_conc", float)


@pollutants_mcp.tool()
async def set_dwf_conc(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    dwf_conc: float = 0.0,
) -> dict:
    """Set the dry-weather-flow concentration."""
    return await _attr_set(
        ctx, session_id, pollutant_id, "dwf_conc", dwf_conc, "dwf_conc", float
    )


@pollutants_mcp.tool()
async def get_init_conc(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the initial concentration throughout the system."""
    return await _attr_get(ctx, session_id, pollutant_id, "init_conc", "init_conc", float)


@pollutants_mcp.tool()
async def set_init_conc(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    init_conc: float = 0.0,
) -> dict:
    """Set the initial system-wide concentration."""
    return await _attr_set(
        ctx, session_id, pollutant_id, "init_conc", init_conc, "init_conc", float
    )


@pollutants_mcp.tool()
async def get_mwt(ctx: Context, session_id: str = "default", pollutant_id: str | int = "") -> dict:
    """Return the molecular weight of a pollutant (g/mol)."""
    return await _attr_get(ctx, session_id, pollutant_id, "mwt", "molecular_weight", float)


@pollutants_mcp.tool()
async def set_mwt(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    molecular_weight: float = 0.0,
) -> dict:
    """Set the molecular weight (g/mol)."""
    return await _attr_set(
        ctx,
        session_id,
        pollutant_id,
        "mwt",
        molecular_weight,
        "molecular_weight",
        float,
    )


@pollutants_mcp.tool()
async def get_snow_only(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the snow-only flag for a pollutant (True = transported only in snow)."""
    return await _attr_get(ctx, session_id, pollutant_id, "snow_only", "snow_only", bool)


@pollutants_mcp.tool()
async def set_snow_only(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    snow_only: bool = False,
) -> dict:
    """Set the snow-only flag for a pollutant."""
    return await _attr_set(
        ctx, session_id, pollutant_id, "snow_only", snow_only, "snow_only", bool
    )


@pollutants_mcp.tool()
async def get_co_pollutant(
    ctx: Context, session_id: str = "default", pollutant_id: str | int = ""
) -> dict:
    """Return the co-pollutant index assigned to this pollutant (-1 = none).

    v1 surfaces co-pollutant as an optional ``(Pollutant, fraction)`` tuple;
    we project that down to the legacy ``(index, fraction)`` shape so the
    JSON contract is preserved (with ``fraction`` exposed as a new field).
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)

    def _read() -> tuple[int, float]:
        co = accessor[idx].co_pollutant
        if co is None:
            return -1, 0.0
        co_p, frac = co
        return int(co_p.index), float(frac)

    co_idx, frac = await asyncio.to_thread(_read)
    return {
        "session_id": session_id,
        "pollutant_id": pollutant_id,
        "pollutant_index": idx,
        "co_pollutant_index": co_idx,
        "fraction": frac,
    }


@pollutants_mcp.tool()
async def set_co_pollutant(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str | int = "",
    co_pollutant_index: int = -1,
    fraction: float = 1.0,
) -> dict:
    """Assign a co-pollutant (set ``co_pollutant_index`` to ``-1`` to clear).

    v1 requires a fraction alongside the co-pollutant.  Defaults to ``1.0``
    so callers that previously only supplied an index see the same effective
    behaviour.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)
    co_idx = int(co_pollutant_index)
    frac = float(fraction)

    def _set() -> None:
        accessor[idx].set_co_pollutant(co_idx, frac)

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "pollutant_id": pollutant_id,
        "pollutant_index": idx,
        "co_pollutant_index": co_idx,
        "fraction": frac,
    }


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
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found.")
    pollut_idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)
    conc = float(concentration)
    # v1 keeps set_node_quality on the Pollutants collection (it's a
    # cross-domain operation, not on a single Pollutant wrapper).
    await asyncio.to_thread(
        accessor.set_node_quality, node_idx, pollut_idx, conc
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": node_idx,
        "pollutant_id": pollutant_id,
        "pollutant_index": pollut_idx,
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
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found.")
    pollut_idx = await _resolve_pollutant(session, pollutant_id)
    accessor = _pollutants_accessor(session)
    conc = float(concentration)
    await asyncio.to_thread(
        accessor.set_link_quality, link_idx, pollut_idx, conc
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": link_idx,
        "pollutant_id": pollutant_id,
        "pollutant_index": pollut_idx,
        "concentration": concentration,
    }
