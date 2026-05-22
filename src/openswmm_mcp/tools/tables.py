"""Tables tools: curves, time series, and patterns.

Wraps the :class:`openswmm.engine.Tables` accessor with MCP tools. ``Tables``
works against either a :class:`Solver` (any non-closed lifecycle state) or a
:class:`ModelBuilder` (the ``building`` state); the helpers below pick the
right binding automatically.

C-API state contract (per ``openswmm_tables.h``):

* ``timeseries_add`` / ``curve_add`` / ``pattern_add`` require
  ``SWMM_STATE_BUILDING``. Creation tools therefore require the session to
  be in the ``building`` state with an attached ``ModelBuilder``.
* ``add_point`` / ``get_point`` / ``get_point_count`` / ``clear`` /
  ``lookup`` carry no state annotation in the header and work in any state
  where the engine handle is alive.
* Patterns: ``pattern_count`` is read-only and works anywhere;
  ``pattern_add`` and ``pattern_set_factors`` are creation/mutation and
  require ``building``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP
from openswmm.engine import Tables

from openswmm_mcp.dependencies import get_session_manager, require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

tables_mcp = FastMCP("tables")


# ---------------------------------------------------------------------------
# Type maps (string -> engine integer code)
# ---------------------------------------------------------------------------

_CURVE_TYPES: dict[str, int] = {
    "storage": 0,
    "diversion": 1,
    "tidal": 2,
    "rating": 3,
    "control": 4,
    "shape": 5,
    "pump1": 6,
    "pump2": 7,
    "pump3": 8,
    "pump4": 9,
    "weir": 10,
}

_PATTERN_TYPES: dict[str, int] = {
    "monthly": 0,
    "daily": 1,
    "hourly": 2,
    "weekend": 3,
}


def _resolve_curve_type(name: str | int) -> int:
    """Map a curve-type identifier to its engine integer code."""
    if isinstance(name, int):
        return name
    key = name.strip().lower()
    if key not in _CURVE_TYPES:
        valid = ", ".join(sorted(_CURVE_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown curve_type '{name}'. "
            f"Valid types: {valid}."
        )
    return _CURVE_TYPES[key]


def _resolve_pattern_type(name: str | int) -> int:
    """Map a pattern-type identifier to its engine integer code."""
    if isinstance(name, int):
        return name
    key = name.strip().lower()
    if key not in _PATTERN_TYPES:
        valid = ", ".join(sorted(_PATTERN_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown pattern_type '{name}'. "
            f"Valid types: {valid}."
        )
    return _PATTERN_TYPES[key]


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    """Return the session for *session_id*, ensuring the openswmm backend."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Tables (curves / time series / patterns)")
    return session


async def _get_tables_accessor(
    ctx: Context, session_id: str, *, require_building: bool = False
) -> tuple[SimSession, Any]:
    """Return ``(session, tables)`` for a Tables-aware tool call.

    For sessions in the ``building`` state, constructs ``Tables`` against the
    attached :class:`ModelBuilder`; otherwise returns the cached accessor on
    the backend (which wraps the :class:`Solver`).

    Raises a tool error when *require_building* is set and the session is not
    in the building state, or when the session is closed.
    """
    session = await _get_session(ctx, session_id)
    state = session.state

    if state == "closed":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is closed; "
            f"open or initialize it before calling Tables tools."
        )

    if state == "building":
        if session.model_builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in "
                f"'building' state but has no ModelBuilder attached."
            )
        return session, Tables(session.model_builder)

    if require_building:
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{state}'; creation requires the 'building' state (create the "
            f"model via building.create_model first)."
        )

    return session, session.tables


# ---------------------------------------------------------------------------
# Read-only lookup tools
# ---------------------------------------------------------------------------


