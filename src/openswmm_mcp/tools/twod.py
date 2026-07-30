"""2D overland-flow surface tools for the OpenSWMM MCP server.

Surfaces the engine's ``Surface2D`` view (``openswmm.engine._2d``) — mesh
queries, per-triangle state, statistics, mass balance, runtime forcing,
edge boundary conditions, and edge conveyance — for models that carry
``[2D_*]`` sections (or an external mesh file).

All tools require the new ``openswmm`` engine backend and an OPENED or
later session. Bulk per-triangle results are returned as summary
statistics plus an optional ``offset`` / ``limit`` slice so responses
stay LLM-friendly on large meshes.
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

twod_mcp = FastMCP("twod")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OPEN_STATES = ("opened", "initialized", "running", "ended")

# Token <-> openswmm.engine.SurfaceBoundaryType codes.
_BC_TYPES: dict[str, int] = {
    "WALL": 0,
    "NORMAL_FLOW": 1,
    "SPECIFIED_STAGE": 2,
    "SPECIFIED_FLOW": 3,
    "RATING_CURVE": 4,
}
_BC_TYPE_NAMES: dict[int, str] = {v: k for k, v in _BC_TYPES.items()}

# Token <-> openswmm.engine.SurfaceForcingMode codes (OVERRIDE=1, ADD=2).
_FORCING_MODES: dict[str, int] = {"replace": 1, "add": 2}


async def _get_surface(ctx: Context, session_id: str, *, require_active: bool = True):
    """Return ``(session, surface2d)`` for an OPENED+ new-engine session."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "2D surface routing")
    require_state(session, *_OPEN_STATES)
    surface = session.surface2d
    if require_active:
        active = await asyncio.to_thread(lambda: surface.is_active)
        if not active:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Session '{session_id}' has no "
                f"active 2D surface (the model carries no [2D_*] sections)."
            )
    return session, surface


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


def _resolve_forcing_mode(mode: str) -> int:
    key = mode.strip().lower()
    if key not in _FORCING_MODES:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid forcing mode '{mode}'. "
            f"Must be one of: {', '.join(_FORCING_MODES)}"
        )
    return _FORCING_MODES[key]


def _resolve_bc_type(bc_type: str) -> int:
    token = bc_type.strip().upper()
    if token not in _BC_TYPES:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid boundary type '{bc_type}'. "
            f"Must be one of: {', '.join(_BC_TYPES)}"
        )
    return _BC_TYPES[token]


def _check_edge(edge: int) -> None:
    if edge not in (0, 1, 2):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] edge must be 0, 1, or 2 (got {edge}).")


# ---------------------------------------------------------------------------
# Mesh queries
# ---------------------------------------------------------------------------


