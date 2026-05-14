"""Programmatic model-construction tools for the OpenSWMM MCP server.

Provides tools for creating SWMM models from scratch using the
:class:`ModelBuilder` API -- adding nodes, links, subcatchments, gages,
time series, curves, and validating / exporting the result.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastmcp import Context, FastMCP
from openswmm.engine import ModelBuilder, Tables

from openswmm_mcp.backends.openswmm import OpenSwmmBackend
from openswmm_mcp.dependencies import get_session_manager, require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import BuildingResult
from openswmm_mcp.session import SimSession

logger = logging.getLogger(__name__)

building_mcp = FastMCP("building")


# ---------------------------------------------------------------------------
# Enum-mapping dicts (string -> int)
# ---------------------------------------------------------------------------

_NODE_TYPES: dict[str, int] = {
    "junction": 0,
    "outfall": 1,
    "storage": 2,
    "divider": 3,
}

_LINK_TYPES: dict[str, int] = {
    "conduit": 0,
    "pump": 1,
    "orifice": 2,
    "weir": 3,
    "outlet": 4,
}

_XSECT_SHAPES: dict[str, int] = {
    "circular": 0,
    "rect_closed": 1,
    "rect_open": 2,
    "trapezoidal": 3,
    "triangular": 4,
}

_CURVE_TYPES: dict[str, str] = {
    "storage": "STORAGE",
    "pump": "PUMP",
    "rating": "RATING",
    "diversion": "DIVERSION",
    "tidal": "TIDAL",
    "shape": "SHAPE",
    "weir": "WEIR",
    "control": "CONTROL",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resolve_node_type(name: str) -> int:
    """Map a human-readable node-type string to its engine enum value."""
    key = name.strip().lower()
    if key not in _NODE_TYPES:
        valid = ", ".join(sorted(_NODE_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown node_type '{name}'. Valid types: {valid}."
        )
    return _NODE_TYPES[key]


def _resolve_link_type(name: str) -> int:
    """Map a human-readable link-type string to its engine enum value."""
    key = name.strip().lower()
    if key not in _LINK_TYPES:
        valid = ", ".join(sorted(_LINK_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown link_type '{name}'. Valid types: {valid}."
        )
    return _LINK_TYPES[key]


def _resolve_xsect_shape(name: str) -> int:
    """Map a human-readable cross-section shape string to its engine enum value."""
    key = name.strip().lower()
    if key not in _XSECT_SHAPES:
        valid = ", ".join(sorted(_XSECT_SHAPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown xsect_shape '{name}'. Valid shapes: {valid}."
        )
    return _XSECT_SHAPES[key]


def _resolve_curve_type(name: str) -> str:
    """Map a human-readable curve-type string to its canonical name."""
    key = name.strip().lower()
    if key not in _CURVE_TYPES:
        valid = ", ".join(sorted(_CURVE_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown curve_type '{name}'. Valid types: {valid}."
        )
    return _CURVE_TYPES[key]


async def _get_builder_session(ctx: Context, session_id: str) -> SimSession:
    """Retrieve an existing session and verify it is in the ``building`` state."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Programmatic model building (ModelBuilder)")
    if session.state != "building":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{session.state}', but model-building tools require state 'building'."
        )
    if session.model_builder is None:
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no ModelBuilder attached."
        )
    return session


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@building_mcp.tool()
async def create_model(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Create an empty SWMM model and start a building session.

    Returns metadata for the new session.  Use the ``add_node``, ``add_link``,
    ``add_subcatchment``, and related tools to populate the model before
    calling ``validate_model`` or ``write_model``.
    """
    sm = get_session_manager(ctx)

    # Guard: session must not already exist
    async with sm._lock:
        if session_id in sm._sessions:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Session '{session_id}' already exists."
            )
        if len(sm._sessions) >= sm._max_sessions:
            raise ToolError(
                f"[{ErrorCode.MAX_SESSIONS_REACHED}] "
                f"Maximum number of sessions ({sm._max_sessions}) reached. "
                "Close an existing session first."
            )

        builder = await asyncio.to_thread(ModelBuilder)

        session_dir = sm._working_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        # The solver is not available until finalize(); pass a placeholder None
        # backend and set state to "building" so lifecycle tools know this is
        # not yet runnable.  ModelBuilder is openswmm-only, so engine_kind
        # implicitly defaults to "openswmm" for building sessions.
        session = SimSession(
            backend=None,
            state="building",
            working_dir=session_dir,
            model_builder=builder,
        )
        sm._sessions[session_id] = session

    logger.info("Building session '%s' created.", session_id)

    return {
        "status": "created",
        "session_id": session_id,
        "state": "building",
        "message": "Empty model created. Use add_node / add_link / add_subcatchment to populate.",
    }


@building_mcp.tool()
async def add_node(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
    node_type: str = "junction",
    invert_elev: float = 0.0,
    max_depth: float = 0.0,
    x: float | None = None,
    y: float | None = None,
) -> BuildingResult:
    """Add a node to the model being built.

    Parameters
    ----------
    node_id:
        Unique identifier for the node.
    node_type:
        One of ``junction``, ``outfall``, ``storage``, ``divider``.
    invert_elev:
        Invert elevation (ft or m depending on flow units).
    max_depth:
        Maximum depth above invert (0 = use default).
    x, y:
        Optional coordinate position for spatial display.
    """
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")

    type_code = _resolve_node_type(node_type)
    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    try:
        idx = await asyncio.to_thread(builder.add_node, node_id, type_code)
        await asyncio.to_thread(builder.set_node_invert, node_id, invert_elev)
        await asyncio.to_thread(builder.set_node_max_depth, node_id, max_depth)

        if x is not None and y is not None:
            try:
                spatial = session.spatial
                await asyncio.to_thread(spatial.set_node_coord, node_id, x, y)
            except Exception:
                logger.debug("Could not set coordinates for node '%s'", node_id, exc_info=True)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to add node '{node_id}': {exc}"
        ) from exc

    return BuildingResult(
        status="ok",
        element_type="node",
        element_id=node_id,
        index=idx,
        message=f"Added {node_type} node '{node_id}' at invert={invert_elev}.",
    )


@building_mcp.tool()
async def pop_last_node(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
) -> BuildingResult:
    """Remove the most recently added node (undo of ``add_node``).

    The supplied ``node_id`` must match the current tail of the node
    list. If any link still references the tail node, the engine
    refuses the pop — call ``pop_last_link`` for those links first.

    Parameters
    ----------
    node_id:
        Expected tail node identifier.
    """
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")

    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    rc = await asyncio.to_thread(builder.pop_last_node, node_id)
    if rc != 0:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] pop_last_node('{node_id}') failed with code {rc} "
            "(8 = SWMM_ERR_BADINDEX: id is not the current tail; "
            "anything else = engine-level lifecycle or reference error)."
        )

    return BuildingResult(
        status="ok",
        element_type="node",
        element_id=node_id,
        index=-1,
        message=f"Removed tail node '{node_id}'.",
    )


@building_mcp.tool()
async def add_link(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    link_type: str = "conduit",
    from_node: str = "",
    to_node: str = "",
    length: float = 100.0,
    roughness: float = 0.013,
    xsect_shape: str = "circular",
    xsect_geom1: float = 1.0,
    xsect_geom2: float = 0.0,
    xsect_geom3: float = 0.0,
    xsect_geom4: float = 0.0,
) -> BuildingResult:
    """Add a link (conduit, pump, orifice, weir, or outlet) to the model.

    Parameters
    ----------
    link_id:
        Unique identifier for the link.
    link_type:
        One of ``conduit``, ``pump``, ``orifice``, ``weir``, ``outlet``.
    from_node, to_node:
        IDs of the upstream and downstream nodes.
    length:
        Conduit length (ft or m).
    roughness:
        Manning's roughness coefficient.
    xsect_shape:
        Cross-section shape: ``circular``, ``rect_closed``, ``rect_open``,
        ``trapezoidal``, ``triangular``.
    xsect_geom1 .. xsect_geom4:
        Shape-dependent geometry parameters (e.g. diameter for circular).
    """
    if not link_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] link_id must not be empty.")
    if not from_node or not to_node:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] from_node and to_node must not be empty.")

    type_code = _resolve_link_type(link_type)
    shape_code = _resolve_xsect_shape(xsect_shape)
    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    try:
        idx = await asyncio.to_thread(builder.add_link, link_id, type_code)
        await asyncio.to_thread(builder.set_link_nodes, link_id, from_node, to_node)
        await asyncio.to_thread(builder.set_link_length, link_id, length)
        await asyncio.to_thread(builder.set_link_roughness, link_id, roughness)
        await asyncio.to_thread(
            builder.set_link_xsect,
            link_id,
            shape_code,
            xsect_geom1,
            xsect_geom2,
            xsect_geom3,
            xsect_geom4,
        )
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to add link '{link_id}': {exc}"
        ) from exc

    return BuildingResult(
        status="ok",
        element_type="link",
        element_id=link_id,
        index=idx,
        message=(
            f"Added {link_type} link '{link_id}' from '{from_node}' to '{to_node}' "
            f"({xsect_shape}, geom1={xsect_geom1})."
        ),
    )


@building_mcp.tool()
async def pop_last_link(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
) -> BuildingResult:
    """Remove the most recently added link (undo of ``add_link``).

    The supplied ``link_id`` must match the current tail of the link
    list, otherwise the engine returns ``SWMM_ERR_BADINDEX``.

    Parameters
    ----------
    link_id:
        Expected tail link identifier.
    """
    if not link_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] link_id must not be empty.")

    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    rc = await asyncio.to_thread(builder.pop_last_link, link_id)
    if rc != 0:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] pop_last_link('{link_id}') failed with code {rc} "
            "(8 = SWMM_ERR_BADINDEX: id is not the current tail)."
        )

    return BuildingResult(
        status="ok",
        element_type="link",
        element_id=link_id,
        index=-1,
        message=f"Removed tail link '{link_id}'.",
    )


@building_mcp.tool()
async def add_subcatchment(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str = "",
    area: float = 1.0,
    imperv_pct: float = 50.0,
    slope: float = 0.5,
    width: float = 100.0,
    outlet_node: str = "",
) -> BuildingResult:
    """Add a subcatchment to the model.

    Parameters
    ----------
    subcatch_id:
        Unique identifier for the subcatchment.
    area:
        Subcatchment area (acres or hectares).
    imperv_pct:
        Percent imperviousness (0-100).
    slope:
        Average surface slope (percent).
    width:
        Characteristic width for overland flow (ft or m).
    outlet_node:
        ID of the node (or another subcatchment) receiving runoff.
    """
    if not subcatch_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] subcatch_id must not be empty.")

    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    try:
        idx = await asyncio.to_thread(builder.add_subcatchment, subcatch_id)
        await asyncio.to_thread(builder.set_subcatch_area, subcatch_id, area)
        await asyncio.to_thread(builder.set_subcatch_slope, subcatch_id, slope)
        await asyncio.to_thread(builder.set_subcatch_width, subcatch_id, width)
        await asyncio.to_thread(builder.set_subcatch_imperv, subcatch_id, imperv_pct)

        if outlet_node:
            await asyncio.to_thread(builder.set_subcatch_outlet, subcatch_id, outlet_node)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to add subcatchment '{subcatch_id}': {exc}"
        ) from exc

    return BuildingResult(
        status="ok",
        element_type="subcatchment",
        element_id=subcatch_id,
        index=idx,
        message=(
            f"Added subcatchment '{subcatch_id}' "
            f"(area={area}, imperv={imperv_pct}%, slope={slope}%)."
        ),
    )


@building_mcp.tool()
async def add_gage(
    ctx: Context,
    session_id: str = "default",
    gage_id: str = "",
) -> BuildingResult:
    """Add a rain gage to the model.

    Parameters
    ----------
    gage_id:
        Unique identifier for the rain gage.
    """
    if not gage_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] gage_id must not be empty.")

    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    try:
        idx = await asyncio.to_thread(builder.add_gage, gage_id)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to add gage '{gage_id}': {exc}"
        ) from exc

    return BuildingResult(
        status="ok",
        element_type="gage",
        element_id=gage_id,
        index=idx,
        message=f"Added rain gage '{gage_id}'.",
    )


@building_mcp.tool()
async def set_option(
    ctx: Context,
    session_id: str = "default",
    option: str = "",
    value: str = "",
) -> dict:
    """Set a simulation option on the model being built.

    Parameters
    ----------
    option:
        Option name (e.g. ``FLOW_UNITS``, ``ROUTING_MODEL``, ``REPORT_STEP``).
    value:
        Option value as a string.
    """
    if not option:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] option must not be empty.")

    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    try:
        await asyncio.to_thread(builder.set_option, option, value)
    except AttributeError:
        # If ModelBuilder doesn't expose set_option directly, store for later
        if not hasattr(session, "_pending_options"):
            session._pending_options = {}  # type: ignore[attr-defined]
        session._pending_options[option] = value  # type: ignore[attr-defined]
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to set option '{option}' = '{value}': {exc}"
        ) from exc

    return {
        "status": "ok",
        "session_id": session_id,
        "option": option,
        "value": value,
        "message": f"Option '{option}' set to '{value}'.",
    }


@building_mcp.tool()
async def add_timeseries(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    times: list[float] | None = None,
    values: list[float] | None = None,
) -> dict:
    """Add a time series to the model.

    Parameters
    ----------
    name:
        Unique name for the time series.
    times:
        List of time values (hours from simulation start).
    values:
        List of corresponding data values (same length as *times*).
    """
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] name must not be empty.")
    if times is None or values is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Both 'times' and 'values' must be provided."
        )
    if len(times) != len(values):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] 'times' and 'values' must have the "
            f"same length (got {len(times)} and {len(values)})."
        )

    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    try:
        tables = Tables(builder)
        await asyncio.to_thread(tables.add_timeseries, name, times, values)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to add time series '{name}': {exc}"
        ) from exc

    return {
        "status": "ok",
        "session_id": session_id,
        "name": name,
        "points": len(times),
        "message": f"Added time series '{name}' with {len(times)} points.",
    }


@building_mcp.tool()
async def add_curve(
    ctx: Context,
    session_id: str = "default",
    name: str = "",
    curve_type: str = "storage",
    x_values: list[float] | None = None,
    y_values: list[float] | None = None,
) -> dict:
    """Add a curve to the model.

    Parameters
    ----------
    name:
        Unique name for the curve.
    curve_type:
        Curve type: ``storage``, ``pump``, ``rating``, ``diversion``,
        ``tidal``, ``shape``, ``weir``, ``control``.
    x_values:
        List of x-axis values.
    y_values:
        List of corresponding y-axis values (same length as *x_values*).
    """
    if not name:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] name must not be empty.")
    if x_values is None or y_values is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Both 'x_values' and 'y_values' must be provided."
        )
    if len(x_values) != len(y_values):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] 'x_values' and 'y_values' must have "
            f"the same length (got {len(x_values)} and {len(y_values)})."
        )

    ctype = _resolve_curve_type(curve_type)
    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    try:
        tables = Tables(builder)
        await asyncio.to_thread(tables.add_curve, name, ctype, x_values, y_values)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to add curve '{name}': {exc}") from exc

    return {
        "status": "ok",
        "session_id": session_id,
        "name": name,
        "curve_type": ctype,
        "points": len(x_values),
        "message": f"Added {curve_type} curve '{name}' with {len(x_values)} points.",
    }


@building_mcp.tool()
async def validate_model(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Validate the model being built.

    Runs the engine's built-in validation checks and returns any warnings
    or errors.  A model with no messages is considered valid.
    """
    session = await _get_builder_session(ctx, session_id)
    builder = session.model_builder

    try:
        messages = await asyncio.to_thread(builder.validate)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Validation failed: {exc}") from exc

    is_valid = len(messages) == 0

    return {
        "status": "valid" if is_valid else "warnings",
        "session_id": session_id,
        "valid": is_valid,
        "message_count": len(messages),
        "messages": messages,
    }


@building_mcp.tool()
async def write_model(
    ctx: Context,
    session_id: str = "default",
    output_path: str = "",
) -> dict:
    """Finalize and write the model to an ``.inp`` file.

    If the session is still in ``building`` state, the :class:`ModelBuilder` is
    finalized to produce a :class:`Solver`, which is then used to write the
    file.  If the session already has a solver (e.g. it was previously
    finalized), the existing solver writes the file directly.

    Parameters
    ----------
    output_path:
        Filesystem path for the output ``.inp`` file.
    """
    if not output_path:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] output_path must not be empty.")

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Writing models built by ModelBuilder")

    try:
        if session.state == "building":
            builder = session.model_builder
            if builder is None:
                raise ToolError(
                    f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no "
                    "ModelBuilder attached."
                )

            # Finalize the builder to get a solver, then wrap in a backend.
            solver = await asyncio.to_thread(builder.finalize)
            session.backend = OpenSwmmBackend.from_solver(solver)
            session.state = "created"

            # Apply any pending options that were deferred
            pending = getattr(session, "_pending_options", {})
            for opt, val in pending.items():
                try:
                    await asyncio.to_thread(solver.set_option, opt, val)
                except Exception:
                    logger.warning(
                        "Could not apply deferred option '%s'='%s'",
                        opt,
                        val,
                        exc_info=True,
                    )

            await asyncio.to_thread(solver.model_write, output_path)
        else:
            if session.backend is None:
                raise ToolError(
                    f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no solver."
                )
            await asyncio.to_thread(session.backend.solver.model_write, output_path)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to write model: {exc}") from exc

    resolved = str(Path(output_path).resolve())

    return {
        "status": "ok",
        "session_id": session_id,
        "path": resolved,
        "message": f"Model written to '{resolved}'.",
    }
