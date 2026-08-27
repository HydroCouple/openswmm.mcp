"""Spatial coordinate and water-quality tools."""

from __future__ import annotations

import asyncio

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import get_session_manager as _get_session_manager
from openswmm_mcp.dependencies import require_new_engine
from openswmm_mcp.errors import ToolError, resolve_index
from openswmm_mcp.models import SpatialResult

spatial_quality_mcp = FastMCP("spatial_quality")


_VALID_SPATIAL_TYPES = frozenset({"node", "link", "subcatchment"})
_VALID_SPATIAL_TYPES_WITH_GAGE = frozenset({"node", "link", "subcatchment", "gage"})

_NODE_TYPE_NAMES = {0: "JUNCTION", 1: "OUTFALL", 2: "STORAGE", 3: "DIVIDER"}
_LINK_TYPE_NAMES = {0: "CONDUIT", 1: "PUMP", 2: "ORIFICE", 3: "WEIR", 4: "OUTLET"}


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


def _validate_spatial_type_with_gage(element_type: str) -> str:
    """Normalise and validate an element type including gages."""
    normalised = element_type.strip().lower()
    if normalised not in _VALID_SPATIAL_TYPES_WITH_GAGE:
        sorted_types = ", ".join(f"'{t}'" for t in sorted(_VALID_SPATIAL_TYPES_WITH_GAGE))
        raise ToolError(f"Unknown element type '{element_type}'. Valid types are: {sorted_types}.")
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
        idx = await resolve_index(session.nodes, element_id, "Node")
        coord = await asyncio.to_thread(spatial.node_coord, idx)
        return SpatialResult(
            element_type=etype,
            element_id=element_id,
            x=coord[0],
            y=coord[1],
        )
    elif etype == "link":
        idx = await resolve_index(session.links, element_id, "Link")
        coord = await asyncio.to_thread(spatial.link_coord, idx)
        return SpatialResult(
            element_type=etype,
            element_id=element_id,
            x=coord[0],
            y=coord[1],
        )
    else:  # subcatchment
        idx = await resolve_index(session.subcatchments, element_id, "Subcatchment")
        coord = await asyncio.to_thread(spatial.subcatchment_coord, idx)
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
        idx = await resolve_index(session.nodes, element_id, "Node")
        await asyncio.to_thread(spatial.set_node_coord, idx, x, y)
    elif etype == "link":
        idx = await resolve_index(session.links, element_id, "Link")
        await asyncio.to_thread(spatial.set_link_coord, idx, x, y)
    else:
        idx = await resolve_index(session.subcatchments, element_id, "Subcatchment")
        await asyncio.to_thread(spatial.set_subcatchment_coord, idx, x, y)

    return {
        "status": "updated",
        "element_type": etype,
        "element_id": element_id,
        "x": x,
        "y": y,
        "message": (f"Coordinates for {etype} '{element_id}' set to ({x}, {y})."),
    }


@spatial_quality_mcp.tool()
async def set_gage_coord(
    ctx: Context,
    session_id: str = "default",
    gage_id: str = "",
    x: float = 0.0,
    y: float = 0.0,
) -> dict:
    """Set the (x, y) symbol coordinates of a rain gage.

    Complements :func:`get_all_coordinates` (which reads gage coordinates
    via ``element_type="gage"``).  The element-keyed :func:`set_coordinates`
    tool handles nodes / links / subcatchments only; this is the dedicated
    gage-coordinate setter.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    gage_id:
        The identifier of the rain gage to update.
    x:
        The new X coordinate.
    y:
        The new Y coordinate.
    """
    if not gage_id:
        raise ToolError("gage_id is required.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Gage coordinate updates")
    spatial = session.spatial

    idx = await resolve_index(session.gages, gage_id, "Gage")
    if idx < 0:
        raise ToolError(f"Gage '{gage_id}' not found.")

    # v1: spatial.set_gage_coord(gage, x, y) — accepts int index or str id.
    await asyncio.to_thread(spatial.set_gage_coord, idx, x, y)

    return {
        "status": "updated",
        "session_id": session_id,
        "gage_id": gage_id,
        "x": x,
        "y": y,
        "message": f"Coordinates for gage '{gage_id}' set to ({x}, {y}).",
    }


