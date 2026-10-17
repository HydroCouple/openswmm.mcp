"""Read the files a model was built from: hashes, formats, GeoJSON, text, CSV, id links.

Standard library only; this module must not import ``openswmm`` (the ledger extra is not
needed to register or link by id). Element dicts passed in come from ``model_geometry``:
``{"kind", "id", ...}``; feature dicts are ``{"feature_key", "props", ...}``.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from openswmm_mcp.ledger.common import ELEMENT_KINDS, FORMATS, canonical_time, crs_label

__all__ = [
    "sha256_file",
    "detect_format",
    "map_units",
    "read_geojson",
    "chunk_text",
    "scan_mentions",
    "csv_span",
    "id_match",
    "manual_edges",
]

_TOKEN = re.compile(r"[^\s,;:()\[\]{}\"'<>]+")
_CONTEXT = 80
_CSV_FORMATS = ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y")
_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}
_CRS84 = "urn:ogc:def:crs:ogc:1.3:crs84"


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of a file, read in blocks."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def detect_format(path: str | Path) -> str:
    """The ledger format of a file from its extension.

    ``.json`` becomes ``geojson`` when the file holds a GeoJSON object.

    Raises
    ------
    ValueError
        If the extension is not in ``FORMATS``.
    """
    path = Path(path)
    fmt = FORMATS.get(path.suffix.lower())
    if fmt is None:
        raise ValueError(f"unsupported file extension {path.suffix!r}; known: {sorted(FORMATS)}")
    if fmt == "json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "json"
        if isinstance(data, dict) and data.get("type") in ("FeatureCollection", "Feature"):
            return "geojson"
    return fmt


def map_units(inp_text: str) -> str:
    """The ``[MAP]`` ``Units`` value of a model, lower case; ``"none"`` when absent.

    Anything outside feet/meters/degrees/none is also reported as ``"none"``.
    """
    in_map = False
    for raw in inp_text.splitlines():
        line = raw.split(";", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            in_map = line.strip("[] \t").upper() == "MAP"
            continue
        if in_map:
            parts = line.split()
            if len(parts) >= 2 and parts[0].upper() == "UNITS":
                value = parts[1].lower()
                return value if value in ("feet", "meters", "degrees", "none") else "none"
    return "none"


# ---------------------------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------------------------
def _positions(coords: Any, out: list[tuple[float, float]]) -> None:
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in coords[:2])
    ):
        x, y = float(coords[0]), float(coords[1])
        if not (math.isfinite(x) and math.isfinite(y)):
            raise ValueError("non-finite coordinate")
        out.append((x, y))
    elif isinstance(coords, (list, tuple)):
        for item in coords:
            _positions(item, out)
    else:
        raise ValueError(f"bad coordinate {coords!r}")


def _geometry_bbox(geometry: dict) -> tuple[float, float, float, float] | None:
    gtype = geometry.get("type")
    if gtype not in _GEOMETRY_TYPES:
        raise ValueError(f"unsupported geometry type {gtype!r}")
    points: list[tuple[float, float]] = []
    if gtype == "GeometryCollection":
        for sub in geometry.get("geometries") or []:
            box = _geometry_bbox(sub)
            if box:
                points += [(box[0], box[1]), (box[2], box[3])]
    else:
        _positions(geometry.get("coordinates"), points)
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def _declared_crs(member: Any) -> str | None:
    """The CRS label of a legacy GeoJSON ``crs`` member, or None when absent/unreadable."""
    if not isinstance(member, dict):
        return None
    name = (member.get("properties") or {}).get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    if name.strip().lower() == _CRS84:
        return "EPSG:4326"
    return crs_label(name)


def read_geojson(
    path: str | Path, crs: str | None = None
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """Parse a GeoJSON file into feature dicts.

    Accepts a FeatureCollection, a Feature or a bare geometry. Each feature is
    ``{"feature_key", "geometry", "bbox", "props"}``: the key is the GeoJSON ``id`` (as text)
    else the feature's index; ``bbox`` is ``(minx, miny, maxx, maxy)`` or None for a null
    geometry. The CRS label is ``crs`` if given, else the legacy ``crs`` member, else
    ``unknown`` (no RFC 7946 default is assumed).

    Raises
    ------
    ValueError
        If the file is not valid GeoJSON (unparsable, wrong type, bad geometry).
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"not readable as JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("a GeoJSON file must hold a JSON object")
    kind = data.get("type")
    if kind == "FeatureCollection":
        raw_features = data.get("features")
        if not isinstance(raw_features, list):
            raise ValueError("FeatureCollection has no 'features' list")
    elif kind == "Feature":
        raw_features = [data]
    elif kind in _GEOMETRY_TYPES:
        raw_features = [{"type": "Feature", "geometry": data, "properties": {}}]
    else:
        raise ValueError(f"top-level type {kind!r} is not GeoJSON")

    warnings: list[str] = []
    if crs:
        label = crs_label(crs)
    else:
        label = _declared_crs(data.get("crs")) or "unknown"
        if label == "unknown":
            warnings.append(
                "GeoJSON has no crs member and none was declared; its CRS is unknown. "
                "Re-register with crs=... to use it for spatial search."
            )
    features: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_features):
        if not isinstance(item, dict) or item.get("type") != "Feature":
            raise ValueError(f"features[{index}] is not a Feature")
        geometry = item.get("geometry")
        if geometry is not None and not isinstance(geometry, dict):
            raise ValueError(f"features[{index}] has a bad geometry")
        try:
            bbox = _geometry_bbox(geometry) if geometry else None
        except ValueError as exc:
            raise ValueError(f"features[{index}]: {exc}") from exc
        key = str(item["id"]) if item.get("id") is not None else str(index)
        if key in seen:
            warnings.append(f"duplicate feature id {key!r} at index {index}; keyed by index")
            key = str(index)
            if key in seen:
                key = f"{index}#{len(seen)}"
        seen.add(key)
        props = item.get("properties")
        features.append(
            {
                "feature_key": key,
                "geometry": geometry,
                "bbox": bbox,
                "props": props if isinstance(props, dict) else {},
            }
        )
    return features, label, warnings


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------
def chunk_text(text: str, size: int = 1500) -> list[tuple[int, str]]:
    """Split text at paragraph boundaries into ``(offset, chunk)`` pieces.

    Paragraphs (blank-line separated) are packed up to ``size`` characters; a paragraph
    longer than ``size`` is split hard. Offsets index the whole text, and
    ``text[offset:offset + len(chunk)] == chunk`` for every chunk.
    """
    chunks: list[tuple[int, str]] = []
    start = end = None  # current packed run, as text offsets
    for m in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\s*\Z)", text, flags=re.DOTALL):
        p0, p1 = m.start(), m.end()
        if p1 - p0 > size:
            if start is not None:
                chunks.append((start, text[start:end]))
                start = end = None
            for s in range(p0, p1, size):
                chunks.append((s, text[s : min(s + size, p1)]))
            continue
        if start is None:
            start, end = p0, p1
        elif p1 - start <= size:
            end = p1
        else:
            chunks.append((start, text[start:end]))
            start, end = p0, p1
    if start is not None:
        chunks.append((start, text[start:end]))
    return chunks