@twod_mcp.tool()
async def get_mesh_summary(ctx: Context, session_id: str = "default") -> dict:
    """Report whether the model has an active 2D surface and its mesh sizes.

    Returns ``active``, vertex / triangle counts, the number of boundary
    edges, and how many vertices / triangles are coupled to 1D nodes.
    Safe to call on any opened model — ``active`` is ``False`` when the
    model carries no ``[2D_*]`` sections.
    """
    _, surface = await _get_surface(ctx, session_id, require_active=False)

    def _read() -> dict:
        if not surface.is_active:
            return {"active": False}
        return {
            "active": True,
            "n_vertices": surface.n_vertices,
            "n_triangles": surface.n_triangles,
            "boundary_edge_count": surface.boundary_edge_count,
            "vertex_coupling_count": surface.vertex_coupling_count,
            "triangle_coupling_count": surface.triangle_coupling_count,
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@twod_mcp.tool()
async def get_mesh_geometry(
    ctx: Context,
    session_id: str = "default",
    offset: int = 0,
    limit: int = 50,
) -> dict:
    """Return mesh geometry for a window of triangles.

    For each triangle in ``[offset, offset+limit)``: its vertex indices,
    area, centroid, Manning's n, and the three neighbour triangle indices
    (-1 = boundary). Also reports vertex elevation summary statistics.
    Use ``twod_get_mesh_summary`` first to learn the mesh size.
    """
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        n_tri = surface.n_triangles
        _x, _y, z = surface.get_vertex_coords()
        tris = []
        for i in range(max(offset, 0), min(offset + max(limit, 0), n_tri)):
            cx, cy, cz = surface.get_triangle_centroid(i)
            tris.append(
                {
                    "index": i,
                    "vertices": [int(v) for v in surface.get_triangle_vertices(i)],
                    "area": float(surface.get_triangle_area(i)),
                    "centroid": [float(cx), float(cy), float(cz)],
                    "mannings_n": float(surface.get_triangle_mannings(i)),
                    "neighbours": [int(v) for v in surface.get_triangle_neighbours(i)],
                }
            )
        return {
            "n_triangles": n_tri,
            "n_vertices": surface.n_vertices,
            "vertex_z": _summarize(z),
            "triangles": tris,
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    out["offset"] = offset
    out["limit"] = limit
    return out


@twod_mcp.tool()
async def set_vertex_z(
    ctx: Context,
    session_id: str = "default",
    vertex: int = 0,
    z: float = 0.0,
) -> dict:
    """Set the ground elevation of a mesh vertex (m).

    Updates derived geometry for every triangle incident to the vertex.
    Useful for what-if terrain edits (berms, regrading) between steps.
    """
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(surface.set_vertex_z, vertex, z)
    return {"status": "ok", "session_id": session_id, "vertex": vertex, "z": z}


@twod_mcp.tool()
async def set_triangle_mannings(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
    n: float = 0.0,
) -> dict:
    """Set Manning's roughness for a 2D mesh triangle (must be > 0).

    Persists in the ``MANNINGS_N`` column of ``[2D_TRIANGLES]`` on save.
    """
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(surface.set_triangle_mannings, triangle, float(n))
    new_n = await asyncio.to_thread(surface.get_triangle_mannings, triangle)
    return {
        "status": "ok",
        "session_id": session_id,
        "triangle": triangle,
        "mannings_n": float(new_n),
    }


@twod_mcp.tool()
async def set_triangle_tag(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
    tag: str = "",
) -> dict:
    """Set the descriptive tag of a 2D triangle (``[2D_TRIANGLES]`` TAG).

    An empty string clears the tag.
    """
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(surface.set_triangle_tag, triangle, tag)
    new_tag = await asyncio.to_thread(surface.get_triangle_tag, triangle)
    return {
        "status": "ok",
        "session_id": session_id,
        "triangle": triangle,
        "tag": new_tag,
    }


@twod_mcp.tool()
async def get_triangle_tag(ctx: Context, session_id: str = "default", triangle: int = 0) -> dict:
    """Return the descriptive tag of a 2D triangle (empty if untagged)."""
    _, surface = await _get_surface(ctx, session_id)
    tag = await asyncio.to_thread(surface.get_triangle_tag, triangle)
    return {"session_id": session_id, "triangle": triangle, "tag": tag}


@twod_mcp.tool()
async def set_vertex_tag(
    ctx: Context,
    session_id: str = "default",
    vertex: int = 0,
    tag: str = "",
) -> dict:
    """Set the descriptive tag of a 2D vertex (``[2D_VERTICES]`` TAG).

    An empty string clears the tag. Distinct from the 1D<->2D coupling node.
    """
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(surface.set_vertex_tag, vertex, tag)
    new_tag = await asyncio.to_thread(surface.get_vertex_tag, vertex)
    return {
        "status": "ok",
        "session_id": session_id,
        "vertex": vertex,
        "tag": new_tag,
    }


@twod_mcp.tool()
async def get_vertex_tag(ctx: Context, session_id: str = "default", vertex: int = 0) -> dict:
    """Return the descriptive tag of a 2D vertex (empty if untagged)."""
    _, surface = await _get_surface(ctx, session_id)
    tag = await asyncio.to_thread(surface.get_vertex_tag, vertex)
    return {"session_id": session_id, "vertex": vertex, "tag": tag}


@twod_mcp.tool()
async def set_vertex_coupled_node(
    ctx: Context,
    session_id: str = "default",
    vertex: int = 0,
    node_name: str = "",
) -> dict:
    """Couple a 2D mesh vertex to a 1D SWMM node by name.

    Establishes the per-vertex 1D<->2D exchange point. Pass an empty string
    to clear the coupling.
    """
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(surface.set_vertex_coupled_node, vertex, node_name)
    new_node = await asyncio.to_thread(surface.get_vertex_coupled_node, vertex)
    return {
        "status": "ok",
        "session_id": session_id,
        "vertex": vertex,
        "node_index": int(new_node),
    }


@twod_mcp.tool()
async def get_coupling_map(ctx: Context, session_id: str = "default") -> dict:
    """List every 2D mesh entity coupled to a 1D node.

    Returns ``vertex_couplings`` (vertex index -> node index) and
    ``triangle_couplings`` (triangle index -> node index). These are the
    exchange points where the 2D surface trades flow with the drainage
    network.
    """
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        vertex_couplings = []
        for v in range(surface.n_vertices):
            node = surface.get_vertex_coupled_node(v)
            if node >= 0:
                vertex_couplings.append({"vertex": v, "node_index": int(node)})
        triangle_couplings = []
        for t in range(surface.n_triangles):
            node = surface.get_triangle_coupled_node(t)
            if node >= 0:
                triangle_couplings.append({"triangle": t, "node_index": int(node)})
        return {
            "vertex_couplings": vertex_couplings,
            "triangle_couplings": triangle_couplings,
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@twod_mcp.tool()
async def get_vertex_head(
    ctx: Context,
    session_id: str = "default",
    vertex: int = 0,
) -> dict:
    """Return the reconstructed water-surface head (m) at one mesh vertex.

    Triangle-based state is the solver's native representation; this is the
    per-vertex value reconstructed for rendering. Use
    ``twod_get_state_bulk`` with ``variable="vertex_head"`` to read every
    vertex at once.
    """
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        return {
            "vertex": vertex,
            "head": float(surface.get_vertex_head(vertex)),
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@twod_mcp.tool()
async def get_edge_geometry_bulk(
    ctx: Context,
    session_id: str = "default",
    offset: int = 0,
    limit: int = 0,
) -> dict:
    """Return time-invariant per-edge geometry for the whole mesh.

    For every triangle edge (indexed ``[tri*3 + edge]``) reports its length
    (m) and the outward unit-normal components ``nx`` / ``ny``. Returns
    summary statistics for each array plus the per-edge values in
    ``[offset, offset+limit)`` when ``limit`` > 0, each entry as
    ``{triangle, edge, length, nx, ny}``. Pairs with ``twod_get_state_bulk``
    (``variable="edge_flux"``), which shares the same ``[tri*3 + edge]``
    indexing.
    """
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        length, nx, ny = surface.get_edge_geometry_bulk()
        edges = []
        if limit > 0:
            stop = offset + limit
            for i in range(max(offset, 0), min(stop, len(length))):
                edges.append(
                    {
                        "triangle": int(i // 3),
                        "edge": int(i % 3),
                        "length": float(length[i]),
                        "nx": float(nx[i]),
                        "ny": float(ny[i]),
                    }
                )
        return {
            "length": _summarize(length),
            "nx": _summarize(nx),
            "ny": _summarize(ny),
            "edges": edges,
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    out["offset"] = offset
    out["limit"] = limit
    return out


@twod_mcp.tool()
async def get_state(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
) -> dict:
    """Return the current 2D state at one triangle.

    Reports depth (m), head (m), rainfall (m/s), net source (m/s), and the
    1D<->2D coupling flux (m3/s, positive = into the 2D surface).
    """
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        return {
            "triangle": triangle,
            "depth": float(surface.get_depth(triangle)),
            "head": float(surface.get_head(triangle)),
            "rainfall": float(surface.get_rainfall(triangle)),
            "net_source": float(surface.get_net_source(triangle)),
            "coupling_flux": float(surface.get_coupling_flux(triangle)),
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@twod_mcp.tool()
async def get_state_bulk(
    ctx: Context,
    session_id: str = "default",
    variable: str = "depth",
    offset: int = 0,
    limit: int = 0,
) -> dict:
    """Summarise a 2D state variable over the whole mesh.

    ``variable`` is one of ``"depth"``, ``"head"``, ``"vertex_head"``,
    ``"vertex_render_depth"``, ``"coupling_flux"``, or ``"edge_flux"``.
    Returns count / min / max / mean, plus the values in
    ``[offset, offset+limit)`` when ``limit`` > 0 (``edge_flux`` and the
    others are indexed per triangle except ``vertex_head`` and
    ``vertex_render_depth``, which are per vertex; ``edge_flux`` is ``[tri*3 +
    edge]``). ``vertex_render_depth`` is the render-oriented signed vertex
    water depth (``eta_v - z_v``) GUIs should interpolate for 2D
    water-surface rendering.
    """
    readers = {
        "depth": "get_depths",
        "head": "get_heads",
        "vertex_head": "get_vertex_heads",
        "vertex_render_depth": "get_vertex_render_depths",
        "coupling_flux": "get_coupling_fluxes",
        "edge_flux": "get_edge_flux_bulk",
    }
    key = variable.strip().lower()
    if key not in readers:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid variable '{variable}'. "
            f"Must be one of: {', '.join(readers)}"
        )
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        values = getattr(surface, readers[key])()
        return {
            "variable": key,
            "summary": _summarize(values),
            "values": _slice(values, offset, limit),
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    out["offset"] = offset
    out["limit"] = limit
    return out


@twod_mcp.tool()
async def get_totals(ctx: Context, session_id: str = "default") -> dict:
    """Return whole-surface totals and internal-stepper diagnostics.

    Reports max depth over the surface (m), total ponded volume (m3),
    total 1D<->2D exchange flow (m3/s), the explicit marcher's sub-step
    count for the last advance, and its last sub-step size (s).
    """
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        return {
            "max_depth": float(surface.max_depth),
            "total_volume": float(surface.total_volume),
            "total_exchange_flow": float(surface.total_exchange_flow),
            "solver_steps": int(surface.solver_steps),
            "solver_last_step": float(surface.solver_last_step),
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


# ---------------------------------------------------------------------------
# Statistics & mass balance
# ---------------------------------------------------------------------------


@twod_mcp.tool()
async def get_stats(
    ctx: Context,
    session_id: str = "default",
    top_n: int = 10,
) -> dict:
    """Return cumulative per-triangle statistics with worst-case hot spots.

    For max depth (m), max velocity magnitude (m/s), and max absolute
    continuity residual (m3/s): summary statistics plus the ``top_n`` triangles with
    the largest values (index + value), ranked descending.
    """
    _, surface = await _get_surface(ctx, session_id)

    def _top(values, n):
        order = values.argsort()[::-1][: max(n, 0)]
        return [{"triangle": int(i), "value": float(values[i])} for i in order]

    def _read() -> dict:
        depths = surface.get_stat_max_depths()
        velocities = surface.get_stat_max_velocities()
        residuals = surface.get_stat_max_continuity_err()
        return {
            "max_depth": {"summary": _summarize(depths), "top": _top(depths, top_n)},
            "max_velocity": {
                "summary": _summarize(velocities),
                "top": _top(velocities, top_n),
            },
            "max_continuity_err": {
                "summary": _summarize(residuals),
                "top": _top(residuals, top_n),
            },
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@twod_mcp.tool()
async def get_mass_balance(ctx: Context, session_id: str = "default") -> dict:
    """Return the global 2D mass-balance terms (m3) and continuity error.

    Terms: initial/final storage, rainfall in, 1D->2D coupling in,
    2D->1D coupling out, outfall in/out, evaporation out, boundary
    in/out, and the overall continuity error as a fraction of total
    inflow.
    """
    _, surface = await _get_surface(ctx, session_id)
    balance = await asyncio.to_thread(surface.get_mass_balance)
    return {
        "session_id": session_id,
        **{k: float(v) for k, v in balance.items()},
    }


# ---------------------------------------------------------------------------
# Forcing
# ---------------------------------------------------------------------------


@twod_mcp.tool()
async def force_rainfall(
    ctx: Context,
    session_id: str = "default",
    value: float = 0.0,
    triangle: int = -1,
    mode: str = "replace",
    persist: bool = False,
) -> dict:
    """Force rainfall on the 2D surface (m/s).

    ``triangle`` < 0 (the default) applies the rate uniformly to every
    triangle; otherwise only the given triangle is forced. ``mode`` is
    ``"replace"`` or ``"add"``; ``persist=True`` holds the forcing until
    cleared, otherwise it resets after one step.
    """
    mode_code = _resolve_forcing_mode(mode)
    _, surface = await _get_surface(ctx, session_id)

    def _apply() -> None:
        if triangle < 0:
            surface.force_rainfall_uniform(value, mode=mode_code, persist=int(persist))
        else:
            surface.force_rainfall(triangle, value, mode=mode_code, persist=int(persist))

    await asyncio.to_thread(_apply)
    return {
        "status": "ok",
        "session_id": session_id,
        "scope": "uniform" if triangle < 0 else f"triangle {triangle}",
        "value": value,
        "mode": mode.strip().lower(),
        "persist": persist,
    }


@twod_mcp.tool()
async def force_evap(
    ctx: Context,
    session_id: str = "default",
    value: float = 0.0,
    triangle: int = -1,
    mode: str = "replace",
    persist: bool = False,
) -> dict:
    """Force evaporation on the 2D surface (m/s).

    ``triangle`` < 0 (the default) applies the rate uniformly to every
    triangle; otherwise only the given triangle is forced. ``mode`` is
    ``"replace"`` or ``"add"``; ``persist=True`` holds the forcing until
    cleared, otherwise it resets after one step.
    """
    mode_code = _resolve_forcing_mode(mode)
    _, surface = await _get_surface(ctx, session_id)

    def _apply() -> None:
        if triangle < 0:
            surface.force_evap_uniform(value, mode=mode_code, persist=int(persist))
        else:
            surface.force_evap(triangle, value, mode=mode_code, persist=int(persist))

    await asyncio.to_thread(_apply)
    return {
        "status": "ok",
        "session_id": session_id,
        "scope": "uniform" if triangle < 0 else f"triangle {triangle}",
        "value": value,
        "mode": mode.strip().lower(),
        "persist": persist,
    }


@twod_mcp.tool()
async def force_coupling_flux(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
    value: float = 0.0,
    mode: str = "replace",
    persist: bool = False,
) -> dict:
    """Force the 1D<->2D coupling flux on a triangle (m/s, positive = into 2D).

    ``mode`` is ``"replace"`` or ``"add"``; ``persist=True`` holds the
    forcing until cleared, otherwise it resets after one step.
    """
    mode_code = _resolve_forcing_mode(mode)
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(
        lambda: surface.force_coupling_flux(triangle, value, mode=mode_code, persist=int(persist))
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "triangle": triangle,
        "value": value,
        "mode": mode.strip().lower(),
        "persist": persist,
    }


@twod_mcp.tool()
async def force_clear(ctx: Context, session_id: str = "default") -> dict:
    """Clear every 2D forcing override (rainfall and coupling flux)."""
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(surface.force_clear_all)
    return {"status": "cleared", "session_id": session_id}


# ---------------------------------------------------------------------------
# Solver parameters
# ---------------------------------------------------------------------------


@twod_mcp.tool()
async def get_solver_params(ctx: Context, session_id: str = "default") -> dict:
    """Return the 2D solver parameters.

    Reports the dry-depth threshold (m). The explicit-marcher
    configuration (THETA, CFL_NUMBER, LTS_TIERS, H_MOVE, FROUDE_MAX,
    COUPLING_AREA, ...) lives in ``[2D_OPTIONS]`` and is read with
    ``model_get_option_ext``. The retired CVODE tolerances no longer
    exist.
    """
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        return {
            "dry_depth": float(surface.dry_depth),
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@twod_mcp.tool()
async def set_solver_params(
    ctx: Context,
    session_id: str = "default",
    dry_depth: float | None = None,
) -> dict:
    """Set 2D solver parameters; omitted parameters are left unchanged.

    ``dry_depth`` is the wet/dry threshold (m). The explicit-marcher
    configuration (THETA, CFL_NUMBER, LTS_TIERS, H_MOVE, FROUDE_MAX,
    COUPLING_AREA, ...) lives in ``[2D_OPTIONS]`` and is set with
    ``model_set_option_ext``. The retired CVODE tolerances no longer
    exist.
    """
    if dry_depth is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Provide dry_depth."
        )
    _, surface = await _get_surface(ctx, session_id)

    def _apply() -> dict:
        surface.dry_depth = dry_depth
        return {
            "dry_depth": float(surface.dry_depth),
        }

    out = await asyncio.to_thread(_apply)
    out["status"] = "ok"
    out["session_id"] = session_id
    return out


# ---------------------------------------------------------------------------
# Edge boundary conditions
# ---------------------------------------------------------------------------


@twod_mcp.tool()
async def get_edge_bc(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
    edge: int = 0,
) -> dict:
    """Return the boundary condition on one triangle edge.

    Reports the BC type (WALL / NORMAL_FLOW / SPECIFIED_STAGE /
    SPECIFIED_FLOW / RATING_CURVE), the constant head and slope, the
    prescribed per-metre flow, and the cumulative flux through the edge.
    """
    _check_edge(edge)
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        bc_code = int(surface.get_edge_bc_type(triangle, edge))
        return {
            "triangle": triangle,
            "edge": edge,
            "bc_type": _BC_TYPE_NAMES.get(bc_code, str(bc_code)),
            "head": float(surface.get_edge_bc_head(triangle, edge)),
            "slope": float(surface.get_edge_bc_slope(triangle, edge)),
            "flow": float(surface.get_edge_bc_flow(triangle, edge)),
            "cum_flux": float(surface.get_edge_bc_cum_flux(triangle, edge)),
        }

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@twod_mcp.tool()
async def set_edge_bc(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
    edge: int = 0,
    bc_type: str = "",
    head: float | None = None,
    slope: float | None = None,
    flow: float | None = None,
    tseries_name: str | None = None,
    flow_tseries_name: str | None = None,
    rating_curve_name: str | None = None,
) -> dict:
    """Configure the boundary condition on one triangle edge.

    ``bc_type`` (optional) is WALL, NORMAL_FLOW, SPECIFIED_STAGE,
    SPECIFIED_FLOW, or RATING_CURVE. The remaining parameters apply only
    when provided: ``head`` (constant stage, m), ``slope`` (NORMAL_FLOW bed
    slope), ``flow`` (per-metre discharge, m3/s/m), ``tseries_name``
    (stage timeseries; "" clears), ``flow_tseries_name`` (flow timeseries;
    "" clears), ``rating_curve_name`` (stage-to-flow curve; "" clears).
    """
    _check_edge(edge)
    if (
        not bc_type
        and head is None
        and slope is None
        and flow is None
        and tseries_name is None
        and flow_tseries_name is None
        and rating_curve_name is None
    ):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Provide bc_type and/or at least one BC parameter."
        )
    bc_code = _resolve_bc_type(bc_type) if bc_type else None
    _, surface = await _get_surface(ctx, session_id)

    def _apply() -> None:
        if bc_code is not None:
            surface.set_edge_bc_type(triangle, edge, bc_code)
        if head is not None:
            surface.set_edge_bc_head(triangle, edge, head)
        if slope is not None:
            surface.set_edge_bc_slope(triangle, edge, slope)
        if flow is not None:
            surface.set_edge_bc_flow(triangle, edge, flow)
        if tseries_name is not None:
            surface.set_edge_bc_tseries_name(triangle, edge, tseries_name)
        if flow_tseries_name is not None:
            surface.set_edge_bc_flow_tseries_name(triangle, edge, flow_tseries_name)
        if rating_curve_name is not None:
            surface.set_edge_bc_rating_curve_name(triangle, edge, rating_curve_name)

    await asyncio.to_thread(_apply)
    return {
        "status": "ok",
        "session_id": session_id,
        "triangle": triangle,
        "edge": edge,
        "bc_type": bc_type.strip().upper() if bc_type else None,
    }


# ---------------------------------------------------------------------------
# Edge conveyance
# ---------------------------------------------------------------------------


@twod_mcp.tool()
async def get_edge_conveyance(
    ctx: Context,
    session_id: str = "default",
    triangle: int = -1,
    edge: int = 0,
) -> dict:
    """Read per-edge conveyance factors (1.0 = unrestricted, 0.0 = wall).

    With ``triangle`` >= 0 returns the single factor at (triangle, edge);
    with ``triangle`` < 0 (default) returns a whole-mesh summary plus the
    list of restricted edges (factor < 1), so berms / barriers are easy to
    spot.
    """
    _, surface = await _get_surface(ctx, session_id)

    def _read() -> dict:
        if triangle >= 0:
            _check_edge(edge)
            return {
                "triangle": triangle,
                "edge": edge,
                "conveyance": float(surface.get_edge_conveyance(triangle, edge)),
            }
        values = surface.get_edge_conveyance_bulk()
        restricted = [
            {"triangle": int(i // 3), "edge": int(i % 3), "conveyance": float(v)}
            for i, v in enumerate(values)
            if v < 1.0
        ]
        return {"summary": _summarize(values), "restricted_edges": restricted}

    out = await asyncio.to_thread(_read)
    out["session_id"] = session_id
    return out


@twod_mcp.tool()
async def set_edge_conveyance(
    ctx: Context,
    session_id: str = "default",
    triangle: int = 0,
    edge: int = 0,
    conveyance: float = 1.0,
) -> dict:
    """Set the conveyance factor on one triangle edge (in [0, 1]).

    0.0 makes the edge a wall; 1.0 leaves it unrestricted. Interior edges
    mirror the value to the neighbouring triangle's partner slot so mass
    conservation is preserved. Apply between routing steps.
    """
    _check_edge(edge)
    if not 0.0 <= conveyance <= 1.0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] conveyance must be in [0, 1] (got {conveyance})."
        )
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(surface.set_edge_conveyance, triangle, edge, conveyance)
    return {
        "status": "ok",
        "session_id": session_id,
        "triangle": triangle,
        "edge": edge,
        "conveyance": conveyance,
    }


@twod_mcp.tool()
async def reset_edge_conveyance(ctx: Context, session_id: str = "default") -> dict:
    """Reset every edge's conveyance factor to 1.0 (unrestricted)."""
    _, surface = await _get_surface(ctx, session_id)
    await asyncio.to_thread(surface.reset_edge_conveyance)
    return {"status": "reset", "session_id": session_id}
