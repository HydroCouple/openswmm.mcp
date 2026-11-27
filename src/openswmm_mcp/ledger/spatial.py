"""Exact geometry for the ledger: working CRS, reprojection, buffer and nearest search.

Imports shapely and pyproj at module top. Callers import this module only inside
functions, after ``dependencies.require_spatial``.

Input shapes
------------
* Source features: GeoJSON geometry dicts in the source CRS (``features.geom_json``).
* Model elements: ``{"kind": "node|link|subcatchment|gage", "id": str,
  "geometry": <GeoJSON geometry dict in the model CRS>}`` (node and gage ``Point``,
  link ``LineString`` from the link vertices, subcatchment ``Polygon``).

CRS rule (design section 1): every function works per source. ``resolve_frame`` calls
``common.check_model_crs`` and ``common.crs_verdict``; sources it cannot use are returned
in ``Frame.excluded`` (never reprojected by guess) and ``Frame.usable`` lists the rest.
Local frames use no pyproj and measure in the map unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np
import pyproj
import shapely
from pyproj import CRS, Transformer
from pyproj.aoi import AreaOfInterest
from pyproj.database import query_utm_crs_info
from shapely.geometry import shape as _shape

from openswmm_mcp.ledger import common

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from shapely.geometry.base import BaseGeometry

_UNIT_NAMES = {"metre": "m", "meter": "m", "foot": "ft", "us survey foot": "ftUS"}


def linear_unit(crs: Any) -> str:
    """Short linear unit name of a CRS: ``m``, ``ft``, ``ftUS``, else pyproj's name verbatim."""
    name = CRS.from_user_input(crs).axis_info[0].unit_name
    return _UNIT_NAMES.get(name.lower(), name)


def shape(geom_json: Mapping[str, Any]) -> BaseGeometry:
    """A shapely geometry from a GeoJSON geometry dict."""
    return _shape(geom_json)


@dataclass
class Frame:
    """The working frame of one ``locate``/``link`` call.

    ``working_crs`` is a ledger label (``local:feet``, ``EPSG:2236``, ``EPSG:32617`` ...),
    ``unit`` the linear unit buffers and distances are in. ``usable`` are the source labels
    that can be searched; ``excluded`` maps the others to their ``CrsRefusal``.
    ``reprojected`` is the usable labels that need a transform.
    """

    model_label: str
    working_crs: str
    unit: str
    local: bool
    usable: list[str] = field(default_factory=list)
    excluded: dict[str, common.CrsRefusal] = field(default_factory=dict)
    reprojected: list[str] = field(default_factory=list)

    def transformer(self, from_label: str, to_label: str | None = None) -> Transformer | None:
        """Transformer ``from_label`` -> working CRS (or ``to_label``); None if identical."""
        target = to_label or self.working_crs
        if self.local or from_label == target:
            return None
        return Transformer.from_crs(
            CRS.from_user_input(from_label), CRS.from_user_input(target), always_xy=True
        )

    def transform_from(self, label: str) -> Callable[[BaseGeometry], BaseGeometry]:
        """Function moving a geometry in CRS ``label`` into the working CRS."""
        tr = self.transformer(label)
        if tr is None:
            return lambda g: g

        def move(g: BaseGeometry) -> BaseGeometry:
            def coords(c):
                x, y = tr.transform(c[:, 0], c[:, 1])
                return np.column_stack([x, y])

            return shapely.transform(g, coords)

        return move


