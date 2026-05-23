"""Inflows tools: external inflows, dry-weather flow, RDII, and unit hydrographs.

Wraps :class:`openswmm.engine.Inflows` for *design-time* configuration of
boundary inflows. Runtime overrides (rainfall, head BCs, lateral inflow per
step) live in ``forcing.py``; this module is the persistent ``.inp``-section
counterpart.

Domain covered (Python ``Inflows`` surface):

* ``[INFLOWS]`` — :func:`add_external`, :func:`ext_inflow_count`
* ``[DWF]`` — :func:`add_dwf`, :func:`dwf_count`
* ``[RDII]`` — :func:`add_rdii`, :func:`get_rdii`, :func:`rdii_count`
* ``[HYDROGRAPHS]`` — :func:`add_hydrograph`, :func:`get_hydrograph`,
  :func:`hydrograph_count`, :func:`add_hydrograph_gage`,
  :func:`get_hydrograph_gage`, :func:`hydrograph_gage_count`,
  :func:`hydrograph_group_count`, :func:`list_hydrograph_groups`
* ``[RDII_DECAY]`` — :func:`add_rdii_decay`, :func:`get_rdii_decay`,
  :func:`rdii_decay_count`

The Python ``Inflows`` constructor accepts either a :class:`Solver` (any
non-closed lifecycle state) or a :class:`ModelBuilder` (``building``
state). The :func:`_get_inflows_accessor` helper picks the right binding.

Node references: external inflow / DWF / RDII tools accept the string node
ID and resolve to integer index via ``session.nodes.get_index`` before
calling the engine (which takes ``int`` only). Pattern / time series /
hydrograph names are passed through unchanged — verifying that those
upstream tables exist is the *caller's* responsibility (a future hardening
pass could cross-check via ``tables.get_index`` before forwarding).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP
from openswmm.engine import Inflows, Nodes

from openswmm_mcp.dependencies import get_session_manager, require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

inflows_mcp = FastMCP("inflows")


# ---------------------------------------------------------------------------
# Enums (string -> engine integer code)
# ---------------------------------------------------------------------------


_UH_RESPONSE: dict[str, int] = {"short": 0, "medium": 1, "long": 2}
_MONTHS: dict[str, int] = {
    "all": -1,
    "jan": 0, "feb": 1, "mar": 2, "apr": 3, "may": 4, "jun": 5,
    "jul": 6, "aug": 7, "sep": 8, "oct": 9, "nov": 10, "dec": 11,
}


def _resolve_response(name: str | int) -> int:
    if isinstance(name, int):
        if name not in (0, 1, 2):
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] response int must be 0 (SHORT), "
                f"1 (MEDIUM), or 2 (LONG); got {name}."
            )
        return name
    key = name.strip().lower()
    if key not in _UH_RESPONSE:
        valid = ", ".join(sorted(_UH_RESPONSE))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown response '{name}'. "
            f"Valid values: {valid}."
        )
    return _UH_RESPONSE[key]


def _resolve_month(name: str | int) -> int:
    if isinstance(name, int):
        if not (-1 <= name <= 11):
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] month int must be -1 (ALL) or "
                f"0..11 (JAN..DEC); got {name}."
            )
        return name
    key = name.strip().lower()
    if key not in _MONTHS:
        valid = ", ".join(sorted(_MONTHS))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown month '{name}'. "
            f"Valid values: {valid}."
        )
    return _MONTHS[key]


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Inflows (external / DWF / RDII / hydrographs)")
    return session


async def _get_inflows_accessor(
    ctx: Context, session_id: str
) -> tuple[SimSession, Any, Any]:
    """Return ``(session, inflows, nodes)`` for an inflows tool call.

    ``nodes`` is used to resolve string node IDs to integer indices for the
    node-targeted tools (``add_external`` / ``add_dwf`` / ``add_rdii``).

    Accepts any non-closed state. For ``building`` sessions the underlying
    accessor is constructed against the attached :class:`ModelBuilder`;
    otherwise the cached backend accessors are returned.
    """
    session = await _get_session(ctx, session_id)
    state = session.state
    if state == "closed":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is closed; "
            f"open or initialize it before calling Inflows tools."
        )

    if state == "building":
        builder = session.model_builder
        if builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in "
                f"'building' state but has no ModelBuilder attached."
            )
        return session, Inflows(builder), Nodes(builder)

    return session, session.inflows, session.nodes


async def _resolve_node_idx(nodes: Any, node_id: str | int) -> int:
    """Translate a string node ID to integer index; pass through ints."""
    if isinstance(node_id, int):
        return node_id
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")
    idx = await asyncio.to_thread(nodes.get_index, node_id)
    if idx < 0:
        raise ToolError(
            f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found in this session."
        )
    return idx


# ===========================================================================
# [INFLOWS] -- external time-series inflows
# ===========================================================================


@inflows_mcp.tool()
async def add_external(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    constituent: str = "FLOW",
    ts_name: str = "",
    inflow_type: str = "FLOW",
    m_factor: float = 1.0,
    s_factor: float = 1.0,
    baseline: float = 0.0,
    pattern: str = "",
) -> dict:
    """Add an external inflow to a node.

    ``constituent`` is either ``"FLOW"`` or a pollutant ID. ``inflow_type``
    is one of ``"FLOW"``, ``"CONCEN"``, ``"MASS"``. ``ts_name`` references
    an existing ``[TIMESERIES]`` entry (empty string for none).
    """
    _, inflows, nodes = await _get_inflows_accessor(ctx, session_id)
    idx = await _resolve_node_idx(nodes, node_id)
    await asyncio.to_thread(
        inflows.add_external,
        idx,
        constituent,
        ts_name,
        inflow_type,
        float(m_factor),
        float(s_factor),
        float(baseline),
        pattern,
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "constituent": constituent,
        "ts_name": ts_name,
    }


@inflows_mcp.tool()
async def ext_inflow_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of external inflow rows in the model."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    n = await asyncio.to_thread(inflows.ext_inflow_count)
    return {"session_id": session_id, "count": n}


# ===========================================================================
# [DWF] -- dry-weather flow
# ===========================================================================


@inflows_mcp.tool()
async def add_dwf(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    constituent: str = "FLOW",
    avg_value: float = 0.0,
    monthly_pattern: str = "",
    daily_pattern: str = "",
    hourly_pattern: str = "",
    weekend_pattern: str = "",
) -> dict:
    """Add a dry-weather flow to a node.

    ``constituent`` is ``"FLOW"`` or a pollutant ID. ``avg_value`` is the
    constant baseline value. The four pattern arguments are pattern IDs
    (empty string = unused); use ``tables.pattern_add`` to create them.

    Pattern coupling: this tool does not verify that the named patterns
    exist before forwarding to the engine. A dangling reference will be
    surfaced by the engine at lookup time, not here.
    """
    _, inflows, nodes = await _get_inflows_accessor(ctx, session_id)
    idx = await _resolve_node_idx(nodes, node_id)
    await asyncio.to_thread(
        inflows.add_dwf,
        idx,
        constituent,
        float(avg_value),
        monthly_pattern,
        daily_pattern,
        hourly_pattern,
        weekend_pattern,
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "constituent": constituent,
        "avg_value": avg_value,
        "patterns": [monthly_pattern, daily_pattern, hourly_pattern, weekend_pattern],
    }


@inflows_mcp.tool()
async def dwf_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of dry-weather-flow rows in the model."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    n = await asyncio.to_thread(inflows.dwf_count)
    return {"session_id": session_id, "count": n}


# ===========================================================================
# [RDII] -- rainfall-derived inflow / infiltration
# ===========================================================================


@inflows_mcp.tool()
async def add_rdii(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    uh_name: str = "",
    area: float = 0.0,
) -> dict:
    """Assign an RDII inflow to a node.

    ``uh_name`` references a unit hydrograph group created via
    :func:`add_hydrograph`. ``area`` is the contributing sewershed area
    (project area units).
    """
    if not uh_name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] uh_name must not be empty.")
    _, inflows, nodes = await _get_inflows_accessor(ctx, session_id)
    idx = await _resolve_node_idx(nodes, node_id)
    await asyncio.to_thread(inflows.add_rdii, idx, uh_name, float(area))
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "uh_name": uh_name,
        "area": area,
    }


@inflows_mcp.tool()
async def get_rdii(
    ctx: Context, session_id: str = "default", entry_index: int = 0
) -> dict:
    """Read back the I{entry_index}-th RDII assignment as ``(node_idx, uh_name, area)``."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    node_idx, uh_name, area = await asyncio.to_thread(inflows.get_rdii, entry_index)
    return {
        "session_id": session_id,
        "entry_index": entry_index,
        "node_index": node_idx,
        "uh_name": uh_name,
        "area": area,
    }


