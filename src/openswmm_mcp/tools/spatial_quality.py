"""Spatial coordinate and water-quality tools."""

from __future__ import annotations

import asyncio

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import require_new_engine
from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import SpatialResult

spatial_quality_mcp = FastMCP("spatial_quality")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session_manager(ctx: Context):
    """Extract the :class:`SessionManager` from the lifespan context."""
    try:
        return ctx.lifespan_context["session_manager"]
    except (KeyError, TypeError) as exc:
        raise ToolError(
            "Session manager is not available. The server may not have started correctly."
        ) from exc


_VALID_SPATIAL_TYPES = frozenset({"node", "link", "subcatchment"})


def _validate_spatial_type(element_type: str) -> str:
    """Normalise and validate an element type for spatial operations."""
    normalised = element_type.strip().lower()
    if normalised not in _VALID_SPATIAL_TYPES:
        sorted_types = ", ".join(f"'{t}'" for t in sorted(_VALID_SPATIAL_TYPES))
        raise ToolError(
            f"Unknown element type '{element_type}' for spatial query. "
            f"Valid types are: {sorted_types}."
        )
    return normalised


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@spatial_quality_mcp.tool()
async def get_coordinates(
    ctx: Context,
    session_id: str = "default",
    element_type: str = "node",
    element_id: str = "",
) -> SpatialResult:
    """Retrieve the spatial coordinates for a model element.

    Supports nodes (x, y), links (x, y centroid), and subcatchments (x, y
    centroid) depending on the data stored in the model.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    element_type:
        One of ``"node"``, ``"link"``, or ``"subcatchment"``.
    element_id:
        The identifier of the element whose coordinates are requested.
    """
    if not element_id:
        raise ToolError("element_id is required.")

    etype = _validate_spatial_type(element_type)
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Spatial coordinate queries")
    spatial = session.spatial

    if etype == "node":
        idx = await asyncio.to_thread(session.nodes.get_index, element_id)
        coord = await asyncio.to_thread(spatial.get_node_coord, idx)
        return SpatialResult(
            element_type=etype,
            element_id=element_id,
            x=coord[0],
            y=coord[1],
        )
    elif etype == "link":
        idx = await asyncio.to_thread(session.links.get_index, element_id)
        coord = await asyncio.to_thread(spatial.get_link_coord, idx)
        return SpatialResult(
            element_type=etype,
            element_id=element_id,
            x=coord[0],
            y=coord[1],
        )
    else:  # subcatchment
        idx = await asyncio.to_thread(session.subcatchments.get_index, element_id)
        coord = await asyncio.to_thread(spatial.get_subcatch_coord, idx)
        return SpatialResult(
            element_type=etype,
            element_id=element_id,
            x=coord[0],
            y=coord[1],
        )


@spatial_quality_mcp.tool()
async def set_coordinates(
    ctx: Context,
    session_id: str = "default",
    element_type: str = "node",
    element_id: str = "",
    x: float = 0.0,
    y: float = 0.0,
) -> dict:
    """Set the spatial coordinates for a model element.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    element_type:
        One of ``"node"``, ``"link"``, or ``"subcatchment"``.
    element_id:
        The identifier of the element to update.
    x:
        The new X coordinate.
    y:
        The new Y coordinate.
    """
    if not element_id:
        raise ToolError("element_id is required.")

    etype = _validate_spatial_type(element_type)
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Spatial coordinate updates")
    spatial = session.spatial

    if etype == "node":
        idx = await asyncio.to_thread(session.nodes.get_index, element_id)
        await asyncio.to_thread(spatial.set_node_coord, idx, x, y)
    elif etype == "link":
        idx = await asyncio.to_thread(session.links.get_index, element_id)
        await asyncio.to_thread(spatial.set_link_coord, idx, x, y)
    else:
        idx = await asyncio.to_thread(session.subcatchments.get_index, element_id)
        await asyncio.to_thread(spatial.set_subcatch_coord, idx, x, y)

    return {
        "status": "updated",
        "element_type": etype,
        "element_id": element_id,
        "x": x,
        "y": y,
        "message": (f"Coordinates for {etype} '{element_id}' set to ({x}, {y})."),
    }


