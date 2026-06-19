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
from openswmm.engine import ModelBuilder, Pollutants, Tables

from openswmm_mcp.backends.openswmm import OpenSwmmBackend
from openswmm_mcp.dependencies import get_session_manager, require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
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

# v1 ``Tables.add_curve(name, curve_type_int)`` takes the integer CurveType
# enum code.  The mapping matches the SWMM 5.x source-order constants.
_CURVE_TYPES: dict[str, int] = {
    "storage": 0,
    "diversion": 1,
    "tidal": 2,
    "rating": 3,
    "control": 4,
    "shape": 5,
    "weir": 6,
    "pump": 7,
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


def _resolve_curve_type(name: str) -> int:
    """Map a human-readable curve-type string to its v1 integer enum code."""
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

    from openswmm.engine import Nodes

    def _add() -> int:
        # builder.add_node returns the SWMM error code (0 on success), not
        # the new node's index — resolve via Nodes(builder).get_index.
        # set_node_invert / set_node_max_depth are kept as ModelBuilder
        # methods in v1.
        rc = builder.add_node(node_id, type_code)
        if rc != 0:
            raise RuntimeError(f"add_node returned error code {rc}")
        new_idx = Nodes(builder).get_index(node_id)
        if new_idx < 0:
            raise RuntimeError(
                f"add_node returned success but node '{node_id}' could not be resolved by id."
            )
        builder.set_node_invert(new_idx, invert_elev)
        builder.set_node_max_depth(new_idx, max_depth)
        return new_idx

    try:
        idx = await asyncio.to_thread(_add)

        if x is not None and y is not None:
            try:
                spatial = session.spatial
                await asyncio.to_thread(spatial.set_node_coord, idx, x, y)
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
        # Resolve from/to node indices upfront (outside the worker).
        from openswmm.engine import Links, Nodes

        nodes_accessor = Nodes(builder)
        from_idx = await resolve_index(nodes_accessor, from_node, "Node")
        if from_idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] from_node '{from_node}' not found.")
        to_idx = await resolve_index(nodes_accessor, to_node, "Node")
        if to_idx < 0:
            raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] to_node '{to_node}' not found.")

        def _add() -> int:
            # builder.add_link returns SWMM error code (0=ok), not the index.
            rc = builder.add_link(link_id, type_code)
            if rc != 0:
                raise RuntimeError(f"add_link returned error code {rc}")
            new_idx = Links(builder).get_index(link_id)
            if new_idx < 0:
                raise RuntimeError(
                    f"add_link returned success but link '{link_id}' could not be resolved."
                )
            builder.set_link_nodes(new_idx, from_idx, to_idx)
            builder.set_link_length(new_idx, length)
            builder.set_link_roughness(new_idx, roughness)
            builder.set_link_xsect(
                new_idx,
                shape_code,
                xsect_geom1,
                xsect_geom2,
                xsect_geom3,
                xsect_geom4,
            )
            return new_idx

        idx = await asyncio.to_thread(_add)
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
        from openswmm.engine import Nodes, Subcatchments

        # Resolve outlet node upfront (outside the worker).
        outlet_idx: int | None = None
        if outlet_node:
            nodes_accessor = Nodes(builder)
            outlet_idx = await resolve_index(nodes_accessor, outlet_node, "Node")
            if outlet_idx < 0:
                raise ToolError(
                    f"[{ErrorCode.ELEMENT_NOT_FOUND}] outlet_node '{outlet_node}' not found."
                )

        def _add() -> int:
            # builder.add_subcatchment returns SWMM error code (0 on success),
            # not the new index — resolve via Subcatchments(builder).get_index.
            rc = builder.add_subcatchment(subcatch_id)
            if rc != 0:
                raise RuntimeError(f"add_subcatchment returned error code {rc}")
            sc_accessor = Subcatchments(builder)
            new_idx = sc_accessor.get_index(subcatch_id)
            if new_idx < 0:
                raise RuntimeError(
                    f"add_subcatchment returned success but "
                    f"subcatchment '{subcatch_id}' could not be resolved by id."
                )
            # v1: properties are settable on the Subcatchment wrapper.
            sub = sc_accessor[new_idx]
            sub.area = area
            sub.slope = slope
            sub.width = width
            sub.imperv_pct = imperv_pct
            if outlet_idx is not None:
                sub.set_outlet_node(outlet_idx)
            return new_idx

        idx = await asyncio.to_thread(_add)
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

    from openswmm.engine import Gages

    def _add() -> int:
        # builder.add_gage returns SWMM error code (0 on success), not the
        # new index — resolve via Gages(builder).get_index.
        rc = builder.add_gage(gage_id)
        if rc != 0:
            raise RuntimeError(f"add_gage returned error code {rc}")
        new_idx = Gages(builder).get_index(gage_id)
        if new_idx < 0:
            raise RuntimeError(
                f"add_gage returned success but gage '{gage_id}' could not be resolved."
            )
        return new_idx

    try:
        idx = await asyncio.to_thread(_add)
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

    def _add_ts() -> None:
        # v1 Tables.add_timeseries(name) returns a TimeSeries object;
        # points are populated via .add(when, value).  Times are
        # interpreted as hours-from-start (matches the previous shape).
        ts = Tables(builder).add_timeseries(name)
        for t, v in zip(times, values):
            ts.add(t, v)

    try:
        await asyncio.to_thread(_add_ts)
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

    def _add_curve() -> None:
        # v1 Tables.add_curve(name, curve_type_int) returns a Curve object;
        # points are populated via add_point(x, y).
        curve = Tables(builder).add_curve(name, ctype)
        for x, y in zip(x_values, y_values):
            curve.add_point(x, y)

    try:
        await asyncio.to_thread(_add_curve)
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to add curve '{name}': {exc}") from exc

    return {
        "status": "ok",
        "session_id": session_id,
        "name": name,
        "curve_type_code": ctype,
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

    # ModelBuilder.validate() returns None on success and raises EngineError
    # / RuntimeError on validation failure (the engine doesn't surface a
    # warning list the way an older API did). Catch the failure path and
    # forward the engine's message; on success report no warnings.
    try:
        messages = await asyncio.to_thread(builder.validate)
    except ToolError:
        raise
    except Exception as exc:
        # Validation failed — the engine raised. Surface the message as a
        # single warning so the caller can see the failure reason without
        # having to catch the tool error.
        return {
            "status": "warnings",
            "session_id": session_id,
            "valid": False,
            "message_count": 1,
            "messages": [str(exc)],
        }

    # validate() may return None (no messages) or a list / tuple of messages.
    if messages is None:
        messages = []
    elif not isinstance(messages, list):
        messages = list(messages)
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

            # Finalize the model (builds connectivity, allocates arrays),
            # then transfer ownership of the engine handle to a Solver via
            # to_solver(). builder.finalize() returns None — the engine
            # handle stays on the builder until to_solver() steals it.
            await asyncio.to_thread(builder.finalize)
            solver = await asyncio.to_thread(builder.to_solver)
            session.backend = OpenSwmmBackend.from_solver(solver)
            session.state = "created"
            # The builder is now invalidated; drop the reference so future
            # tool calls don't accidentally try to use it.
            session.model_builder = None

            # Apply any pending options that were deferred — v1 uses the
            # ``solver.options`` MutableMapping rather than a method.
            pending = getattr(session, "_pending_options", {})

            def _apply_pending() -> None:
                for opt, val in pending.items():
                    try:
                        solver.options[opt] = val
                    except Exception:
                        logger.warning(
                            "Could not apply deferred option '%s'='%s'",
                            opt, val, exc_info=True,
                        )

            if pending:
                await asyncio.to_thread(_apply_pending)

            # v1 Solver writes via ``solver.write(path)`` (was model_write).
            await asyncio.to_thread(solver.write, output_path)
        else:
            if session.backend is None:
                raise ToolError(
                    f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no solver."
                )
            await asyncio.to_thread(session.backend.solver.write, output_path)
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


# ---------------------------------------------------------------------------
# Pollutant management
# ---------------------------------------------------------------------------

_POLLUTANT_UNITS: dict[str, int] = {
    "mg/l": 0,
    "ug/l": 1,
    "#/l": 2,
}

_POLLUTANT_UNITS_NAMES: dict[int, str] = {v: k.upper() for k, v in _POLLUTANT_UNITS.items()}


@building_mcp.tool()
async def add_pollutant(
    ctx: Context,
    session_id: str = "default",
    pollutant_id: str = "",
    units: str = "mg/l",
    kdecay: float = 0.0,
    rain_conc: float = 0.0,
    gw_conc: float = 0.0,
    init_conc: float = 0.0,
    snow_only: bool = False,
) -> BuildingResult:
    """Add a pollutant to the model.

    Valid in ``building`` or ``opened`` state.  After adding, the pollutant
    can be referenced by its ID when configuring buildup/washoff or quality
    injection.

    Parameters
    ----------
    pollutant_id:
        Unique pollutant identifier (e.g. ``TSS``, ``TN``).
    units:
        Concentration units: ``mg/l``, ``ug/l``, or ``#/l``.
    kdecay:
        First-order decay coefficient (1/days).
    rain_conc:
        Concentration in rainfall (same units as pollutant).
    gw_conc:
        Concentration in groundwater inflow.
    init_conc:
        Initial concentration in the network.
    snow_only:
        If True, buildup occurs only during snow accumulation.
    """
    if not pollutant_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] pollutant_id must not be empty.")

    units_key = units.strip().lower()
    if units_key not in _POLLUTANT_UNITS:
        valid = ", ".join(sorted(_POLLUTANT_UNITS))
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Unknown units '{units}'. Valid: {valid}.")
    units_code = _POLLUTANT_UNITS[units_key]

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Adding pollutants")
    if session.state not in ("building", "opened"):
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' is in state "
            f"'{session.state}'. Adding pollutants requires 'building' or 'opened' state."
        )

    # Resolve the engine handle — either builder or solver
    if session.state == "building":
        if session.model_builder is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{session_id}' has no ModelBuilder."
            )
        engine_obj = session.model_builder
    else:
        engine_obj = session.solver

    pollutants = Pollutants(engine_obj)

    def _add() -> int:
        # v1: Pollutants.add(name, units) returns a Pollutant wrapper;
        # subsequent fields are settable properties on the wrapper.
        new_p = pollutants.add(pollutant_id, units_code)
        if kdecay != 0.0:
            new_p.kdecay = kdecay
        if rain_conc != 0.0:
            new_p.rain_conc = rain_conc
        if gw_conc != 0.0:
            new_p.gw_conc = gw_conc
        if init_conc != 0.0:
            new_p.init_conc = init_conc
        if snow_only:
            new_p.snow_only = True
        return new_p.index

    try:
        new_idx = await asyncio.to_thread(_add)
    except RuntimeError as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}")

    logger.info(
        "Session '%s': pollutant '%s' added at index %d (units=%s).",
        session_id,
        pollutant_id,
        new_idx,
        units,
    )
    return BuildingResult(
        status="ok",
        element_type="pollutant",
        element_id=pollutant_id,
        index=new_idx,
        message=f"Pollutant '{pollutant_id}' added with units '{units.upper()}'.",
    )