@inflows_mcp.tool()
async def rdii_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of RDII inflow rows in the model."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    n = await asyncio.to_thread(inflows.rdii_count)
    return {"session_id": session_id, "count": n}


# ===========================================================================
# [HYDROGRAPHS] -- unit hydrograph parameter rows
# ===========================================================================


@inflows_mcp.tool()
async def add_hydrograph(
    ctx: Context,
    session_id: str = "default",
    uh_name: str = "",
    month: str | int = "all",
    response: str | int = "short",
    r: float = 0.0,
    t: float = 0.0,
    k: float = 1.0,
    dmax: float = 0.0,
    drecov: float = 0.0,
    dinit: float = 0.0,
) -> dict:
    """Add a unit-hydrograph parameter line.

    ``month`` is ``"all"``/``-1`` (the default), ``"jan"``..``"dec"``, or
    ``0..11``. ``response`` is ``"short"``/``"medium"``/``"long"`` or
    ``0..2``.

    Parameters
    ----------
    r:
        Fraction of rainfall that becomes RDII (0..1).
    t:
        Time to peak (hours).
    k:
        Ratio of base time to peak time (must be >= 1).
    dmax:
        Maximum initial-abstraction depth (project depth units).
    drecov:
        Linear-model IA recovery rate. Ignored when an exponential-decay
        row exists for the same ``(uh_name, response)`` pair (see
        :func:`add_rdii_decay`).
    dinit:
        Initial IA already used.
    """
    if not uh_name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] uh_name must not be empty.")
    if k < 1.0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] k (base/peak time ratio) must be "
            f">= 1.0; got {k}."
        )
    m_int = _resolve_month(month)
    r_int = _resolve_response(response)
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    await asyncio.to_thread(
        inflows.add_hydrograph,
        uh_name,
        m_int,
        r_int,
        float(r),
        float(t),
        float(k),
        float(dmax),
        float(drecov),
        float(dinit),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "uh_name": uh_name,
        "month": m_int,
        "response": r_int,
    }