@tables_mcp.tool()
async def count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of curves and time series in the model.

    The count combines both since the engine stores them in a single table
    namespace. Patterns are counted separately; see ``pattern_count``.
    """
    _, tables = await _get_tables_accessor(ctx, session_id)
    n = await asyncio.to_thread(tables.count)
    return {"session_id": session_id, "count": n}


@tables_mcp.tool()
async def get_id(
    ctx: Context, session_id: str = "default", index: int = 0
) -> dict:
    """Return the string ID of a table by zero-based index."""
    _, tables = await _get_tables_accessor(ctx, session_id)
    table_id = await asyncio.to_thread(tables.get_id, index)
    return {"session_id": session_id, "index": index, "id": table_id}


@tables_mcp.tool()
async def get_index(
    ctx: Context, session_id: str = "default", table_id: str = ""
) -> dict:
    """Return the zero-based index of a table by string ID.

    Returns ``-1`` if no table with that ID exists.
    """
    if not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)
    idx = await asyncio.to_thread(tables.get_index, table_id)
    return {"session_id": session_id, "id": table_id, "index": idx}


# ---------------------------------------------------------------------------
# Creation tools (building state only)
# ---------------------------------------------------------------------------


@tables_mcp.tool()
async def add_timeseries(
    ctx: Context,
    session_id: str = "default",
    ts_id: str = "",
    times: list[float] | None = None,
    values: list[float] | None = None,
) -> dict:
    """Create a time series and populate it with ``(time, value)`` points.

    Requires the session to be in the ``building`` state. Points are added
    in input order; the engine does not sort them.
    """
    if not ts_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] ts_id must not be empty.")
    if not times or not values:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] 'times' and 'values' must both be non-empty."
        )
    if len(times) != len(values):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] len(times)={len(times)} != "
            f"len(values)={len(values)}."
        )

    _, tables = await _get_tables_accessor(ctx, session_id, require_building=True)

    def _create_and_populate() -> int:
        idx = tables.timeseries_add(ts_id)
        for x, y in zip(times, values):
            tables.add_point(idx, float(x), float(y))
        return idx

    idx = await asyncio.to_thread(_create_and_populate)
    return {
        "status": "ok",
        "session_id": session_id,
        "id": ts_id,
        "index": idx,
        "points": len(times),
    }


@tables_mcp.tool()
async def add_curve(
    ctx: Context,
    session_id: str = "default",
    curve_id: str = "",
    curve_type: str = "storage",
    x_values: list[float] | None = None,
    y_values: list[float] | None = None,
) -> dict:
    """Create a curve and populate it with ``(x, y)`` points.

    ``curve_type`` is a string (``storage``, ``diversion``, ``tidal``,
    ``rating``, ``control``, ``shape``, ``pump1``..``pump4``, ``weir``) or
    the engine integer code directly. Requires the ``building`` state.
    """
    if not curve_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] curve_id must not be empty.")
    if not x_values or not y_values:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] 'x_values' and 'y_values' must both "
            f"be non-empty."
        )
    if len(x_values) != len(y_values):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] len(x_values)={len(x_values)} != "
            f"len(y_values)={len(y_values)}."
        )

    ctype = _resolve_curve_type(curve_type)
    _, tables = await _get_tables_accessor(ctx, session_id, require_building=True)

    def _create_and_populate() -> int:
        idx = tables.curve_add(curve_id, ctype)
        for x, y in zip(x_values, y_values):
            tables.add_point(idx, float(x), float(y))
        return idx

    idx = await asyncio.to_thread(_create_and_populate)
    return {
        "status": "ok",
        "session_id": session_id,
        "id": curve_id,
        "index": idx,
        "curve_type": curve_type,
        "points": len(x_values),
    }


# ---------------------------------------------------------------------------
# Point ops (work in any non-closed state)
# ---------------------------------------------------------------------------


@tables_mcp.tool()
async def add_point(
    ctx: Context,
    session_id: str = "default",
    table_id: str | int = "",
    x: float = 0.0,
    y: float = 0.0,
) -> dict:
    """Append a single ``(x, y)`` data point to an existing table."""
    if isinstance(table_id, str) and not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)
    await asyncio.to_thread(tables.add_point, table_id, float(x), float(y))
    return {"status": "ok", "session_id": session_id, "id": table_id, "x": x, "y": y}


@tables_mcp.tool()
async def get_point(
    ctx: Context,
    session_id: str = "default",
    table_id: str | int = "",
    point_index: int = 0,
) -> dict:
    """Read a single ``(x, y)`` data point from a table by point index."""
    if isinstance(table_id, str) and not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)
    x, y = await asyncio.to_thread(tables.get_point, table_id, point_index)
    return {
        "session_id": session_id,
        "id": table_id,
        "point_index": point_index,
        "x": x,
        "y": y,
    }


@tables_mcp.tool()
async def get_point_count(
    ctx: Context,
    session_id: str = "default",
    table_id: str | int = "",
) -> dict:
    """Return the number of data points in a table."""
    if isinstance(table_id, str) and not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)
    n = await asyncio.to_thread(tables.get_point_count, table_id)
    return {"session_id": session_id, "id": table_id, "count": n}


@tables_mcp.tool()
async def get_points(
    ctx: Context,
    session_id: str = "default",
    table_id: str | int = "",
) -> dict:
    """Return all data points in a table as a list of ``[x, y]`` pairs.

    Convenience wrapper that batches ``get_point_count`` + N x ``get_point``
    into a single tool call so an LLM can read a whole curve / time series
    without iterating.
    """
    if isinstance(table_id, str) and not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)

    def _read_all() -> list[list[float]]:
        n = tables.get_point_count(table_id)
        return [list(tables.get_point(table_id, i)) for i in range(n)]

    points = await asyncio.to_thread(_read_all)
    return {
        "session_id": session_id,
        "id": table_id,
        "count": len(points),
        "points": points,
    }


@tables_mcp.tool()
async def clear_points(
    ctx: Context,
    session_id: str = "default",
    table_id: str | int = "",
) -> dict:
    """Remove all data points from a table (the table itself remains)."""
    if isinstance(table_id, str) and not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)
    await asyncio.to_thread(tables.clear, table_id)
    return {"status": "ok", "session_id": session_id, "id": table_id}


@tables_mcp.tool()
async def lookup(
    ctx: Context,
    session_id: str = "default",
    table_id: str | int = "",
    x: float = 0.0,
) -> dict:
    """Interpolate a Y value from a table at the given X.

    Uses the engine's cursor-optimized lookup; values outside the table's
    X range clamp to the nearest endpoint.
    """
    if isinstance(table_id, str) and not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)
    y = await asyncio.to_thread(tables.lookup, table_id, float(x))
    return {"session_id": session_id, "id": table_id, "x": x, "y": y}


# ---------------------------------------------------------------------------
# Pattern tools
# ---------------------------------------------------------------------------


@tables_mcp.tool()
async def pattern_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of time patterns in the model."""
    _, tables = await _get_tables_accessor(ctx, session_id)
    n = await asyncio.to_thread(tables.pattern_count)
    return {"session_id": session_id, "count": n}


