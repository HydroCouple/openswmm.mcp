"""Spatial coordinate and water-quality tools."""

from __future__ import annotations

import asyncio

from fastmcp import Context, FastMCP

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

    Supports nodes (x, y), links (vertex list), and subcatchments (centroid
    or polygon vertices) depending on the data stored in the model.

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
    spatial = session.spatial

    if etype == "node":
        coord = await asyncio.to_thread(spatial.get_node_coord, element_id)
        return SpatialResult(
            element_type=etype,
            element_id=element_id,
            x=coord[0],
            y=coord[1],
        )
    elif etype == "link":
        coord = await asyncio.to_thread(spatial.get_link_coord, element_id)
        return SpatialResult(
            element_type=etype,
            element_id=element_id,
            vertices=coord,
        )
    else:  # subcatchment
        coord = await asyncio.to_thread(
            spatial.get_subcatch_coord,
            element_id,
        )
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
    spatial = session.spatial

    if etype == "node":
        await asyncio.to_thread(spatial.set_node_coord, element_id, x, y)
    elif etype == "link":
        await asyncio.to_thread(spatial.set_link_coord, element_id, x, y)
    else:
        await asyncio.to_thread(spatial.set_subcatch_coord, element_id, x, y)

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
    quality = session.quality

    if etype == "node":
        data = await asyncio.to_thread(quality.get_node_quality, element_id)
    elif etype == "link":
        data = await asyncio.to_thread(quality.get_link_quality, element_id)
    else:
        data = await asyncio.to_thread(
            quality.get_subcatch_quality,
            element_id,
        )

    # data is expected to be a dict mapping pollutant name -> concentration
    if isinstance(data, dict) and pollutant is not None:
        if pollutant not in data:
            raise ToolError(
                f"Pollutant '{pollutant}' not found for {etype} "
                f"'{element_id}'. Available: {', '.join(data.keys())}."
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

    await asyncio.to_thread(
        session.quality.set_treatment,
        node_id,
        pollutant,
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
    lid_type: str = "",
    area: float = 0.0,
) -> dict:
    """Add a Low Impact Development (LID) control to a subcatchment.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    subcatch_id:
        The subcatchment that will receive the LID.
    lid_type:
        The type of LID control (e.g. ``"BC"`` for bio-retention cell,
        ``"RG"`` for rain garden, ``"PP"`` for permeable pavement).
    area:
        The surface area of the LID unit (in model area units).
    """
    if not subcatch_id:
        raise ToolError("subcatch_id is required.")
    if not lid_type:
        raise ToolError("lid_type is required.")
    if area <= 0.0:
        raise ToolError("area must be a positive number.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)

    await asyncio.to_thread(
        session.infrastructure.add_lid,
        subcatch_id,
        lid_type,
        area,
    )

    return {
        "status": "lid_added",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "lid_type": lid_type,
        "area": area,
        "message": (f"LID '{lid_type}' with area {area} added to subcatchment '{subcatch_id}'."),
    }