@inflows_mcp.tool()
async def get_hydrograph(
    ctx: Context, session_id: str = "default", entry_index: int = 0
) -> dict:
    """Read back the I{entry_index}-th hydrograph row as a dict."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    entry = await asyncio.to_thread(inflows.get_hydrograph, entry_index)
    return {"session_id": session_id, "entry_index": entry_index, "entry": dict(entry)}


@inflows_mcp.tool()
async def hydrograph_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of hydrograph parameter rows in the model."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    n = await asyncio.to_thread(inflows.hydrograph_count)
    return {"session_id": session_id, "count": n}


@inflows_mcp.tool()
async def add_hydrograph_gage(
    ctx: Context,
    session_id: str = "default",
    uh_name: str = "",
    gage_name: str = "",
) -> dict:
    """Assign a rain gage to a unit-hydrograph group.

    The gage drives the RDII calculation for every node that references
    this hydrograph via :func:`add_rdii`.
    """
    if not uh_name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] uh_name must not be empty.")
    if not gage_name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] gage_name must not be empty.")
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    await asyncio.to_thread(inflows.add_hydrograph_gage, uh_name, gage_name)
    return {
        "status": "ok",
        "session_id": session_id,
        "uh_name": uh_name,
        "gage_name": gage_name,
    }


@inflows_mcp.tool()
async def get_hydrograph_gage(
    ctx: Context, session_id: str = "default", entry_index: int = 0
) -> dict:
    """Read back the I{entry_index}-th UH-to-gage assignment as ``(uh_name, gage_name)``."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    uh, gage = await asyncio.to_thread(inflows.get_hydrograph_gage, entry_index)
    return {
        "session_id": session_id,
        "entry_index": entry_index,
        "uh_name": uh,
        "gage_name": gage,
    }


