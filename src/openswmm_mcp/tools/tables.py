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
from openswmm.engine import Patterns, Tables

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
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown curve_type '{name}'. Valid types: {valid}."
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
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown pattern_type '{name}'. Valid types: {valid}."
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
    n = await asyncio.to_thread(lambda: len(tables))
    return {"session_id": session_id, "count": n}


@tables_mcp.tool()
async def get_id(ctx: Context, session_id: str = "default", index: int = 0) -> dict:
    """Return the string ID of a table by zero-based index."""
    _, tables = await _get_tables_accessor(ctx, session_id)
    table_id = await asyncio.to_thread(tables.get_id, index)
    return {"session_id": session_id, "index": index, "id": table_id}


@tables_mcp.tool()
async def get_index(ctx: Context, session_id: str = "default", table_id: str = "") -> dict:
    """Return the zero-based index of a table by string ID.

    Returns ``-1`` if no table with that ID exists.
    """
    if not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)
    idx = await asyncio.to_thread(tables.get_index, table_id)
    return {"session_id": session_id, "id": table_id, "index": idx}


@tables_mcp.tool()
async def get_type(ctx: Context, session_id: str = "default", table_id: str | int = "") -> dict:
    """Return the type of a table (curve kind or time series).

    Surfaces ``Tables.get_type`` — a ``TableType`` enum identifying the
    table (e.g. ``STORAGE``, ``RATING``, ``PUMP1`` for curves, or the
    time-series kind). ``table_id`` is a string ID or integer index.
    Reports both the enum ``type`` name and its integer ``type_code``.
    """
    if isinstance(table_id, str) and not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)
    # v1 Tables.get_type returns a TableType IntEnum.
    ttype = await asyncio.to_thread(tables.get_type, table_id)
    return {
        "session_id": session_id,
        "id": table_id,
        "type": getattr(ttype, "name", str(ttype)),
        "type_code": int(ttype),
    }


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
            f"[{ErrorCode.VALIDATION_ERROR}] len(times)={len(times)} != len(values)={len(values)}."
        )

    _, tables = await _get_tables_accessor(ctx, session_id, require_building=True)

    def _create_and_populate() -> int:
        # v1 Tables.add_timeseries(name) returns a TimeSeries wrapper.
        ts = tables.add_timeseries(ts_id)
        for x, y in zip(times, values):
            # TimeSeries.add accepts a float (interpreted as hours-from-start
            # by the engine) or a datetime; keep the legacy float semantic.
            ts.add(float(x), float(y))
        return ts.index

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
            f"[{ErrorCode.VALIDATION_ERROR}] 'x_values' and 'y_values' must both be non-empty."
        )
    if len(x_values) != len(y_values):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] len(x_values)={len(x_values)} != "
            f"len(y_values)={len(y_values)}."
        )

    ctype = _resolve_curve_type(curve_type)
    _, tables = await _get_tables_accessor(ctx, session_id, require_building=True)

    def _create_and_populate() -> int:
        # v1 Tables.add_curve(name, type_int) returns a Curve wrapper.
        curve = tables.add_curve(curve_id, ctype)
        for x, y in zip(x_values, y_values):
            curve.add_point(float(x), float(y))
        return curve.index

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
    x_val, y_val = float(x), float(y)

    def _add() -> None:
        # v1: point ops live on the per-table wrapper.
        tables[table_id].add_point(x_val, y_val)

    await asyncio.to_thread(_add)
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

    def _read() -> tuple[float, float]:
        # v1 _PointTable doesn't expose a get_point(idx) — read via .points
        # array, which is contiguous (TimeSeries: structured; Curve: float).
        pts = tables[table_id].points
        row = pts[point_index]
        # TimeSeries has named fields (time, value); Curve has [x, y].
        try:
            return float(row["time"].astype("float64") / 1e9), float(row["value"])
        except (IndexError, ValueError, TypeError, KeyError):
            return float(row[0]), float(row[1])

    x, y = await asyncio.to_thread(_read)
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
    n = await asyncio.to_thread(lambda: len(tables[table_id]))
    return {"session_id": session_id, "id": table_id, "count": n}