@tables_mcp.tool()
async def pattern_add(
    ctx: Context,
    session_id: str = "default",
    pattern_id: str = "",
    pattern_type: str = "monthly",
    factors: list[float] | None = None,
) -> dict:
    """Create a time pattern and (optionally) seed its multiplier factors.

    ``pattern_type`` accepts a string (``monthly``, ``daily``, ``hourly``,
    ``weekend``) or the integer engine code. When ``factors`` is supplied,
    it is applied via ``pattern_set_factors`` immediately after creation.
    Expected factor counts: 12 for monthly, 7 for daily, 24 for hourly /
    weekend.
    """
    if not pattern_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] pattern_id must not be empty.")
    ptype = _resolve_pattern_type(pattern_type)
    _, tables = await _get_tables_accessor(ctx, session_id, require_building=True)

    import numpy as np

    def _create_and_seed() -> int:
        idx = tables.pattern_add(pattern_id, ptype)
        if factors:
            arr = np.asarray(factors, dtype=np.float64)
            tables.pattern_set_factors(idx, arr)
        return idx

    idx = await asyncio.to_thread(_create_and_seed)
    return {
        "status": "ok",
        "session_id": session_id,
        "id": pattern_id,
        "index": idx,
        "pattern_type": pattern_type,
        "factors": len(factors) if factors else 0,
    }


@tables_mcp.tool()
async def pattern_set_factors(
    ctx: Context,
    session_id: str = "default",
    pattern_index: int = 0,
    factors: list[float] | None = None,
) -> dict:
    """Replace the multiplier factors of an existing time pattern.

    Pattern length is fixed by ``pattern_type`` at creation; supplying a
    factor count that does not match will raise an engine error.
    """
    if not factors:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] 'factors' must be non-empty.")
    _, tables = await _get_tables_accessor(ctx, session_id, require_building=True)

    import numpy as np

    arr = np.asarray(factors, dtype=np.float64)
    await asyncio.to_thread(tables.pattern_set_factors, pattern_index, arr)
    return {
        "status": "ok",
        "session_id": session_id,
        "pattern_index": pattern_index,
        "factors": len(factors),
    }
