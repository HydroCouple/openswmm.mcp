"""Per-cell 2D infiltration tools for the OpenSWMM MCP server.

Surfaces the engine's ``Infiltration2DView`` (``surface2d.infiltration``,
C API ``openswmm_infil2d.h``) — the ``GROUNDWATER OFF`` loss model for the
2D overland-flow mesh, per plan §5.5. Covers the three input sections:

===============================  ==========================================
``[2D_INFILTRATION_OPTIONS]``    ``infil2d_get_options`` / ``set_options``
``[2D_INFILTRATION_DEFAULTS]``   ``infil2d_get_defaults`` / ``set_default``
                                 / ``remove_default``
``[2D_INFILTRATION]``            ``infil2d_get_cell`` / ``set_cell``
                                 / ``set_cells`` / ``clear_cells``
===============================  ==========================================

**Units are the trap.** Row parameters are in **project units** — the same
numbers a user types into a legacy ``[INFILTRATION]`` row (in/hr and in on a
US-``FLOW_UNITS`` project, mm/hr and mm on SI). The readback tools are SI,
like the rest of the 2D surface: rate is m/s, cumulative depth is m, total
volume is m³.

**Configuration is pre-run only.** Infiltration parameters are baked into
per-cell kernel state once, when the 2D surface initializes, and there is no
per-cell re-init path, so every ``set_*`` tool here is rejected by the engine
after ``initialize()``. Edit in the OPENED state, then initialize. There is
deliberately no mid-run state-seeding tool (plan §5.5.6).

All tools require the new ``openswmm`` engine backend and an OPENED or later
session. Bulk per-triangle results are returned as summary statistics plus an
optional ``offset`` / ``limit`` slice so responses stay LLM-friendly on large
meshes.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError

logger = logging.getLogger(__name__)

infil2d_mcp = FastMCP("infil2d")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OPEN_STATES = ("opened", "initialized", "running", "ended")

# Token <-> SWMM_INFIL2D_* method codes. The canonical spellings are the
# [2D_INFILTRATION*] file tokens; the engine enum's short names are accepted
# as aliases so either vocabulary works.
_METHODS: dict[str, int] = {
    "HORTON": 0,
    "MODIFIED_HORTON": 1,
    "MOD_HORTON": 1,
    "GREEN_AMPT": 2,
    "MODIFIED_GREEN_AMPT": 3,
    "MOD_GREEN_AMPT": 3,
    "CURVE_NUMBER": 4,
    "CURVE_NUM": 4,
    "CONSTANT": 5,
}
_METHOD_NAMES: dict[int, str] = {
    0: "HORTON",
    1: "MODIFIED_HORTON",
    2: "GREEN_AMPT",
    3: "MODIFIED_GREEN_AMPT",
    4: "CURVE_NUMBER",
    5: "CONSTANT",
}

# Token <-> SWMM_INFIL2D_DEST_* codes. D-I4: only LOST is routed in this
# release; the others are accepted by the grammar and rejected by the engine.
_DESTS: dict[str, int] = {
    "LOST": 0,
    "SUBCATCH_AQUIFER": 1,
    "AQUIFER_2D": 2,
}
_DEST_NAMES: dict[int, str] = {v: k for k, v in _DESTS.items()}

_MAX_PARAMS = 5


async def _get_infiltration(ctx: Context, session_id: str):
    """Return ``(session, infiltration_view)`` for an OPENED+ session."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "2D infiltration")
    require_state(session, *_OPEN_STATES)
    surface = session.surface2d
    active = await asyncio.to_thread(lambda: surface.is_active)
    if not active:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Session '{session_id}' has no "
            f"active 2D surface (the model carries no [2D_*] sections)."
        )
    return session, await asyncio.to_thread(lambda: surface.infiltration)


def _summarize(values) -> dict:
    """Summary statistics for a numeric array (JSON-native floats)."""
    n = len(values)
    if n == 0:
        return {"count": 0}
    return {
        "count": n,
        "min": float(values.min()),
        "max": float(values.max()),
        "mean": float(values.mean()),
    }


def _slice(values, offset: int, limit: int) -> list[float]:
    """A JSON-native slice of a numeric array."""
    if limit <= 0:
        return []
    return [float(v) for v in values[offset : offset + limit]]


