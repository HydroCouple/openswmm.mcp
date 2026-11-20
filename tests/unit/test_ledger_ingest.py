"""Ingest parsers, model geometry and the sources_root commitment."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from openswmm_mcp.ledger import ingest, merkle
from openswmm_mcp.ledger.model_geometry import model_elements
from openswmm_mcp.session import SimSession
from openswmm_mcp.tools.model import _open_solver

DATA = Path(__file__).parent / "data"
_SUBCATCHMENT_SECTIONS = {
    "SUBCATCHMENTS",
    "SUBAREAS",
    "INFILTRATION",
    "POLYGONS",
    "COVERAGES",
    "LOADINGS",
}


def _write(folder: Path, name: str, content: str | dict) -> Path:
    path = folder / name
    path.write_text(content if isinstance(content, str) else json.dumps(content))
    return path


def _fc(features, **extra):
    return {"type": "FeatureCollection", "features": features, **extra}


def _feat(geom, props=None, **kw):
    return {"type": "Feature", "geometry": geom, "properties": props or {}, **kw}


# --- hashing, formats ---------------------------------------------------------------------
def test_sha256_and_detect_format(output_dir):
    p = _write(output_dir, "a.txt", "hello")
    assert ingest.sha256_file(p) == hashlib.sha256(b"hello").hexdigest()
    assert ingest.detect_format(p) == "txt"
    assert ingest.detect_format("x.MD") == "md"
    assert ingest.detect_format("x.csv") == "csv"
    assert ingest.detect_format("x.gpkg") == "gpkg"
    with pytest.raises(ValueError):
        ingest.detect_format("x.xyz")
    gj = _write(output_dir, "b.json", _fc([]))
    assert ingest.detect_format(gj) == "geojson"
    plain = _write(output_dir, "c.json", {"a": 1})
    assert ingest.detect_format(plain) == "json"
    assert ingest.detect_format(_write(output_dir, "d.json", "{not json")) == "json"


def test_map_units():
    assert (
        ingest.map_units("[OPTIONS]\nX 1\n[MAP]\nDIMENSIONS 0 0 1 1\nUnits      Feet\n") == "feet"
    )
    assert ingest.map_units("[MAP]\nUNITS Meters ; note\n") == "meters"
    assert ingest.map_units("[MAP]\nunits degrees") == "degrees"
    assert ingest.map_units("[OPTIONS]\nUnits Feet\n") == "none"  # wrong section
    assert ingest.map_units("[OPTIONS]\nA 1\n") == "none"
    assert ingest.map_units("[MAP]\nUnits Furlongs") == "none"


# --- GeoJSON ------------------------------------------------------------------------------
def test_read_geojson_geometries_keys_and_unknown_crs(output_dir):
    path = _write(
        output_dir,
        "g.geojson",
        _fc(
            [
                _feat({"type": "Point", "coordinates": [1, 2]}, {"n": "J1"}, id=7),
                _feat({"type": "LineString", "coordinates": [[0, 0], [3, 4]]}),
                _feat({"type": "MultiPoint", "coordinates": [[5, 5], [-1, 9]]}),
                _feat(
                    {"type": "MultiLineString", "coordinates": [[[0, 0], [1, 1]], [[2, 2], [3, 8]]]}
                ),
                _feat({"type": "Polygon", "coordinates": [[[0, 0], [4, 0], [4, 2], [0, 0]]]}),
                _feat(
                    {"type": "MultiPolygon", "coordinates": [[[[0, 0], [1, 0], [1, 1], [0, 0]]]]}
                ),
                _feat(None),
            ]
        ),
    )
    feats, label, warnings = ingest.read_geojson(path)
    assert label == "unknown"
    assert any("no crs member" in w for w in warnings)
    assert [f["feature_key"] for f in feats] == ["7", "1", "2", "3", "4", "5", "6"]
    assert feats[0]["bbox"] == (1.0, 2.0, 1.0, 2.0)
    assert feats[1]["bbox"] == (0.0, 0.0, 3.0, 4.0)
    assert feats[2]["bbox"] == (-1.0, 5.0, 5.0, 9.0)
    assert feats[3]["bbox"] == (0.0, 0.0, 3.0, 8.0)
    assert feats[4]["bbox"] == (0.0, 0.0, 4.0, 2.0)
    assert feats[6]["bbox"] is None
    assert feats[0]["props"] == {"n": "J1"}


def test_read_geojson_crs_precedence(output_dir):
    crs = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2236"}}
    path = _write(
        output_dir, "g.geojson", _fc([_feat({"type": "Point", "coordinates": [1, 2]})], crs=crs)
    )
    assert ingest.read_geojson(path)[1] == "EPSG:2236"
    feats, label, warnings = ingest.read_geojson(path, crs="local:feet")
    assert label == "local:feet" and not warnings
    crs84 = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    path2 = _write(output_dir, "h.geojson", _fc([], crs=crs84))
    assert ingest.read_geojson(path2)[1] == "EPSG:4326"


def test_read_geojson_single_feature_and_duplicates(output_dir):
    single = _write(
        output_dir, "s.geojson", _feat({"type": "Point", "coordinates": [1, 1]}, id="a")
    )
    assert ingest.read_geojson(single)[0][0]["feature_key"] == "a"
    bare = _write(output_dir, "b.geojson", {"type": "Point", "coordinates": [1, 1]})
    assert ingest.read_geojson(bare)[0][0]["bbox"] == (1.0, 1.0, 1.0, 1.0)
    dup = _write(
        output_dir,
        "d.geojson",
        _fc([_feat(None, id="x"), _feat(None, id="x")]),
    )
    feats, _, warnings = ingest.read_geojson(dup)
    assert [f["feature_key"] for f in feats] == ["x", "1"]
    assert any("duplicate" in w for w in warnings)


@pytest.mark.parametrize(
    "content",
    [
        "{broken",
        "[1, 2]",
        {"type": "Topology"},
        {"type": "FeatureCollection"},
        _fc([{"type": "Nope"}]),
        _fc([_feat({"type": "Blob", "coordinates": []})]),
        _fc([_feat({"type": "Point", "coordinates": ["a", "b"]})]),
        _fc([_feat({"type": "Point", "coordinates": [float("inf"), 1]})]),
    ],
)
def test_read_geojson_rejects_bad_files(output_dir, content):
    with pytest.raises(ValueError):
        ingest.read_geojson(_write(output_dir, "bad.geojson", content))


# --- text ---------------------------------------------------------------------------------
def test_chunk_text_offsets_and_hard_split():
    text = "alpha\n\nbeta\n\n" + "x" * 40 + "\n\ngamma"
    chunks = ingest.chunk_text(text, 20)
    for offset, chunk in chunks:
        assert text[offset : offset + len(chunk)] == chunk
        assert len(chunk) <= 20
    assert chunks[0] == (0, "alpha\n\nbeta")
    assert "".join(c for _, c in chunks if set(c) == {"x"}) == "x" * 40
    assert chunks[-1][1] == "gamma"
    assert ingest.chunk_text("", 20) == []
    assert ingest.chunk_text("  \n\n ", 20) == []
    # default size packs small paragraphs together, big ones split at 1500
    big = "a" * 3200
    sizes = [len(c) for _, c in ingest.chunk_text(big)]
    assert sizes == [1500, 1500, 200]


def test_scan_mentions_boundaries():
    text = (
        "Flooding at J2, (C2) and J22; also C2a and j2.\n\n"
        'End of report: J2. See "C2" and [J2]; ST1/J2 is one token.'
    )
    chunks = ingest.chunk_text(text, 100)
    assert len(chunks) == 2
    hits = ingest.scan_mentions(chunks, {"node": {"J2", "J22"}, "link": {"C2"}})
    got = [(h["element_kind"], h["element_id"], h["chunk_index"]) for h in hits]
    assert got == [
        ("node", "J2", 0),
        ("link", "C2", 0),
        ("node", "J22", 0),
        ("node", "J2", 1),
        ("link", "C2", 1),
        ("node", "J2", 1),
    ]
    for h in hits:
        assert text[h["span_start"] : h["span_end"]] == h["element_id"]
        assert h["element_id"] in h["text"]
    # no hit for J2 inside J22 / C2a / ST1/J2
    spans = {(h["element_id"], h["span_start"]) for h in hits}
    assert ("J2", text.index("J22")) not in spans
    assert not any(h["span_start"] == text.index("C2a") for h in hits)


def test_scan_mentions_long_context_clipped():
    text = "z" * 200 + " J2 " + "z" * 200
    (hit,) = ingest.scan_mentions(ingest.chunk_text(text), {"node": {"J2"}})
    assert hit["text"].count("z") == 160 + 0 or len(hit["text"]) <= 162
    assert hit["span_start"] == 201


# --- CSV ----------------------------------------------------------------------------------
def test_csv_span_iso_and_us_formats(output_dir):
    iso = _write(
        output_dir,
        "iso.csv",
        "time,mm\n2020-03-01T00:05:00,1\n2020-03-01T00:00:00,0\n2020-03-02,2\n",
    )
    r = ingest.csv_span(iso)
    assert r["columns"] == ["time", "mm"]
    assert r["start"] == "2020-03-01T00:00:00.000000Z"
    assert r["end"] == "2020-03-02T00:00:00.000000Z"
    assert (r["rows"], r["parsed"]) == (3, 3)
    us = _write(output_dir, "us.csv", "date,time,v\n03/01/2020,00:00,1\n03/01/2020,06:30,2\n")
    r = ingest.csv_span(us)
    assert r["start"] == "2020-03-01T00:00:00.000000Z"
    assert r["end"] == "2020-03-01T06:30:00.000000Z"
    assert r["columns"] == ["date", "time", "v"]
    full = _write(output_dir, "full.csv", "t,v\n03/01/2020 10:00:30,1\n03/01/2020,2\n")
    assert ingest.csv_span(full)["end"] == "2020-03-01T10:00:30.000000Z"


def test_csv_span_without_timestamps(output_dir):
    r = ingest.csv_span(_write(output_dir, "n.csv", "a,b\nx,y\n"))
    assert r["start"] is None and r["end"] is None and r["rows"] == 0


# --- id_match / manual --------------------------------------------------------------------
ELEMENTS = [
    {"kind": "node", "id": "J1"},
    {"kind": "node", "id": "J2"},
    {"kind": "link", "id": "C1"},
    {"kind": "gage", "id": "G1"},
]
FEATURES = [
    {"feature_key": "a", "props": {"name": "J1"}},
    {"feature_key": "b", "props": {"name": "j2"}},
    {"feature_key": "c", "props": {"name": "C1"}},
    {"feature_key": "d", "props": {"name": "C1"}},
    {"feature_key": "e", "props": {"name": 5}},
    {"feature_key": "f", "props": {}},
]


def test_id_match_is_case_sensitive_and_reports_unmatched():
    edges, unmatched = ingest.id_match(ELEMENTS, FEATURES, "name")
    assert edges == [
        {"element_kind": "node", "element_id": "J1", "feature_key": "a"},
        {"element_kind": "link", "element_id": "C1", "feature_key": "c"},
        {"element_kind": "link", "element_id": "C1", "feature_key": "d"},
    ]
    assert unmatched == [
        {"element_kind": "node", "element_id": "J2"},
        {"element_kind": "gage", "element_id": "G1"},
    ]


def test_id_match_kinds_and_non_string_values():
    edges, unmatched = ingest.id_match(ELEMENTS, FEATURES, "name", kinds=["link"])
    assert [e["feature_key"] for e in edges] == ["c", "d"] and unmatched == []
    edges, _ = ingest.id_match([{"kind": "node", "id": "5"}], FEATURES, "name")
    assert edges == [{"element_kind": "node", "element_id": "5", "feature_key": "e"}]


def test_manual_edges_validation():
    good = {"element_kind": "node", "element_id": "J1", "feature_key": "a"}
    accepted, errors = ingest.manual_edges(
        [
            good,
            dict(good),
            {"element_kind": "node", "element_id": "J9", "feature_key": "a"},
            {"element_kind": "node", "element_id": "J1", "feature_key": "zz"},
            {"element_kind": "pump", "element_id": "J1", "feature_key": "a"},
            {"element_kind": "node"},
            "nonsense",
        ],
        ELEMENTS,
        FEATURES,
    )
    assert accepted == [good]
    assert [e["index"] for e in errors] == [2, 3, 4, 5, 6]
    assert "J9" in errors[0]["reason"] and "zz" in errors[1]["reason"]


# --- sources_root -------------------------------------------------------------------------
def _ref_sources_root(triples):
    def h(b):
        return hashlib.sha256(b).digest()

    triples = sorted(set(triples))
    level = [h(b"\x00" + b"openswmm-ledger/v1/source|" + "|".join(t).encode()) for t in triples]
    n = len(level)
    while len(level) > 1:
        nxt = [h(b"\x01" + level[i] + level[i + 1]) for i in range(0, len(level) - 1, 2)]
        if len(level) % 2:
            nxt.append(level[-1])
        level = nxt
    return h(b"\x02" + n.to_bytes(8, "big") + level[0]).hex()


def test_sources_root_known_answer():
    s1, s2, s3 = "a" * 64, "b" * 64, "c" * 64
    entries = [
        {"source_id": "pipes", "sha256": s2, "role": "network"},
        {"source_id": "nodes", "sha256": s1, "role": "nodes"},
        {"source_id": "pipes", "sha256": s1, "role": "network"},
        {"source_id": "rain", "sha256": s3, "role": "rain"},
    ]
    triples = [(e["source_id"], e["sha256"], e["role"]) for e in entries]
    assert merkle.sources_root(entries) == _ref_sources_root(triples)
    assert merkle.sources_root(entries[:1]) == _ref_sources_root(triples[:1])
    assert merkle.sources_root(entries[:3]) == _ref_sources_root(triples[:3])
    # order and duplicates do not matter; any change does
    assert merkle.sources_root(list(reversed(entries)) + entries[:1]) == merkle.sources_root(
        entries
    )
    changed = [dict(entries[0], sha256=s3)] + entries[1:]
    assert merkle.sources_root(changed) != merkle.sources_root(entries)
    assert merkle.sources_root([]) is None
    leaf = hashlib.sha256(b"\x00openswmm-ledger/v1/source|x|y|z").digest()
    assert merkle.source_leaf("x|y|z") == leaf


# --- import hygiene -----------------------------------------------------------------------
def test_ingest_imports_no_engine():
    code = (
        "import sys, openswmm_mcp.ledger.ingest; "
        "print(any(m=='openswmm' or m.startswith('openswmm.') for m in sys.modules))"
    )
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr


# --- model geometry (engine) --------------------------------------------------------------
async def _open(session_manager, inp: Path, name: str) -> SimSession:
    folder = session_manager.session_dir(name)
    dest = folder / inp.name
    shutil.copy(inp, dest)
    session = SimSession(
        name,
        folder,
        inp_path=str(dest),
        rpt_path=str(folder / "m.rpt"),
        out_path=str(folder / "m.out"),
    )
    await session_manager.add(session)
    await session.call(_open_solver, session, False)
    return session


def _without_subcatchments(folder: Path) -> Path:
    lines, keep = [], True
    for line in (DATA / "site_drainage_model.inp").read_text().splitlines():
        if line.startswith("["):
            keep = line.strip("[] ").upper() not in _SUBCATCHMENT_SECTIONS
        if keep:
            lines.append(line)
    return _write(folder, "nosub.inp", "\n".join(lines) + "\n")


async def test_reference_model_geometry(session_manager, output_dir):
    session = await _open(session_manager, DATA / "site_drainage_model.inp", "geo")
    elements, meta = await session.call(model_elements, session)
    by_kind = {}
    for e in elements:
        by_kind.setdefault(e["kind"], {})[e["id"]] = e
    assert {k: len(v) for k, v in by_kind.items()} == {
        "node": 12,
        "link": 11,
        "subcatchment": 7,
        "gage": 1,
    }
    assert meta["map_units"] == "feet"
    assert meta["engine_crs"] == ""
    assert meta["inp_sha256"] == ingest.sha256_file(session.inp_path)
    assert meta["no_geometry"] == []
    assert by_kind["node"]["J1"]["geometry"] == {
        "type": "Point",
        "coordinates": pytest.approx([648.532, 1043.713]),
    }
    c2 = by_kind["link"]["C2"]
    assert len(c2["geometry"]["coordinates"]) == 5
    assert c2["geometry"]["coordinates"][0] == by_kind["node"]["J2"]["geometry"]["coordinates"]
    assert (c2["from_node"], c2["to_node"]) == ("J2", "J11")
    ring = by_kind["subcatchment"]["S1"]["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1] and len(ring) == 23
    assert by_kind["gage"]
    (gage,) = by_kind["gage"].values()
    assert gage["geometry"]["coordinates"] == pytest.approx([-175.841, 1212.778])
    json.dumps(elements)  # plain JSON types only


async def test_model_without_subcatchments(session_manager, output_dir):
    # street_inlet_junction.inp has no [MAP] and no subcatchments
    session = await _open(session_manager, DATA / "street_inlet_junction.inp", "street")
    elements, meta = await session.call(model_elements, session)
    assert not [e for e in elements if e["kind"] == "subcatchment"]
    assert meta["map_units"] == "none"
    assert [e for e in elements if e["kind"] == "node"]
    assert not [e for e in elements if e["kind"] == "gage"]


async def test_stripped_reference_model_has_no_subcatchments(session_manager, output_dir):
    path = _without_subcatchments(output_dir)
    session = await _open(session_manager, path, "nosub")
    elements, meta = await session.call(model_elements, session)
    kinds = [e["kind"] for e in elements]
    assert "subcatchment" not in kinds and kinds.count("node") == 12
    assert meta["map_units"] == "feet"