def scan_mentions(chunks: list[tuple[int, str]], ids: dict[str, set[str]]) -> list[dict[str, Any]]:
    """Find exact, case-sensitive element ids in text chunks.

    Tokens are runs of characters other than whitespace and ``, ; : ( ) [ ] { } " ' < >``;
    trailing dots are stripped, so ``J2.`` matches ``J2`` but ``J22`` and ``C2a`` do not
    match ``J2`` / ``C2``. ``ids`` maps an element kind to its set of ids.

    Returns
    -------
    list of dict
        ``{chunk_index, element_kind, element_id, span_start, span_end, text}`` with spans as
        offsets in the whole text and ``text`` the match with up to 80 characters of context.
    """
    found: list[dict[str, Any]] = []
    for index, (offset, chunk) in enumerate(chunks):
        for m in _TOKEN.finditer(chunk):
            token = m.group().rstrip(".")
            if not token:
                continue
            for kind in ELEMENT_KINDS:
                if token in ids.get(kind, ()):
                    s = m.start()
                    e = s + len(token)
                    found.append(
                        {
                            "chunk_index": index,
                            "element_kind": kind,
                            "element_id": token,
                            "span_start": offset + s,
                            "span_end": offset + e,
                            "text": chunk[max(0, s - _CONTEXT) : e + _CONTEXT],
                        }
                    )
    return found


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def _parse_time(text: str) -> str | None:
    text = text.strip()
    if not text:
        return None
    try:
        return canonical_time(text)
    except ValueError:
        pass
    for fmt in _CSV_FORMATS:
        try:
            return canonical_time(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def csv_span(path: str | Path) -> dict[str, Any]:
    """The time span and columns of a CSV time series.

    The timestamp is the first column; when the second holds a time of day (contains ``:``),
    the first two columns joined by a space (date, time). Accepted: ISO, ``%m/%d/%Y %H:%M[:%S]``,
    ``%m/%d/%Y``.
    Naive times are UTC. Rows that do not parse (a header, blanks) are skipped.

    Returns
    -------
    dict
        ``{start, end, columns, rows, parsed}`` where start/end are canonical times or None,
        ``columns`` the header cells, ``rows`` the data-row count, ``parsed`` how many carried
        a timestamp.
    """
    import csv

    columns: list[str] = []
    times: list[str] = []
    rows = 0
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as handle:
        for n, row in enumerate(csv.reader(handle)):
            if not any(c.strip() for c in row):
                continue
            stamp = None
            if len(row) > 1 and ":" in row[1]:  # date and time in separate columns
                stamp = _parse_time(f"{row[0].strip()} {row[1].strip()}")
            if stamp is None and row:
                stamp = _parse_time(row[0])
            if stamp is None:
                if not columns:
                    columns = [c.strip() for c in row]
                continue
            times.append(stamp)
            rows += 1
    return {
        "start": min(times) if times else None,
        "end": max(times) if times else None,
        "columns": columns,
        "rows": rows,
        "parsed": len(times),
    }


# ---------------------------------------------------------------------------
# Lineage rules that need no geometry
# ---------------------------------------------------------------------------
def id_match(
    elements: list[dict[str, Any]],
    features: list[dict[str, Any]],
    key: str,
    kinds: list[str] | None = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Link features to elements whose id equals ``str(props[key])`` (case-sensitive).

    Returns
    -------
    tuple
        ``(edges, unmatched)``: edges are ``{element_kind, element_id, feature_key}``;
        unmatched are the in-scope elements no feature matched, ``{element_kind, element_id}``.
    """
    wanted = set(kinds) if kinds else set(ELEMENT_KINDS)
    by_value: dict[str, list[str]] = {}
    for feature in features:
        value = (feature.get("props") or {}).get(key)
        if value is not None:
            by_value.setdefault(str(value), []).append(feature["feature_key"])
    edges: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []
    for element in elements:
        if element["kind"] not in wanted:
            continue
        keys = by_value.get(str(element["id"]))
        if not keys:
            unmatched.append({"element_kind": element["kind"], "element_id": str(element["id"])})
            continue
        for fk in keys:
            edges.append(
                {
                    "element_kind": element["kind"],
                    "element_id": str(element["id"]),
                    "feature_key": fk,
                }
            )
    return edges, unmatched


def manual_edges(
    edges: list[dict[str, Any]],
    elements: list[dict[str, Any]],
    features: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Validate hand-written edges against the model and the source.

    Returns
    -------
    tuple
        ``(accepted, errors)``: accepted edges are normalised and de-duplicated; each error is
        ``{index, reason}`` for an edge that is malformed or names an unknown element/feature.
    """
    known_elements = {(e["kind"], str(e["id"])) for e in elements}
    known_features = {str(f["feature_key"]) for f in features}
    accepted: list[dict[str, str]] = []
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, dict) or not {"element_kind", "element_id", "feature_key"} <= set(
            edge
        ):
            errors.append(
                {"index": index, "reason": "needs element_kind, element_id and feature_key"}
            )
            continue
        kind, eid, fk = edge["element_kind"], str(edge["element_id"]), str(edge["feature_key"])
        if kind not in ELEMENT_KINDS:
            errors.append({"index": index, "reason": f"unknown element_kind {kind!r}"})
        elif (kind, eid) not in known_elements:
            errors.append({"index": index, "reason": f"model has no {kind} {eid!r}"})
        elif fk not in known_features:
            errors.append({"index": index, "reason": f"source has no feature {fk!r}"})
        elif (kind, eid, fk) not in seen:
            seen.add((kind, eid, fk))
            accepted.append({"element_kind": kind, "element_id": eid, "feature_key": fk})
    return accepted, errors
