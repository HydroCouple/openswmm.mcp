"""Cross-section geometry tools: analytic hydraulic geometry for any shape.

Wraps ``openswmm.engine.XSectionGeometry`` -- the same geometry kernels the
routing solvers use, so answers agree with a simulation exactly. This answers
questions the rest of the tool surface cannot: *what depth carries 15 cfs in
this conduit*, *what is the critical depth at this section*, *what is the real
filling ratio of a box culvert*.

**Every tool is stateless.** A section is built, queried and released within
the call -- no handle is ever stored in the session, so an agent cannot leak
one. Each query tool names its section in one of two ways:

* ``link_id`` -- the geometry the engine actually built for a link of the open
  model, transect tables and all (units come from the model); or
* ``shape`` + ``geom1``..``geom4`` + ``units`` -- a standalone section, no
  model required. Call ``list_shapes`` for the accepted names.

The tabulated shapes (IRREGULAR / CUSTOM / STREET_XSECT) carry no inline
geometry, so they cannot be named by ``shape``; build them with
``properties_from_transect`` / ``properties_from_curve`` /
``properties_from_street``, or reach them through ``link_id``.

Every query tool accepts a single value or a list. A list dispatches to one
batched C call -- pass the whole rating curve rather than looping.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP
from openswmm.engine import XSectionGeometry, shape_name

from openswmm_mcp._util.formatting import ndarray_to_list
from openswmm_mcp._util.xsect_shapes import resolve_shape, shape_codes
from openswmm_mcp.dependencies import get_session_manager, require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
from openswmm_mcp.session import SimSession

xsect_mcp = FastMCP("xsect")


# ---------------------------------------------------------------------------
# Shape / unit maps
# ---------------------------------------------------------------------------
# ``_util.xsect_shapes`` derives the map from the engine's own ``XSectShape``
# so the codes can never drift; ``editing`` and ``building`` share it.

_resolve_shape = resolve_shape


def _resolve_units(units: str) -> str:
    key = units.strip().upper()
    if key not in ("US", "SI"):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] units must be 'US' or 'SI'; got {units!r}. "
            "There is no default -- an assumed unit system is a silently wrong answer."
        )
    return key


# ---------------------------------------------------------------------------
# Session + geometry helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Cross-section geometry")
    return session


def _identity(xs: XSectionGeometry) -> dict:
    """The shape / unit block echoed by every tool."""
    code = int(xs.shape)
    return {
        "shape": shape_name(code).lower(),
        "shape_code": code,
        "units": xs.units,
        "flow_units": xs.flow_units,
    }


def _full_properties(xs: XSectionGeometry) -> dict:
    return {
        **_identity(xs),
        "full_depth": xs.full_depth,
        "full_area": xs.full_area,
        "full_hyd_radius": xs.full_hyd_radius,
        "max_width": xs.max_width,
        "full_section_factor": xs.full_section_factor,
        "max_area": xs.max_area,
        "is_open": xs.is_open,
    }


async def _geometry(
    ctx: Context,
    session_id: str,
    link_id: str | int,
    shape: str,
    geom1: float,
    geom2: float,
    geom3: float,
    geom4: float,
    units: str,
) -> tuple[XSectionGeometry, dict]:
    """Build a section from a link or an explicit shape spec.

    The section is local to the calling tool -- nothing is stored in the
    session, so it is released as soon as the call returns.
    """
    if link_id != "" and shape:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Give either link_id or shape, not both.")
    if link_id != "":
        session = await _get_session(ctx, session_id)
        idx = await resolve_index(session.links, link_id, "Link")
        xs = await asyncio.to_thread(lambda: XSectionGeometry.from_link(session.links[idx]))
        return xs, {"source": "link", "link_id": link_id, "link_index": idx}

    if not shape:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Supply either link_id (a link of the open "
            f"model) or shape + geom1..geom4 + units. "
            f"Valid shapes: {', '.join(sorted(shape_codes()))}."
        )
    code = _resolve_shape(shape)
    unit_system = _resolve_units(units)
    try:
        xs = await asyncio.to_thread(
            XSectionGeometry,
            code,
            float(geom1),
            float(geom2),
            float(geom3),
            float(geom4),
            units=unit_system,
        )
    except ValueError as exc:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {exc}") from exc
    return xs, {
        "source": "shape",
        "geom1": geom1,
        "geom2": geom2,
        "geom3": geom3,
        "geom4": geom4,
    }


async def _query(
    ctx: Context,
    session_id: str,
    *,
    method: str,
    values: float | list[float],
    input_key: str,
    result_key: str,
    link_id: str | int,
    shape: str,
    geom1: float,
    geom2: float,
    geom3: float,
    geom4: float,
    units: str,
) -> dict:
    """Build the section, evaluate *method*, return the result."""
    xs, source = await _geometry(ctx, session_id, link_id, shape, geom1, geom2, geom3, geom4, units)

    def _run() -> tuple[Any, dict]:
        fn = getattr(xs, method)
        if isinstance(values, (list, tuple)):
            # One batched C call -- this is what the ``*_array`` variants are for.
            out: Any = ndarray_to_list(fn(list(values)))
        else:
            out = float(fn(float(values)))
        return out, _identity(xs)

    try:
        result, identity = await asyncio.to_thread(_run)
    except ValueError as exc:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {exc}") from exc

    return {
        "session_id": session_id,
        **source,
        **identity,
        input_key: values,
        result_key: result,
    }


def _require_points(values: list[float] | None, name: str) -> list[float]:
    if not values:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {name} must not be empty.")
    return [float(v) for v in values]


# ===========================================================================
# Metadata
# ===========================================================================


@xsect_mcp.tool()
async def list_shapes(ctx: Context, session_id: str = "default") -> dict:
    """List every cross-section shape name accepted by the ``shape`` argument.

    IRREGULAR / CUSTOM / STREET_XSECT are tabulated shapes: they carry no
    inline geometry and must be built with ``properties_from_transect`` /
    ``properties_from_curve`` / ``properties_from_street``, or reached through
    ``link_id``.
    """
    # wraps: swmm_xsect_shape_name
    shapes = await asyncio.to_thread(
        lambda: [
            {"name": shape_name(c).lower(), "code": c}
            for c in sorted(set(shape_codes().values()))
        ]
    )
    return {"session_id": session_id, "count": len(shapes), "shapes": shapes}


@xsect_mcp.tool()
async def properties(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
) -> dict:
    """Return a section's full geometric properties.

    Full depth / area / hydraulic radius / section factor, the maximum width
    and area, whether the section is open to the atmosphere, and its unit
    system. Name the section with ``link_id`` or with ``shape`` +
    ``geom1``..``geom4`` + ``units``.
    """
    # wraps: swmm_xsect_create swmm_xsect_free swmm_link_create_xsect swmm_xsect_full_properties swmm_xsect_get_shape swmm_xsect_get_units swmm_xsect_is_open  # noqa: E501
    xs, source = await _geometry(ctx, session_id, link_id, shape, geom1, geom2, geom3, geom4, units)
    props = await asyncio.to_thread(_full_properties, xs)
    return {"session_id": session_id, **source, **props}


# ===========================================================================
# Tabulated-shape constructors
# ===========================================================================


@xsect_mcp.tool()
async def properties_from_transect(
    ctx: Context,
    session_id: str = "default",
    stations: list[float] | None = None,
    elevations: list[float] | None = None,
    left_bank: float = 0.0,
    right_bank: float = 0.0,
    n_channel: float = 0.0,
    n_left: float = 0.0,
    n_right: float = 0.0,
    length_factor: float = 1.0,
    units: str = "",
) -> dict:
    """Build an irregular (natural channel) section from transect data.

    Mirrors a ``[TRANSECTS]`` entry and returns the resulting section's full
    properties. ``n_channel`` must be > 0; ``n_left`` / ``n_right`` default to
    ``n_channel`` when left at 0. Pass the same value for ``left_bank`` and
    ``right_bank`` for a channel with no overbanks.
    """
    # wraps: swmm_xsect_create_irregular
    st = _require_points(stations, "stations")
    el = _require_points(elevations, "elevations")
    unit_system = _resolve_units(units)
    try:
        xs = await asyncio.to_thread(
            lambda: XSectionGeometry.from_transect(
                st,
                el,
                left_bank=float(left_bank),
                right_bank=float(right_bank),
                n_channel=float(n_channel),
                n_left=float(n_left),
                n_right=float(n_right),
                length_factor=float(length_factor),
                units=unit_system,
            )
        )
    except ValueError as exc:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {exc}") from exc
    props = await asyncio.to_thread(_full_properties, xs)
    return {
        "session_id": session_id,
        "source": "transect",
        "station_count": len(st),
        **props,
    }


@xsect_mcp.tool()
async def properties_from_curve(
    ctx: Context,
    session_id: str = "default",
    full_depth: float = 0.0,
    curve_depths: list[float] | None = None,
    curve_widths: list[float] | None = None,
    units: str = "",
) -> dict:
    """Build a custom section from a normalized shape curve.

    Mirrors a ``SHAPE``-type ``[CURVES]`` entry scaled to ``full_depth``:
    ``curve_depths`` are y/yFull in [0, 1] ascending, ``curve_widths`` the
    matching w/wMax. Returns the resulting section's full properties.
    """
    # wraps: swmm_xsect_create_custom
    depths = _require_points(curve_depths, "curve_depths")
    widths = _require_points(curve_widths, "curve_widths")
    unit_system = _resolve_units(units)
    try:
        xs = await asyncio.to_thread(
            lambda: XSectionGeometry.from_curve(
                float(full_depth), depths, widths, units=unit_system
            )
        )
    except ValueError as exc:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {exc}") from exc
    props = await asyncio.to_thread(_full_properties, xs)
    return {
        "session_id": session_id,
        "source": "curve",
        "point_count": len(depths),
        **props,
    }


@xsect_mcp.tool()
async def properties_from_street(
    ctx: Context,
    session_id: str = "default",
    width: float = 0.0,
    curb_height: float = 0.0,
    slope: float = 0.0,
    roughness: float = 0.0,
    gutter_depression: float = 0.0,
    gutter_width: float = 0.0,
    sides: int = 2,
    back_width: float = 0.0,
    back_slope: float = 0.0,
    back_roughness: float = 0.0,
    units: str = "",
) -> dict:
    """Build a street section. Mirrors a ``[STREETS]`` entry.

    ``width`` is curb-to-crown, ``slope`` and ``back_slope`` are in percent,
    ``sides`` is 1 (half street) or 2 (full street). Returns the resulting
    section's full properties.
    """
    # wraps: swmm_xsect_create_street
    unit_system = _resolve_units(units)
    try:
        xs = await asyncio.to_thread(
            lambda: XSectionGeometry.from_street(
                float(width),
                float(curb_height),
                float(slope),
                float(roughness),
                gutter_depression=float(gutter_depression),
                gutter_width=float(gutter_width),
                sides=int(sides),
                back_width=float(back_width),
                back_slope=float(back_slope),
                back_roughness=float(back_roughness),
                units=unit_system,
            )
        )
    except ValueError as exc:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {exc}") from exc
    props = await asyncio.to_thread(_full_properties, xs)
    return {"session_id": session_id, "source": "street", **props}


# ===========================================================================
# Queries -- depth in
# ===========================================================================


@xsect_mcp.tool()
async def area_of_depth(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    depth: float | list[float] = 0.0,
) -> dict:
    """Flow area at a depth, or at a list of depths in one batched call."""
    # wraps: swmm_xsect_area_of_depth swmm_xsect_area_of_depth_array
    return await _query(
        ctx,
        session_id,
        method="area",
        values=depth,
        input_key="depth",
        result_key="area",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )


@xsect_mcp.tool()
async def width_of_depth(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    depth: float | list[float] = 0.0,
) -> dict:
    """Top width of the water surface at a depth, or at a list of depths."""
    # wraps: swmm_xsect_width_of_depth swmm_xsect_width_of_depth_array
    return await _query(
        ctx,
        session_id,
        method="width",
        values=depth,
        input_key="depth",
        result_key="width",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )


@xsect_mcp.tool()
async def hydrad_of_depth(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    depth: float | list[float] = 0.0,
) -> dict:
    """Hydraulic radius (area / wetted perimeter) at a depth or list of depths."""
    # wraps: swmm_xsect_hydrad_of_depth swmm_xsect_hydrad_of_depth_array
    return await _query(
        ctx,
        session_id,
        method="hyd_radius",
        values=depth,
        input_key="depth",
        result_key="hyd_radius",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )


# ===========================================================================
# Queries -- area in
# ===========================================================================


@xsect_mcp.tool()
async def depth_of_area(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    area: float | list[float] = 0.0,
) -> dict:
    """Depth of flow for a given area -- the inverse of ``area_of_depth``."""
    # wraps: swmm_xsect_depth_of_area swmm_xsect_depth_of_area_array
    return await _query(
        ctx,
        session_id,
        method="depth_from_area",
        values=area,
        input_key="area",
        result_key="depth",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )


@xsect_mcp.tool()
async def hydrad_of_area(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    area: float | list[float] = 0.0,
) -> dict:
    """Hydraulic radius for a given flow area, or a list of areas."""
    # wraps: swmm_xsect_hydrad_of_area swmm_xsect_hydrad_of_area_array
    return await _query(
        ctx,
        session_id,
        method="hyd_radius_from_area",
        values=area,
        input_key="area",
        result_key="hyd_radius",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )


@xsect_mcp.tool()
async def sectfactor_of_area(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    area: float | list[float] = 0.0,
) -> dict:
    """Section factor (A*R^(2/3)) for a given flow area, or a list of areas."""
    # wraps: swmm_xsect_sectfactor_of_area swmm_xsect_sectfactor_of_area_array
    return await _query(
        ctx,
        session_id,
        method="section_factor",
        values=area,
        input_key="area",
        result_key="section_factor",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )


@xsect_mcp.tool()
async def dsda(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    area: float | list[float] = 0.0,
) -> dict:
    """Derivative of the section factor with respect to area (dS/dA)."""
    # wraps: swmm_xsect_dsda swmm_xsect_dsda_array
    return await _query(
        ctx,
        session_id,
        method="dsda",
        values=area,
        input_key="area",
        result_key="dsda",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )


# ===========================================================================
# Queries -- section factor / flow in
# ===========================================================================


@xsect_mcp.tool()
async def area_of_sectfactor(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    section_factor: float | list[float] = 0.0,
) -> dict:
    """Flow area for a section factor -- the step that solves for normal depth."""
    # wraps: swmm_xsect_area_of_sectfactor swmm_xsect_area_of_sectfactor_array
    return await _query(
        ctx,
        session_id,
        method="area_from_section_factor",
        values=section_factor,
        input_key="section_factor",
        result_key="area",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )


@xsect_mcp.tool()
async def critical_depth(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shape: str = "",
    geom1: float = 0.0,
    geom2: float = 0.0,
    geom3: float = 0.0,
    geom4: float = 0.0,
    units: str = "",
    flow: float | list[float] = 0.0,
) -> dict:
    """Critical depth for a flow, or for a list of flows.

    ``flow`` is in the section's ``flow_units`` (echoed in the response).
    """
    # wraps: swmm_xsect_critical_depth swmm_xsect_critical_depth_array
    return await _query(
        ctx,
        session_id,
        method="critical_depth",
        values=flow,
        input_key="flow",
        result_key="critical_depth",
        link_id=link_id,
        shape=shape,
        geom1=geom1,
        geom2=geom2,
        geom3=geom3,
        geom4=geom4,
        units=units,
    )