@spatial_quality_mcp.tool()
async def set_node_coords_bulk(
    ctx: Context,
    session_id: str = "default",
    coordinates: list[list[float]] = [],
) -> dict:
    """Set **all** node coordinates in one bulk call.

    The inverse of reading node coordinates via :func:`get_all_coordinates`
    (``element_type="node"``).  *coordinates* must be a list of ``[x, y]``
    pairs in node-index order, one per node in the model; a single C-level
    bulk write (memcpy) replaces every node's coordinates at once.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    coordinates:
        List of ``[x, y]`` pairs, one per node, in index order.
    """
    for i, pt in enumerate(coordinates):
        if len(pt) != 2:
            raise ToolError(f"coordinates[{i}] must be a two-element [x, y] list, got {pt!r}.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Bulk node coordinate updates")

    spatial = session.spatial
    nodes = session.nodes

    node_count = await asyncio.to_thread(lambda: len(nodes))
    if len(coordinates) != node_count:
        raise ToolError(
            f"coordinates has {len(coordinates)} rows but the model has "
            f"{node_count} node(s); supply exactly one [x, y] pair per node."
        )

    import numpy as np

    # v1 set_node_coords accepts one (n, 2) ndarray in node-index order.
    arr = np.asarray(coordinates, dtype=np.float64)
    if arr.size == 0:
        arr = arr.reshape(0, 2)
    await asyncio.to_thread(spatial.set_node_coords, arr)

    return {
        "status": "updated",
        "session_id": session_id,
        "node_count": node_count,
        "message": f"Set coordinates for {node_count} node(s).",
    }


@spatial_quality_mcp.tool()
async def get_vertices(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
) -> dict:
    """Return the ordered polyline vertices for a link (pipe or channel).

    Vertices are returned in upstream-to-downstream order.  For conduits with
    no interior vertices only the two endpoint coordinates (from the connected
    nodes) are returned.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    link_id:
        String identifier of the link whose vertices are requested.
    """
    if not link_id:
        raise ToolError("link_id is required.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Link vertex queries")

    spatial = session.spatial
    idx = await resolve_index(session.links, link_id, "Link")
    if idx < 0:
        raise ToolError(f"Link '{link_id}' not found.")

    # v1: spatial.link_vertices(idx) returns one np.ndarray of shape (n, 2).
    verts = await asyncio.to_thread(spatial.link_vertices, idx)
    count = int(len(verts))
    if count == 0:
        return {
            "session_id": session_id,
            "link_id": link_id,
            "vertex_count": 0,
            "vertices": [],
        }
    vertices = [[float(row[0]), float(row[1])] for row in verts]

    return {
        "session_id": session_id,
        "link_id": link_id,
        "vertex_count": len(vertices),
        "vertices": vertices,
    }


@spatial_quality_mcp.tool()
async def set_vertices(
    ctx: Context,
    session_id: str = "default",
    link_id: str = "",
    vertices: list[list[float]] = [],
) -> dict:
    """Set the ordered polyline vertices for a link.

    Replaces any existing interior vertices.  Each entry in *vertices* must
    be a two-element list ``[x, y]``.  Pass an empty list to clear all
    interior vertices.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    link_id:
        String identifier of the link to update.
    vertices:
        Ordered list of ``[x, y]`` coordinate pairs, upstream to downstream.
    """
    if not link_id:
        raise ToolError("link_id is required.")

    for i, pt in enumerate(vertices):
        if len(pt) != 2:
            raise ToolError(f"vertices[{i}] must be a two-element [x, y] list, got {pt!r}.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Link vertex updates")

    spatial = session.spatial
    idx = await resolve_index(session.links, link_id, "Link")
    if idx < 0:
        raise ToolError(f"Link '{link_id}' not found.")

    import numpy as np

    # v1 set_link_vertices accepts an (n, 2) ndarray.
    arr = np.asarray(vertices, dtype=np.float64)
    if arr.size == 0:
        arr = arr.reshape(0, 2)
    await asyncio.to_thread(spatial.set_link_vertices, idx, arr)

    return {
        "status": "updated",
        "session_id": session_id,
        "link_id": link_id,
        "vertex_count": len(vertices),
        "message": f"Set {len(vertices)} vertex/vertices for link '{link_id}'.",
    }