def _resolve_method(method: str) -> int | None:
    """Map a method token to its code; ``None`` for the NONE token."""
    token = method.strip().upper()
    if token in ("", "NONE"):
        return None
    if token not in _METHODS:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid infiltration method "
            f"'{method}'. Must be NONE or one of: "
            f"{', '.join(sorted(set(_METHOD_NAMES.values())))}"
        )
    return _METHODS[token]


def _resolve_dest(dest: str) -> int:
    token = (dest or "LOST").strip().upper()
    if token not in _DESTS:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid destination '{dest}'. "
            f"Must be one of: {', '.join(_DESTS)}"
        )
    return _DESTS[token]


def _make_row(method: str, params: list[float] | None, dest: str):
    """Build an ``Infil2DRow`` from MCP-native arguments.

    The import is deferred because ``openswmm.engine._2d`` only exists on a
    build with ``OPENSWMM_BUILD_2D=ON`` — the same reason the twod tools reach
    the mesh through ``session.surface2d`` rather than importing it.
    """
    from openswmm.engine._2d import Infil2DRow  # noqa: PLC0415

    code = _resolve_method(method)
    if code is None:
        return Infil2DRow(None)

    values = list(params or [])
    if len(values) > _MAX_PARAMS:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] At most {_MAX_PARAMS} parameters, "
            f"got {len(values)}."
        )
    values += [0.0] * (_MAX_PARAMS - len(values))
    return Infil2DRow(code, tuple(float(v) for v in values),
                      _resolve_dest(dest))


def _row_dict(row) -> dict:
    """Render an ``Infil2DRow`` as a JSON-native dict."""
    if row.method is None:
        return {"method": "NONE", "params": [], "dest": "LOST"}
    return {
        "method": _METHOD_NAMES.get(int(row.method), str(int(row.method))),
        "params": [float(v) for v in row.params],
        "dest": _DEST_NAMES.get(int(row.dest), str(int(row.dest))),
    }


def _check_triangles(triangles: list[int] | None) -> list[int]:
    if not triangles:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Provide at least one triangle index."
        )
    return [int(t) for t in triangles]


# ---------------------------------------------------------------------------
# Options -- [2D_INFILTRATION_OPTIONS]
# ---------------------------------------------------------------------------


@infil2d_mcp.tool()
async def get_options(ctx: Context, session_id: str = "default") -> dict:
    """Return the 2D infiltration options.

    Reports ``infil_step``, the evaluation cadence in SECONDS. Infiltration
    is a *held rate*: it is recomputed on this cadence and held constant
    between updates, not re-evaluated every solver sub-step. A value of 0 (or
    less) means "use the project ``WET_STEP``", which the 2D surface resolves
    when it initializes — the value reported here is the authored one.
    """
    _, infil = await _get_infiltration(ctx, session_id)
    step = await asyncio.to_thread(lambda: infil.infil_step)
    return {
        "session_id": session_id,
        "infil_step": float(step),
        "uses_wet_step": bool(step <= 0.0),
    }


@infil2d_mcp.tool()
async def set_options(
    ctx: Context,
    session_id: str = "default",
    infil_step: float | None = None,
) -> dict:
    """Set the 2D infiltration evaluation cadence (seconds).

    Pass ``infil_step <= 0`` to fall back to the project ``WET_STEP``.
    Rejected once the solver has been initialized — edit in the OPENED state.
    """
    if infil_step is None:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide infil_step.")
    _, infil = await _get_infiltration(ctx, session_id)

    def _apply() -> dict:
        infil.infil_step = float(infil_step)
        return {"infil_step": float(infil.infil_step)}

    out = await asyncio.to_thread(_apply)
    out["status"] = "ok"
    out["session_id"] = session_id
    return out


# ---------------------------------------------------------------------------
# Tag defaults -- [2D_INFILTRATION_DEFAULTS]
# ---------------------------------------------------------------------------