@spatial_quality_mcp.tool()
async def get_quality(
    ctx: Context,
    session_id: str = "default",
    element_type: str = "node",
    element_id: str = "",
    pollutant: str | None = None,
) -> dict:
    """Retrieve water-quality concentrations for a model element.

    When *pollutant* is ``None`` all tracked pollutants are returned;
    otherwise only the named pollutant's concentration is included.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    element_type:
        One of ``"node"``, ``"link"``, or ``"subcatchment"``.
    element_id:
        The identifier of the element to query.
    pollutant:
        Optional pollutant name.  When omitted, all pollutants are returned.
    """
    if not element_id:
        raise ToolError("element_id is required.")

    etype = _validate_spatial_type(element_type)
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Water-quality queries")

    pollutants = session.pollutants
    pollut_count = await asyncio.to_thread(pollutants.count)

    if etype == "node":
        elem_idx = await asyncio.to_thread(session.nodes.get_index, element_id)

        def _get_conc(p_idx: int) -> float:
            return session.nodes.get_quality(elem_idx, p_idx)

    elif etype == "link":
        elem_idx = await asyncio.to_thread(session.links.get_index, element_id)

        def _get_conc(p_idx: int) -> float:
            return session.links.get_quality(elem_idx, p_idx)

    else:
        elem_idx = await asyncio.to_thread(session.subcatchments.get_index, element_id)

        def _get_conc(p_idx: int) -> float:
            return session.subcatchments.get_quality(elem_idx, p_idx)

    data: dict[str, float] = {}
    for p_idx in range(pollut_count):
        p_name = await asyncio.to_thread(pollutants.get_id, p_idx)
        conc = await asyncio.to_thread(_get_conc, p_idx)
        data[p_name] = conc

    if pollutant is not None:
        if pollutant not in data:
            available = ", ".join(data.keys()) if data else "none"
            raise ToolError(
                f"Pollutant '{pollutant}' not found for {etype} "
                f"'{element_id}'. Available: {available}."
            )
        data = {pollutant: data[pollutant]}

    return {
        "session_id": session_id,
        "element_type": etype,
        "element_id": element_id,
        "quality": data,
    }


@spatial_quality_mcp.tool()
async def set_treatment(
    ctx: Context,
    session_id: str = "default",
    node_id: str = "",
    pollutant: str = "",
    expression: str = "",
) -> dict:
    """Assign a treatment expression to a node for a given pollutant.

    Treatment expressions use SWMM's built-in syntax (e.g.
    ``"R = 0.5 * C"`` to remove 50 % of concentration *C*).

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    node_id:
        The node to which the treatment applies.
    pollutant:
        Name of the pollutant being treated.
    expression:
        SWMM treatment expression string.
    """
    if not node_id:
        raise ToolError("node_id is required.")
    if not pollutant:
        raise ToolError("pollutant is required.")
    if not expression:
        raise ToolError("expression is required.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Treatment assignments")

    node_idx = await asyncio.to_thread(session.nodes.get_index, node_id)
    pollut_idx = await asyncio.to_thread(session.pollutants.get_index, pollutant)
    if pollut_idx < 0:
        raise ToolError(f"Pollutant '{pollutant}' not found in model.")

    await asyncio.to_thread(
        session.quality.treatment_set,
        node_idx,
        pollut_idx,
        expression,
    )

    return {
        "status": "treatment_set",
        "session_id": session_id,
        "node_id": node_id,
        "pollutant": pollutant,
        "expression": expression,
        "message": (f"Treatment for pollutant '{pollutant}' set on node '{node_id}': {expression}"),
    }


@spatial_quality_mcp.tool()
async def add_lid(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str = "",
    lid_idx: int = 0,
    number: int = 1,
    area: float = 0.0,
    width: float = 0.0,
    init_sat: float = 0.0,
    from_imperv: float = 1.0,
) -> dict:
    """Add a Low Impact Development (LID) control to a subcatchment.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    subcatch_id:
        The subcatchment that will receive the LID.
    lid_idx:
        Index of the LID control defined in the model (zero-based).
    number:
        Number of identical LID units to place.
    area:
        Surface area of each LID unit (in model area units).
    width:
        Top width of the overland-flow surface of each LID unit.
    init_sat:
        Initial saturation fraction in [0.0, 1.0].
    from_imperv:
        Fraction of impervious area routed to the LID, in [0.0, 1.0].
    """
    if not subcatch_id:
        raise ToolError("subcatch_id is required.")
    if area <= 0.0:
        raise ToolError("area must be a positive number.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "LID controls")

    subcatch_idx = await asyncio.to_thread(session.subcatchments.get_index, subcatch_id)
    await asyncio.to_thread(
        session.infrastructure.lid_usage_add,
        subcatch_idx,
        lid_idx,
        number,
        area,
        width,
        init_sat,
        from_imperv,
    )

    return {
        "status": "lid_added",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "lid_idx": lid_idx,
        "number": number,
        "area": area,
        "message": (
            f"LID {lid_idx} with area {area} × {number} unit(s) "
            f"added to subcatchment '{subcatch_id}'."
        ),
    }
