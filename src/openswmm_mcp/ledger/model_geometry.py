"""Read the geometry of a session's model from the engine, as plain GeoJSON-style dicts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from openswmm_mcp import catalog as cat
from openswmm_mcp.ledger.ingest import map_units
from openswmm_mcp.session import SimSession

__all__ = ["model_elements"]


def _pt(pair: Any) -> list[float]:
    return [float(pair[0]), float(pair[1])]


def _ids(solver: Any, kind: str) -> list[str]:
    collection = cat.resolve(solver, cat.require_kind(kind)["collection"], None)
    if len(collection) == 0:  # the bulk id getters refuse an empty collection
        return []
    return [str(i) for i in collection.ids]


def _read(session: SimSession) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    solver = session.require_solver()
    spatial = cat.resolve(solver, "spatial", None)
    elements: list[dict[str, Any]] = []
    no_geometry: list[dict[str, str]] = []

    nodes = _ids(solver, "node")
    if nodes:
        coords = spatial.node_coords()
        for i, node_id in enumerate(nodes):
            elements.append(
                {
                    "kind": "node",
                    "id": node_id,
                    "geometry": {"type": "Point", "coordinates": _pt(coords[i])},
                }
            )
    for link_id in _ids(solver, "link"):
        link = cat.resolve(solver, "link", link_id)
        verts = [_pt(v) for v in spatial.link_vertices(link_id)]
        element: dict[str, Any] = {
            "kind": "link",
            "id": link_id,
            "geometry": {"type": "LineString", "coordinates": verts} if len(verts) >= 2 else None,
            "from_node": str(link.from_node.id),
            "to_node": str(link.to_node.id),
        }
        if element["geometry"] is None:
            no_geometry.append({"kind": "link", "id": link_id})
            continue
        elements.append(element)
    for sub_id in _ids(solver, "subcatchment"):
        ring = [_pt(v) for v in spatial.subcatchment_polygon(sub_id)]
        geometry = None
        if len(ring) >= 3:
            if ring[0] != ring[-1]:
                ring.append(list(ring[0]))
            geometry = {"type": "Polygon", "coordinates": [ring]}
        else:
            no_geometry.append({"kind": "subcatchment", "id": sub_id})
            continue
        elements.append({"kind": "subcatchment", "id": sub_id, "geometry": geometry})
    for gage_id in _ids(solver, "gage"):
        elements.append(
            {
                "kind": "gage",
                "id": gage_id,
                "geometry": {"type": "Point", "coordinates": _pt(spatial.gage_coord(gage_id))},
            }
        )

    text, digest = "", None
    if session.inp_path:
        try:
            raw = Path(session.inp_path).read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            text = raw.decode("utf-8", errors="replace")
        except OSError:
            pass
    meta = {
        "inp_sha256": digest,
        "map_units": map_units(text),
        "engine_crs": str(spatial.crs or ""),
        "no_geometry": no_geometry,
    }
    return elements, meta


def model_elements(session: SimSession) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Every node, link, subcatchment and gage of the open model with its geometry.

    Run under ``session.call``. Readable in any state that has a solver. Elements are
    ``{kind, id, geometry}`` with a GeoJSON ``Point``/``LineString``/``Polygon`` (closed ring)
    in model coordinates; links also carry ``from_node`` and ``to_node``. Links with fewer
    than 2 vertices and polygons with fewer than 3 points are skipped and listed in
    ``meta["no_geometry"]``. ``meta`` is ``{inp_sha256, map_units, engine_crs, no_geometry}``.
    """
    return _read(session)