@infil2d_mcp.tool()
async def get_defaults(ctx: Context, session_id: str = "default") -> dict:
    """List the authored tag-default infiltration rows.

    One entry per ``[2D_INFILTRATION_DEFAULTS]`` row. The tag ``"*"`` is the
    mesh-wide fallback; every other tag matches the ``TAG`` column of
    ``[2D_TRIANGLES]``. Resolution order, most specific wins:
    ``per-cell override > tag row > '*' row > no infiltration``.

    ``params`` is POSITIONAL and in PROJECT UNITS:
    HORTON / MODIFIED_HORTON ``[f0, fmin, decay(1/hr), dry_time(d), Fmax]``;
    GREEN_AMPT / MODIFIED_GREEN_AMPT ``[suction, Ks, IMD, -, -]``;
    CURVE_NUMBER ``[CN, -, dry_time(d), -, -]``; CONSTANT ``[rate, -, -, -, -]``.
    """
    # wraps: swmm_infil2d_defaults_count swmm_infil2d_get_default swmm_infil2d_get_default_tag
    _, infil = await _get_infiltration(ctx, session_id)

    def _read() -> dict:
        defaults = infil.defaults
        rows = []
        for tag in defaults:
            entry = {"tag": tag}
            entry.update(_row_dict(defaults[tag]))
            rows.append(entry)
        return {"count": len(rows), "defaults": rows}

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@infil2d_mcp.tool()
async def set_default(
    ctx: Context,
    session_id: str = "default",
    tag: str = "",
    method: str = "NONE",
    params: list[float] | None = None,
    dest: str = "LOST",
) -> dict:
    """Add or replace the infiltration default for a triangle tag.

    Upsert: a tag never ends up with two definitions. Use ``tag="*"`` for the
    mesh-wide fallback. ``method="NONE"`` is meaningful — on a tag it CLEARS
    the ``'*'`` default for that tag's cells.

    ``params`` is POSITIONAL and in PROJECT UNITS (see ``infil2d_get_defaults``
    for the per-method column layout). Only ``dest="LOST"`` is routed in this
    release. Rejected once the solver has been initialized.
    """
    # wraps: swmm_infil2d_set_default
    if not tag:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a tag.")
    _, infil = await _get_infiltration(ctx, session_id)

    def _apply() -> dict:
        infil.defaults[tag] = _make_row(method, params, dest)
        return _row_dict(infil.defaults[tag])

    out = await asyncio.to_thread(_apply)
    out["status"] = "ok"
    out["session_id"] = session_id
    out["tag"] = tag
    return out


@infil2d_mcp.tool()
async def remove_default(
    ctx: Context,
    session_id: str = "default",
    tag: str = "",
) -> dict:
    """Remove the infiltration default for a triangle tag.

    Removing ``"*"`` leaves cells with no matching tag row resolving to no
    infiltration at all. Rejected once the solver has been initialized.
    """
    # wraps: swmm_infil2d_remove_default
    if not tag:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a tag.")
    _, infil = await _get_infiltration(ctx, session_id)

    def _apply() -> bool:
        defaults = infil.defaults
        if tag not in defaults:
            return False
        del defaults[tag]
        return True

    removed = await asyncio.to_thread(_apply)
    return {
        "status": "ok",
        "session_id": session_id,
        "tag": tag,
        "removed": bool(removed),
    }


# ---------------------------------------------------------------------------
# Per-cell overrides -- [2D_INFILTRATION]
# ---------------------------------------------------------------------------


@infil2d_mcp.tool()
async def get_cell(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
) -> dict:
    """Return the infiltration specification in force at one triangle.

    After ``initialize()`` this is the RESOLVED row and ``is_override`` says
    whether it came from the per-cell ``[2D_INFILTRATION]`` layer. Before
    ``initialize()`` only the per-cell layer is visible, so a cell carrying no
    override reports ``method="NONE"`` even when a tag or ``'*'`` default would
    later apply — cross-check with ``infil2d_get_defaults`` and
    ``twod_get_triangle_tag`` in that state.

    ``params`` is in PROJECT UNITS.
    """
    _, infil = await _get_infiltration(ctx, session_id)

    def _read() -> dict:
        cell = infil.cell(int(triangle))
        out = {"triangle": int(triangle), "is_override": bool(cell.is_override)}
        out.update(_row_dict(cell.row))
        return out

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@infil2d_mcp.tool()
async def set_cell(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
    method: str = "NONE",
    params: list[float] | None = None,
    dest: str = "LOST",
) -> dict:
    """Set the per-cell ``[2D_INFILTRATION]`` override of one triangle.

    ``method="NONE"`` stores an explicit NONE override, which SUPPRESSES the
    tag and ``'*'`` defaults for that cell. To instead remove the override so
    the cell falls back to those defaults, use ``infil2d_clear_cells``.

    ``params`` is POSITIONAL and in PROJECT UNITS (see
    ``infil2d_get_defaults``). Rejected once the solver has been initialized.
    """
    _, infil = await _get_infiltration(ctx, session_id)

    def _apply() -> dict:
        infil.set_cell(int(triangle), _make_row(method, params, dest))
        return _row_dict(infil.cell(int(triangle)).row)

    out = await asyncio.to_thread(_apply)
    out["status"] = "ok"
    out["session_id"] = session_id
    out["triangle"] = int(triangle)
    return out