@inflows_mcp.tool()
async def hydrograph_gage_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of UH-to-gage assignments in the model."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    n = await asyncio.to_thread(inflows.hydrograph_gage_count)
    return {"session_id": session_id, "count": n}


@inflows_mcp.tool()
async def hydrograph_group_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of *unique* unit-hydrograph group names.

    A UH "group" is identified by name; the engine stores one row per
    ``(group, month, response)``. This count is the number of distinct
    group names across parameter entries and gage assignments — the
    figure a GUI Object Browser needs for the Unit Hydrographs section.
    """
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    n = await asyncio.to_thread(inflows.hydrograph_group_count)
    return {"session_id": session_id, "count": n}


@inflows_mcp.tool()
async def list_hydrograph_groups(
    ctx: Context, session_id: str = "default"
) -> dict:
    """Return the unit-hydrograph groups as a list of ``{index, name}`` dicts.

    Groups are enumerated in first-occurrence order across the parameter
    entry list (matches the order in which they appear in the
    ``[HYDROGRAPHS]`` section of the input file). Convenience wrapper
    that batches ``hydrograph_group_count`` + N x
    ``get_hydrograph_group_id`` so an LLM (or GUI Object Browser) can
    populate the Unit Hydrographs node in a single call.
    """
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)

    def _read_all() -> list[dict[str, Any]]:
        n = inflows.hydrograph_group_count()
        return [
            {"index": i, "name": inflows.get_hydrograph_group_id(i)}
            for i in range(n)
        ]

    groups = await asyncio.to_thread(_read_all)
    return {"session_id": session_id, "count": len(groups), "groups": groups}


# ===========================================================================
# [RDII_DECAY] -- exponential IA decay (physically-based replacement for drecov)
# ===========================================================================


@inflows_mcp.tool()
async def add_rdii_decay(
    ctx: Context,
    session_id: str = "default",
    uh_name: str = "",
    response: str | int = "short",
    k_dep: float = 0.0,
    k_0: float = 0.0,
    k_T: float = 0.0,
    T_ref: float = 10.0,
    theta_rec: float = 0.0,
    T_freeze: float = 0.0,
) -> dict:
    """Add an exponential IA-decay row for a ``(uh_name, response)`` pair.

    Replaces the legacy linear ``drecov`` rate from :func:`add_hydrograph`
    with a physically-based recovery model:

        depletion: dIA/dt = -k_dep * rainfall
        recovery:  dIA/dt = +k_0 + k_T * exp(theta_rec * (T - T_ref))

    Recovery is suppressed when ``T <= T_freeze``. The hydrograph row for
    ``(uh_name, response)`` must already exist.
    """
    if not uh_name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] uh_name must not be empty.")
    r_int = _resolve_response(response)
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    await asyncio.to_thread(
        inflows.add_rdii_decay,
        uh_name,
        r_int,
        float(k_dep),
        float(k_0),
        float(k_T),
        float(T_ref),
        float(theta_rec),
        float(T_freeze),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "uh_name": uh_name,
        "response": r_int,
    }


@inflows_mcp.tool()
async def get_rdii_decay(
    ctx: Context, session_id: str = "default", entry_index: int = 0
) -> dict:
    """Read back the I{entry_index}-th exponential-decay row as a dict."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    entry = await asyncio.to_thread(inflows.get_rdii_decay, entry_index)
    return {"session_id": session_id, "entry_index": entry_index, "entry": dict(entry)}


@inflows_mcp.tool()
async def rdii_decay_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of exponential IA-decay rows in the model."""
    _, inflows, _ = await _get_inflows_accessor(ctx, session_id)
    n = await asyncio.to_thread(inflows.rdii_decay_count)
    return {"session_id": session_id, "count": n}