def resolve_frame(
    model_label: str,
    source_labels: Iterable[str],
    anchor_xy: tuple[float, float] | None = None,
) -> Frame:
    """Choose the working CRS and sort source labels into usable and excluded.

    Parameters
    ----------
    model_label
        The model CRS label (``common.resolve_model_crs``).
    source_labels
        CRS labels of the in-scope spatial sources.
    anchor_xy
        Anchor point in the model CRS; required only for a geographic model CRS (picks the
        UTM zone).

    Raises
    ------
    common.CrsRefusal
        ``crs_required`` for a ``local:degrees`` model.
    ValueError
        A geographic model CRS without ``anchor_xy``, or an unparseable CRS label.
    """
    common.check_model_crs(model_label)
    labels = list(dict.fromkeys(source_labels))
    verdicts = {s: common.crs_verdict(model_label, s) for s in labels}
    excluded = {s: v for s, v in verdicts.items() if v is not None}
    usable = [s for s in labels if s not in excluded]

    if common.crs_class(model_label) == "local":
        unit = common.MAP_UNITS[model_label.split(":", 1)[1]]
        return Frame(model_label, model_label, unit, True, usable, excluded, [])

    try:
        model = CRS.from_user_input(model_label)
    except pyproj.exceptions.CRSError as exc:
        raise ValueError(f"unreadable CRS {model_label!r}: {exc}") from exc
    working = model_label
    if model.is_geographic:
        if anchor_xy is None:
            raise ValueError("a geographic model CRS needs an anchor to pick a UTM zone")
        lon, lat = anchor_xy
        info = query_utm_crs_info(
            datum_name="WGS 84", area_of_interest=AreaOfInterest(lon, lat, lon, lat)
        )
        if not info:
            raise ValueError(f"no UTM zone found for lon={lon}, lat={lat}")
        working = f"EPSG:{info[0].code}"
    unit = linear_unit(working)
    # Non-spatial ("none") sources need no transform; they are usable but not reprojected.
    reproj = [s for s in usable if common.crs_class(s) == "declared" and s != working]
    return Frame(model_label, working, unit, False, usable, excluded, reproj)


def element_geometry(element: Mapping[str, Any], frame: Frame) -> BaseGeometry:
    """A model element's geometry (model CRS) moved into the working CRS."""
    return frame.transform_from(frame.model_label)(shape(element["geometry"]))


def source_geometry(geom_json: Mapping[str, Any], label: str, frame: Frame) -> BaseGeometry:
    """A source feature's geometry (CRS ``label``) moved into the working CRS."""
    return frame.transform_from(label)(shape(geom_json))


def envelope_for(
    label: str, frame: Frame, bbox: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Bounding box in the working CRS -> bounding box in the source CRS ``label``.

    Used to prefilter stored feature bboxes; ``transform_bounds`` with densify 21 keeps the
    envelope conservative across curved reprojections. Local frames return ``bbox``.
    """
    tr = frame.transformer(frame.working_crs, label) if not frame.local else None
    if tr is None:
        return bbox
    return tuple(tr.transform_bounds(*bbox, densify_pts=21))  # type: ignore[return-value]


def within(
    anchor: BaseGeometry,
    candidates: Iterable[tuple[Any, BaseGeometry]],
    buffer: float,
) -> list[tuple[Any, float, str]]:
    """Candidates within ``buffer`` of ``anchor`` (all in the working CRS).

    Returns ``(item, distance, relation)`` sorted by distance; ``relation`` is
    ``"intersects"`` when the geometries intersect (distance 0), else ``"within_buffer"``.
    """
    out: list[tuple[Any, float, str]] = []
    for item, geom in candidates:
        if anchor.intersects(geom):
            out.append((item, 0.0, "intersects"))
            continue
        d = float(anchor.distance(geom))
        if d <= buffer:
            out.append((item, d, "within_buffer"))
    out.sort(key=lambda r: r[1])
    return out


def nearest(
    elements: Iterable[tuple[Any, BaseGeometry]],
    features: Iterable[tuple[str, BaseGeometry]],
    max_distance: float,
) -> list[tuple[Any, str, float]]:
    """Nearest feature per element within ``max_distance`` (ties: lowest ``feature_key``).

    Elements are ``(item, geometry)``, features ``(feature_key, geometry)``, all in the
    working CRS. Elements with no feature in range are omitted. Returns
    ``(item, feature_key, distance)`` in element order.
    """
    feats = sorted(features, key=lambda f: f[0])
    out: list[tuple[Any, str, float]] = []
    for item, geom in elements:
        best: tuple[str, float] | None = None
        for key, fg in feats:
            d = float(geom.distance(fg))
            if d <= max_distance and (best is None or d < best[1]):
                best = (key, d)
        if best is not None:
            out.append((item, best[0], best[1]))
    return out