@infil2d_mcp.tool()
async def set_cells(
    ctx: Context,
    session_id: str = "default",
    triangles: list[int] | None = None,
    method: str = "NONE",
    params: list[float] | None = None,
    dest: str = "LOST",
) -> dict:
    """Assign one infiltration specification to many triangles at once.

    The select-many-then-assign entry point: one validation pass, then one
    apply. ALL-OR-NOTHING — if any index is out of range nothing at all is
    written. Duplicate indices are tolerated.

    ``params`` is POSITIONAL and in PROJECT UNITS (see
    ``infil2d_get_defaults``). Rejected once the solver has been initialized.
    """
    tris = _check_triangles(triangles)
    _, infil = await _get_infiltration(ctx, session_id)
    row = _make_row(method, params, dest)

    await asyncio.to_thread(infil.set_cells, tris, row)
    return {
        "status": "ok",
        "session_id": session_id,
        "triangle_count": len(tris),
        **_row_dict(row),
    }


@infil2d_mcp.tool()
async def clear_cells(
    ctx: Context,
    session_id: str = "default",
    triangles: list[int] | None = None,
) -> dict:
    """Remove the per-cell override from many triangles at once.

    The cleared cells fall back to their tag row / the ``'*'`` row / no
    infiltration. Distinct from ``infil2d_set_cell`` with ``method="NONE"``,
    which stores an explicit NONE that suppresses those defaults.
    ALL-OR-NOTHING, and rejected once the solver has been initialized.
    """
    tris = _check_triangles(triangles)
    _, infil = await _get_infiltration(ctx, session_id)

    await asyncio.to_thread(infil.set_cells, tris, None)
    return {
        "status": "ok",
        "session_id": session_id,
        "triangle_count": len(tris),
    }


# ---------------------------------------------------------------------------
# State readback (SI)
# ---------------------------------------------------------------------------


@infil2d_mcp.tool()
async def get_rate_bulk(
    ctx: Context,
    session_id: str = "default",
    offset: int = 0,
    limit: int = 0,
) -> dict:
    """Return the held per-cell infiltration rate (m/s) across the mesh.

    Summary statistics plus an optional ``[offset, offset+limit)`` slice.
    The rate is recomputed on the ``INFIL_STEP`` cadence and held constant
    between updates. A mesh with no resolved infiltration model reports all
    zeros — that is a valid configuration, not an error.
    """
    _, infil = await _get_infiltration(ctx, session_id)

    def _read() -> dict:
        values = infil.rate()
        return {
            "variable": "infil_rate",
            "units": "m/s",
            "summary": _summarize(values),
            "values": _slice(values, offset, limit),
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    out["offset"] = offset
    out["limit"] = limit
    return out


@infil2d_mcp.tool()
async def get_cum_bulk(
    ctx: Context,
    session_id: str = "default",
    offset: int = 0,
    limit: int = 0,
) -> dict:
    """Return the cumulative infiltrated depth per cell (m) across the mesh.

    Summary statistics plus an optional ``[offset, offset+limit)`` slice.
    This is the ``infil_cum`` sidecar variable — the running time-integral of
    the held rate. Zeros when no infiltration model is resolved.
    """
    _, infil = await _get_infiltration(ctx, session_id)

    def _read() -> dict:
        values = infil.cumulative()
        return {
            "variable": "infil_cum",
            "units": "m",
            "summary": _summarize(values),
            "values": _slice(values, offset, limit),
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    out["offset"] = offset
    out["limit"] = limit
    return out


@infil2d_mcp.tool()
async def get_total_volume(ctx: Context, session_id: str = "default") -> dict:
    """Return the cumulative 2D infiltration loss (m³).

    The ``infil_out`` mass-balance ledger row — the whole-domain companion to
    ``infil2d_get_cum_bulk``, reported beside ``evap_out`` in the continuity
    report. Requires the 2D mass balance to be live (same contract as
    ``twod_get_mass_balance``).
    """
    _, infil = await _get_infiltration(ctx, session_id)
    volume = await asyncio.to_thread(lambda: infil.total_volume)
    return {
        "session_id": session_id,
        "variable": "infil_out",
        "units": "m3",
        "total_volume": float(volume),
    }