@spatial_quality_mcp.tool()
async def get_polygon(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str = "",
) -> dict:
    """Return the polygon vertices of a subcatchment (watershed boundary).

    Vertices define the closed boundary polygon of the subcatchment in model
    coordinates.  An empty list means no polygon geometry has been assigned.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    subcatch_id:
        String identifier of the subcatchment whose polygon is requested.
    """
    if not subcatch_id:
        raise ToolError("subcatch_id is required.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Subcatchment polygon queries")

    spatial = session.spatial
    idx = await resolve_index(session.subcatchments, subcatch_id, "Subcatchment")
    if idx < 0:
        raise ToolError(f"Subcatchment '{subcatch_id}' not found.")

    # v1: spatial.subcatchment_polygon(idx) returns one np.ndarray (n, 2).
    poly = await asyncio.to_thread(spatial.subcatchment_polygon, idx)
    count = int(len(poly))
    if count == 0:
        return {
            "session_id": session_id,
            "subcatch_id": subcatch_id,
            "vertex_count": 0,
            "polygon": [],
        }
    polygon = [[float(row[0]), float(row[1])] for row in poly]

    return {
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "vertex_count": len(polygon),
        "polygon": polygon,
    }


@spatial_quality_mcp.tool()
async def set_polygon(
    ctx: Context,
    session_id: str = "default",
    subcatch_id: str = "",
    polygon: list[list[float]] = [],
) -> dict:
    """Set the polygon boundary of a subcatchment (watershed).

    Replaces any existing polygon geometry.  Each entry in *polygon* must be
    a two-element list ``[x, y]``.  Pass an empty list to clear the polygon.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    subcatch_id:
        String identifier of the subcatchment to update.
    polygon:
        Ordered list of ``[x, y]`` coordinate pairs defining the closed boundary.
    """
    if not subcatch_id:
        raise ToolError("subcatch_id is required.")

    for i, pt in enumerate(polygon):
        if len(pt) != 2:
            raise ToolError(f"polygon[{i}] must be a two-element [x, y] list, got {pt!r}.")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Subcatchment polygon updates")

    spatial = session.spatial
    idx = await resolve_index(session.subcatchments, subcatch_id, "Subcatchment")
    if idx < 0:
        raise ToolError(f"Subcatchment '{subcatch_id}' not found.")

    import numpy as np

    # v1 set_subcatchment_polygon accepts an (n, 2) ndarray.
    arr = np.asarray(polygon, dtype=np.float64)
    if arr.size == 0:
        arr = arr.reshape(0, 2)
    await asyncio.to_thread(spatial.set_subcatchment_polygon, idx, arr)

    return {
        "status": "updated",
        "session_id": session_id,
        "subcatch_id": subcatch_id,
        "vertex_count": len(polygon),
        "message": f"Set polygon with {len(polygon)} vertices for subcatchment '{subcatch_id}'.",
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
    pollut_count = await asyncio.to_thread(len, pollutants)

    if etype == "node":
        elem_idx = await resolve_index(session.nodes, element_id, "Node")

        def _get_conc(p_idx: int) -> float:
            return session.nodes[elem_idx].quality(p_idx)

    elif etype == "link":
        elem_idx = await resolve_index(session.links, element_id, "Link")

        def _get_conc(p_idx: int) -> float:
            return session.links[elem_idx].quality(p_idx)

    else:
        elem_idx = await resolve_index(session.subcatchments, element_id, "Subcatchment")

        def _get_conc(p_idx: int) -> float:
            return session.subcatchments[elem_idx].quality(p_idx)

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

    node_idx = await resolve_index(session.nodes, node_id, "Node")
    pollut_idx = await resolve_index(session.pollutants, pollutant, "Pollutant")
    if pollut_idx < 0:
        raise ToolError(f"Pollutant '{pollutant}' not found in model.")

    await asyncio.to_thread(
        session.quality.set_treatment,
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
async def get_all_coordinates(
    ctx: Context,
    session_id: str = "default",
    element_type: str = "node",
) -> dict:
    """Return coordinates for **all** elements of a given type in one call.

    This is much faster than calling :func:`get_coordinates` in a loop.
    For nodes, a single C-level bulk read (memcpy) is used.  For links,
    subcatchments, and gages, all per-element reads are batched inside a
    single thread call to avoid per-element async overhead.

    Returns a list of ``{"id": "...", "x": ..., "y": ...}`` records, one per
    element, in index order.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    element_type:
        One of ``"node"``, ``"link"``, ``"subcatchment"``, or ``"gage"``.
    """
    etype = _validate_spatial_type_with_gage(element_type)
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Bulk spatial coordinate queries")

    spatial = session.spatial

    if etype == "node":
        nodes = session.nodes

        def _fetch_node_coords() -> tuple[int, list[dict]]:
            count = len(nodes)
            if count == 0:
                return 0, []
            # v1: spatial.node_coords() returns one ndarray shape (n, 2).
            arr = spatial.node_coords()
            return count, [
                {"id": nodes.get_id(i), "x": float(arr[i, 0]), "y": float(arr[i, 1])}
                for i in range(count)
            ]

        count, coords = await asyncio.to_thread(_fetch_node_coords)
        if count == 0:
            return {"session_id": session_id, "element_type": etype, "count": 0, "coordinates": []}

    elif etype == "link":
        links = session.links

        def _fetch_all_link_coords():
            count = len(links)
            return count, [
                {
                    "id": links.get_id(i),
                    "x": float(spatial.link_coord(i)[0]),
                    "y": float(spatial.link_coord(i)[1]),
                }
                for i in range(count)
            ]

        count, coords = await asyncio.to_thread(_fetch_all_link_coords)
        if count == 0:
            return {"session_id": session_id, "element_type": etype, "count": 0, "coordinates": []}

    elif etype == "subcatchment":
        subcatchments = session.subcatchments

        def _fetch_all_subcatch_coords():
            count = len(subcatchments)
            return count, [
                {
                    "id": subcatchments.get_id(i),
                    "x": float(spatial.subcatchment_coord(i)[0]),
                    "y": float(spatial.subcatchment_coord(i)[1]),
                }
                for i in range(count)
            ]

        count, coords = await asyncio.to_thread(_fetch_all_subcatch_coords)
        if count == 0:
            return {"session_id": session_id, "element_type": etype, "count": 0, "coordinates": []}

    else:  # gage
        gages = session.gages
        count = await asyncio.to_thread(lambda: len(gages))
        if count == 0:
            return {"session_id": session_id, "element_type": etype, "count": 0, "coordinates": []}

        def _fetch_all_gage_coords():
            return [
                {
                    "id": gages.get_id(i),
                    "x": float(spatial.gage_coord(i)[0]),
                    "y": float(spatial.gage_coord(i)[1]),
                }
                for i in range(count)
            ]

        coords = await asyncio.to_thread(_fetch_all_gage_coords)

    return {
        "session_id": session_id,
        "element_type": etype,
        "count": len(coords),
        "coordinates": coords,
    }


@spatial_quality_mcp.tool()
async def get_crs(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Return the coordinate reference system (CRS) string for the model.

    The CRS is stored as a string such as an EPSG code (e.g. ``EPSG:4326``),
    a PROJ string, or a WKT string.  An empty string means no CRS has been
    assigned.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "CRS queries")
    crs = await asyncio.to_thread(lambda: session.spatial.crs)
    return {"session_id": session_id, "crs": crs}


@spatial_quality_mcp.tool()
async def set_crs(
    ctx: Context,
    session_id: str = "default",
    crs: str = "",
) -> dict:
    """Set the coordinate reference system (CRS) string for the model.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    crs:
        CRS identifier string, e.g. ``"EPSG:4326"``, a PROJ string, or WKT.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "CRS updates")
    crs_value = crs

    def _set_crs() -> None:
        # v1: spatial.crs is a settable property.
        session.spatial.crs = crs_value

    await asyncio.to_thread(_set_crs)
    return {"status": "updated", "session_id": session_id, "crs": crs}


@spatial_quality_mcp.tool()
async def get_all_vertices(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Return the polyline vertices for **all** links in one call.

    Each link entry contains its full ordered polyline (upstream endpoint,
    any interior shape-points, downstream endpoint).  All reads are batched
    inside a single thread to avoid per-link async overhead.

    Returns a list of records::

        {"id": "C1", "vertex_count": 3, "vertices": [[x0,y0], [x1,y1], [x2,y2]]}

    Links with no stored geometry return ``"vertices": []``.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Bulk link vertex queries")

    spatial = session.spatial
    links = session.links

    def _fetch_all():
        count = len(links)
        if count == 0:
            return 0, []
        result = []
        for i in range(count):
            lid = links.get_id(i)
            # v1: spatial.link_vertices(i) returns ndarray shape (n, 2).
            arr = spatial.link_vertices(i)
            if len(arr) > 0:
                verts = [[float(row[0]), float(row[1])] for row in arr]
            else:
                verts = []
            result.append({"id": lid, "vertex_count": len(verts), "vertices": verts})
        return count, result

    count, links_data = await asyncio.to_thread(_fetch_all)
    if count == 0:
        return {"session_id": session_id, "link_count": 0, "links": []}
    return {"session_id": session_id, "link_count": len(links_data), "links": links_data}


@spatial_quality_mcp.tool()
async def get_all_polygons(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Return the boundary polygon for **all** subcatchments in one call.

    Each record contains the subcatchment ID, its centroid, and its full
    polygon vertex list.  All reads are batched inside a single thread to
    avoid per-subcatchment async overhead.

    Returns a list of records::

        {"id": "S1", "centroid": [x, y], "vertex_count": 6,
         "polygon": [[x0,y0], ..., [x5,y5]]}

    Subcatchments with no polygon geometry return ``"polygon": []``.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Bulk subcatchment polygon queries")

    spatial = session.spatial
    subcatchments = session.subcatchments

    def _fetch_all():
        count = len(subcatchments)
        if count == 0:
            return 0, []
        result = []
        for i in range(count):
            sid = subcatchments.get_id(i)
            cx, cy = spatial.subcatchment_coord(i)
            arr = spatial.subcatchment_polygon(i)
            if len(arr) > 0:
                poly = [[float(row[0]), float(row[1])] for row in arr]
            else:
                poly = []
            result.append(
                {
                    "id": sid,
                    "centroid": [float(cx), float(cy)],
                    "vertex_count": len(poly),
                    "polygon": poly,
                }
            )
        return count, result

    count, sc_data = await asyncio.to_thread(_fetch_all)
    if count == 0:
        return {"session_id": session_id, "subcatchment_count": 0, "subcatchments": []}
    return {"session_id": session_id, "subcatchment_count": len(sc_data), "subcatchments": sc_data}


@spatial_quality_mcp.tool()
async def get_model_geometry(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Return complete geometry for the entire model in a single call.

    This is the primary endpoint for rendering a SWMM model.  It returns all
    spatial data needed to draw nodes, links (pipes/channels), subcatchments
    (watersheds), and rain gages — plus the model CRS and bounding box.

    All data is fetched with minimal thread hops:

    - **Nodes** use a single C-level bulk memcpy for coordinates.
    - **Links**, **subcatchments**, and **gages** batch all per-element reads
      inside one thread call each.

    Response structure::

        {
          "session_id": "default",
          "crs": "EPSG:4326",
          "bounds": {"min_x": ..., "min_y": ..., "max_x": ..., "max_y": ...},
          "nodes": [
            {"id": "J1", "type": 0, "type_name": "JUNCTION", "x": 0.0, "y": 0.0}
          ],
          "links": [
            {"id": "C1", "type": 0, "type_name": "CONDUIT",
             "from_node": "J1", "to_node": "J2",
             "vertices": [[x0,y0], [x1,y1], ...]}
          ],
          "subcatchments": [
            {"id": "S1", "centroid": [x, y],
             "polygon": [[x0,y0], ...], "outlet_node_idx": 0}
          ],
          "gages": [
            {"id": "RG1", "x": 0.0, "y": 0.0}
          ]
        }

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    """
    import numpy as np

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Full model geometry export")

    spatial = session.spatial
    nodes_acc = session.nodes
    links_acc = session.links
    subcatch_acc = session.subcatchments
    gages_acc = session.gages

    # ---- CRS ----------------------------------------------------------------
    crs = await asyncio.to_thread(lambda: spatial.crs)

    # ---- Nodes (bulk coord memcpy + batched property reads) -----------------
    def _fetch_nodes() -> tuple[int, list[dict], np.ndarray]:
        count = len(nodes_acc)
        if count == 0:
            return 0, [], np.zeros((0, 2))
        # v1: spatial.node_coords() returns one ndarray shape (n, 2).
        arr = spatial.node_coords()
        recs = [
            {
                "id": nodes_acc.get_id(i),
                "type": int(nodes_acc[i].type),
                "type_name": _NODE_TYPE_NAMES.get(int(nodes_acc[i].type), "UNKNOWN"),
                "x": float(arr[i, 0]),
                "y": float(arr[i, 1]),
            }
            for i in range(count)
        ]
        return count, recs, arr

    node_count, nodes_data, node_xy = await asyncio.to_thread(_fetch_nodes)

    # ---- Links (batched per-link reads in one thread) -----------------------
    def _fetch_links():
        count = len(links_acc)
        if count == 0:
            return 0, []
        result = []
        for i in range(count):
            link = links_acc[i]
            ltype = int(link.type)
            fn_wrap = link.from_node
            tn_wrap = link.to_node
            verts_arr = spatial.link_vertices(i)
            verts = (
                [[float(row[0]), float(row[1])] for row in verts_arr] if len(verts_arr) > 0 else []
            )
            result.append(
                {
                    "id": links_acc.get_id(i),
                    "type": ltype,
                    "type_name": _LINK_TYPE_NAMES.get(ltype, "UNKNOWN"),
                    "from_node": fn_wrap.id,
                    "to_node": tn_wrap.id,
                    "vertices": verts,
                }
            )
        return count, result

    link_count, links_data = await asyncio.to_thread(_fetch_links)

    # ---- Subcatchments (batched per-subcatchment reads in one thread) --------
    def _fetch_subcatchments():
        count = len(subcatch_acc)
        if count == 0:
            return 0, []
        result = []
        for i in range(count):
            sub = subcatch_acc[i]
            cx, cy = spatial.subcatchment_coord(i)
            poly_arr = spatial.subcatchment_polygon(i)
            poly = [[float(row[0]), float(row[1])] for row in poly_arr] if len(poly_arr) > 0 else []
            # v1: subcatchment.outlet returns Union[Node, Subcatchment, None]
            outlet = sub.outlet
            outlet_idx = -1
            try:
                outlet_idx = int(outlet.index) if outlet is not None else -1
            except AttributeError:
                outlet_idx = -1
            result.append(
                {
                    "id": subcatch_acc.get_id(i),
                    "centroid": [float(cx), float(cy)],
                    "polygon": poly,
                    "outlet_node_idx": outlet_idx,
                }
            )
        return count, result

    sc_count, sc_data = await asyncio.to_thread(_fetch_subcatchments)

    # ---- Gages (batched per-gage reads in one thread) -----------------------
    def _fetch_gages():
        count = len(gages_acc)
        return count, [
            {
                "id": gages_acc.get_id(i),
                "x": float(spatial.gage_coord(i)[0]),
                "y": float(spatial.gage_coord(i)[1]),
            }
            for i in range(count)
        ]

    gage_count, gages_data = await asyncio.to_thread(_fetch_gages)

    # ---- Bounding box (from node coords) ------------------------------------
    bounds: dict = {}
    if node_count > 0:
        node_x = node_xy[:, 0]
        node_y = node_xy[:, 1]
        valid_x = node_x[np.isfinite(node_x)]
        valid_y = node_y[np.isfinite(node_y)]
        if len(valid_x) > 0:
            bounds = {
                "min_x": float(valid_x.min()),
                "min_y": float(valid_y.min()),
                "max_x": float(valid_x.max()),
                "max_y": float(valid_y.max()),
            }

    return {
        "session_id": session_id,
        "crs": crs,
        "bounds": bounds,
        "node_count": node_count,
        "link_count": link_count,
        "subcatchment_count": sc_count,
        "gage_count": gage_count,
        "nodes": nodes_data,
        "links": links_data,
        "subcatchments": sc_data,
        "gages": gages_data,
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

    subcatch_idx = await resolve_index(session.subcatchments, subcatch_id, "Subcatchment")
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