@tables_mcp.tool()
async def get_points(
    ctx: Context,
    session_id: str = "default",
    table_id: str | int = "",
) -> dict:
    """Return all data points in a table as a list of ``[x, y]`` pairs.

    Reads ``table.points`` (a NumPy array) in a single C call and projects
    each row to ``[x, y]`` floats for the JSON wire format.
    """
    if isinstance(table_id, str) and not table_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] table_id must not be empty.")
    _, tables = await _get_tables_accessor(ctx, session_id)

    def _read_all() -> list[list[float]]:
        pts = tables[table_id].points
        result: list[list[float]] = []
        for row in pts:
            try:
                x = float(row["time"].astype("float64") / 1e9)
                y = float(row["value"])
            except (IndexError, ValueError, TypeError, KeyError):
                x = float(row[0])
                y = float(row[1])
            result.append([x, y])
        return result

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
    await asyncio.to_thread(lambda: tables[table_id].clear())
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
    x_val = float(x)
    y = await asyncio.to_thread(lambda: tables[table_id].lookup(x_val))
    return {"session_id": session_id, "id": table_id, "x": x, "y": y}


# ---------------------------------------------------------------------------
# Pattern tools
# ---------------------------------------------------------------------------


def _patterns_accessor(session: SimSession):
    """Return the right Patterns accessor for the session's state.

    Patterns are a separate v1 collection from Tables.  In ``building``
    state construct against the ModelBuilder; otherwise pull from the
    Solver via the session pass-through.
    """
    if session.state == "building":
        if session.model_builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Building session has no ModelBuilder."
            )
        return Patterns(session.model_builder)
    return session.patterns


@tables_mcp.tool()
async def pattern_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of time patterns in the model."""
    session = await _get_session(ctx, session_id)
    patterns = _patterns_accessor(session)
    n = await asyncio.to_thread(lambda: len(patterns))
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
    it is applied via ``pattern.set_factors`` immediately after creation.
    Expected factor counts: 12 for monthly, 7 for daily, 24 for hourly /
    weekend.
    """
    if not pattern_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] pattern_id must not be empty.")
    ptype = _resolve_pattern_type(pattern_type)
    session = await _get_session(ctx, session_id)
    if session.state != "building":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{session.state}'; pattern_add requires 'building' state."
        )
    patterns = _patterns_accessor(session)

    def _create_and_seed() -> int:
        # v1 Patterns.add(name, type) returns a Pattern wrapper that
        # accepts ``set_factors(values, type)``.
        p = patterns.add(pattern_id, ptype)
        if factors:
            p.set_factors(factors, ptype)
        return p.index

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
async def pattern_remove(
    ctx: Context,
    session_id: str = "default",
    pattern_id: str | int = "",
) -> dict:
    """Remove a time pattern by string ID or integer index (BUILDING state).

    Mutation; requires the session to be in the ``building`` state (mirrors
    ``pattern_add``).
    """
    if isinstance(pattern_id, str) and not pattern_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] pattern_id must not be empty.")
    session = await _get_session(ctx, session_id)
    if session.state != "building":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{session.state}'; pattern_remove requires 'building' state."
        )
    patterns = _patterns_accessor(session)
    await asyncio.to_thread(patterns.remove, pattern_id)
    return {"status": "ok", "session_id": session_id, "id": pattern_id}


@tables_mcp.tool()
async def pattern_set_factors(
    ctx: Context,
    session_id: str = "default",
    pattern_index: int = 0,
    pattern_type: str = "monthly",
    factors: list[float] | None = None,
) -> dict:
    """Replace the multiplier factors of an existing time pattern.

    Pattern length is determined by ``pattern_type``; supplying a factor
    count that does not match will raise an engine error.  The
    ``pattern_type`` argument was added in v1 — it tells the engine which
    block (monthly / daily / hourly / weekend) the factors apply to.
    """
    if not factors:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] 'factors' must be non-empty.")
    ptype = _resolve_pattern_type(pattern_type)
    session = await _get_session(ctx, session_id)
    if session.state != "building":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{session.state}'; pattern_set_factors requires 'building' state."
        )
    patterns = _patterns_accessor(session)
    idx = int(pattern_index)
    values = list(factors)

    def _set() -> None:
        patterns[idx].set_factors(values, ptype)

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "pattern_index": idx,
        "factors": len(values),
    }
