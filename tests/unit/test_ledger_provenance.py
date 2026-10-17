"""Source provenance end to end: register, link, locate, tally commit and degraded modes.

Fixtures are written under the test's output folder next to a copy of the site model.
Tests that need ``shapely`` and ``pyproj`` (the ``ledger`` extra) skip when they are
missing; the id_match, mention, register and bitemporal tests do not.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from openswmm_mcp.ledger import merkle

from .conftest import DATA, Tools

J2 = (1221.281, 1005.760)
J11 = (1334.823, 497.305)
C2_LINE = [[1221.281, 1005.760], [1321.339, 774.591], [1334.823, 497.305]]


def _spatial():
    """Skip the calling test when the ledger extra (shapely, pyproj) is not installed."""
    pytest.importorskip("shapely")
    pytest.importorskip("pyproj")


def _point(x, y, fid, key):
    return {
        "type": "Feature",
        "id": key,
        "properties": {"FACILITYID": fid},
        "geometry": {"type": "Point", "coordinates": [x, y]},
    }


def _line(coords, fid, key):
    return {
        "type": "Feature",
        "id": key,
        "properties": {"FACILITYID": fid},
        "geometry": {"type": "LineString", "coordinates": coords},
    }


def _write_geojson(path: Path, features, crs: str | None = None) -> None:
    doc = {"type": "FeatureCollection", "features": features}
    if crs:
        doc["crs"] = {"type": "name", "properties": {"name": crs}}
    path.write_text(json.dumps(doc))


def _network(extra=()):
    return [
        _point(*J2, "J2", "n2"),
        _point(*J11, "J11", "n11"),
        _line(C2_LINE, "C2", "p2"),
        _point(5000, 5000, "J1", "far"),
        *extra,
    ]


@pytest.fixture
async def ledger_tools(output_dir: Path, monkeypatch):
    """A client to a server with the ledger toolset, as verified-narrative needs."""
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    monkeypatch.setenv("OPENSWMM_MCP_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("OPENSWMM_MCP_TOOLSETS", "core,ledger")
    from openswmm_mcp.server import build_server

    async with Client(build_server()) as client:
        yield Tools(client)


@pytest.fixture
async def model(ledger_tools, output_dir: Path):
    """The site model opened and run to the end as session ``s``."""
    shutil.copy(DATA / "site_drainage_model.inp", output_dir / "model.inp")
    await ledger_tools("open_model", path="model.inp", session_id="s")
    await ledger_tools("run", session_id="s", until="end")
    return ledger_tools


async def _register_network(tools, output_dir: Path, extra=()):
    _write_geojson(output_dir / "pipes.geojson", _network(extra))
    return await tools(
        "sources", action="register", path="pipes.geojson", role="network", crs="local:feet"
    )


async def _link_ids(tools):
    return await tools(
        "sources",
        action="link",
        session_id="s",
        source_id="pipes",
        rule="id_match",
        params={"key": "FACILITYID"},
    )


async def _expect(tools, slug: str, tool: str, **args):
    with pytest.raises(ToolError) as err:
        await tools(tool, **args)
    assert slug in str(err.value)
    return str(err.value)


# --- 1, 3, 4: id_match, mentions, rain register (no spatial extra) --------------------------


async def test_id_match_links_node_and_link_lineage(model, output_dir):
    reg = await _register_network(model, output_dir)
    assert reg["status"] == "new"
    assert reg["source"]["crs"] == "local:feet"
    assert reg["source"]["features"] == 4
    link = await _link_ids(model)
    assert link["added"] >= 2
    c2 = await model(
        "sources", action="lineage", session_id="s", element_kind="link", element_id="C2"
    )
    assert [(e["source_id"], e["feature_key"]) for e in c2["edges"]] == [("pipes", "p2")]
    j2 = await model(
        "sources", action="lineage", session_id="s", element_kind="node", element_id="J2"
    )
    assert [(e["source_id"], e["feature_key"]) for e in j2["edges"]] == [("pipes", "n2")]


async def test_report_mentions_match_whole_ids_only(model, output_dir):
    (output_dir / "report.md").write_text(
        "# Inspection\n\nJunction J2 was surcharged. Pipe C2 is cracked.\n\n"
        "J22 and C2a are unrelated.\n"
    )
    reg = await model(
        "sources", action="register", path="report.md", role="document", session_id="s"
    )
    assert reg["source"]["mentions"] == 2
    found = await model("sources", action="mentions", source_id="report")
    assert sorted(m["element_id"] for m in found["mentions"]) == ["C2", "J2"]
    for m in found["mentions"]:
        assert (
            m["text"][m["span"][0] : m["span"][1]] in ("J2", "C2") or m["element_id"] in m["text"]
        )
    by_element = await model("sources", action="mentions", element_kind="node", element_id="J2")
    assert by_element["count"] == 1


async def test_csv_rain_validity_starts_at_first_timestamp(model, output_dir):
    (output_dir / "rain.csv").write_text(
        "time,rain_in_hr\n2024-03-01T00:00:00,0.1\n2024-03-01T06:00:00,0.4\n2024-03-02T12:30:00,0.0\n"
    )
    reg = await model("sources", action="register", path="rain.csv", role="rain")
    assert reg["source"]["format"] == "csv"
    assert reg["source"]["valid_from"].startswith("2024-03-01T00:00:00")
    # Only the first timestamp is used (valid_from); a series is not retired by its last row.
    assert reg["source"]["valid_to"] is None


# --- 2: nearest ------------------------------------------------------------------------------


async def test_nearest_picks_feature_ten_feet_off(model, output_dir):
    _spatial()
    _write_geojson(
        output_dir / "survey_pts.geojson",
        [_point(J2[0] + 10, J2[1], "X", "off10"), _point(5000, 5000, "Y", "far")],
    )
    await model(
        "sources",
        action="register",
        path="survey_pts.geojson",
        role="survey",
        crs="local:feet",
    )
    link = await model(
        "sources",
        action="link",
        session_id="s",
        source_id="survey_pts",
        rule="nearest",
        params={"max_distance": 15, "kinds": ["node"]},
    )
    edges = {e["feature_key"]: e for e in link["edges"]}
    assert set(edges) == {"off10"}
    assert edges["off10"]["element_id"] == "J2"
    assert abs(edges["off10"]["distance"] - 10.0) < 0.01


# --- 5, 12: locate, buffer widening ----------------------------------------------------------


async def test_locate_link_carries_provenance_and_fact_ids(model, output_dir):
    _spatial()
    await _register_network(model, output_dir)
    (output_dir / "report.md").write_text("Junction J2 was surcharged. Pipe C2 is cracked.\n")
    await model("sources", action="register", path="report.md", role="document", session_id="s")
    await _link_ids(model)
    await model("tally", action="commit", session_id="s")
    loc = await model("locate", session_id="s", element_kind="link", element_id="C2", buffer=25)
    keys = {f["feature_key"]: f for f in loc["features"]}
    assert "p2" in keys and "n2" in keys and "far" not in keys
    assert keys["p2"]["distance"] == 0
    assert all(f["relation"] for f in loc["features"])
    assert [(m["element_id"]) for m in loc["mentions"]].count("C2") == 1
    prov = loc["provenance"]
    assert prov["buffer"] == 25 and prov["buffer_unit"] == "ft"
    assert prov["crs"] == "local:feet" and prov["crs_source"] == "map_units"
    assert prov["spatial"] is True and prov["tally_id"] == "s/model"
    assert {s["id"] for s in prov["sources"]} >= {"pipes", "report"}
    assert loc["excluded"]["outside_buffer"] >= 1
    c2 = next(e for e in loc["elements"] if e["id"] == "C2")
    assert c2["fact_ids"]


async def test_wider_buffer_leaves_fewer_outside(model, output_dir):
    _spatial()
    await _register_network(model, output_dir, extra=[_point(J2[0] + 30, J2[1], "X", "off30")])
    narrow = await model("locate", session_id="s", element_kind="link", element_id="C2", buffer=5)
    wide = await model("locate", session_id="s", element_kind="link", element_id="C2", buffer=50)
    assert wide["excluded"]["outside_buffer"] < narrow["excluded"]["outside_buffer"]
    assert len(wide["features"]) >= len(narrow["features"])


# --- 6, 7: CRS ------------------------------------------------------------------------------


async def test_declared_crs_source_is_excluded_not_refused(model, output_dir):
    _spatial()
    await _register_network(model, output_dir)
    _write_geojson(
        output_dir / "survey.geojson",
        [_point(1221.0, 1005.0, "J2", "e1")],
        crs="urn:ogc:def:crs:EPSG::2236",
    )
    reg = await model("sources", action="register", path="survey.geojson", role="survey")
    assert "2236" in reg["source"]["crs"]
    loc = await model("locate", session_id="s", element_kind="node", element_id="J2")
    skipped = {x["source_id"]: x for x in loc["excluded"]["crs_incompatible"]}
    assert "survey" in skipped and skipped["survey"]["slug"] == "crs_mismatch"
    assert any(f["source_id"] == "pipes" for f in loc["features"])
    msg = await _expect(
        model,
        "crs_mismatch",
        "sources",
        action="link",
        session_id="s",
        source_id="survey",
        rule="nearest",
        params={"max_distance": 20},
    )
    assert "survey" in msg


async def test_geojson_without_crs_is_unknown_and_excluded(model, output_dir):
    _spatial()
    await _register_network(model, output_dir)
    _write_geojson(output_dir / "loose.geojson", [_point(*J2, "J2", "q1")])
    reg = await model("sources", action="register", path="loose.geojson", role="survey")
    assert reg["source"]["crs"] == "unknown"
    loc = await model("locate", session_id="s", element_kind="node", element_id="J2")
    skipped = {x["source_id"]: x for x in loc["excluded"]["crs_incompatible"]}
    assert skipped["loose"]["slug"] == "crs_unknown"


# --- 8: bitemporal --------------------------------------------------------------------------


async def test_retired_source_visible_as_known_before_retirement(model, output_dir):
    _spatial()
    await _register_network(model, output_dir)
    before = await model("locate", session_id="s", element_kind="link", element_id="C2")
    known = before["provenance"]["as_known"]
    await model("sources", action="retire", source_id="pipes")
    then = await model(
        "locate", session_id="s", element_kind="link", element_id="C2", as_known=known
    )
    now = await model("locate", session_id="s", element_kind="link", element_id="C2")
    assert len(then["features"]) > len(now["features"]) == 0
    assert now["excluded"]["retired"] >= 1 and then["excluded"]["retired"] == 0
    listed = await model("sources", action="list")
    assert [s["status"] for s in listed["sources"] if s["source_id"] == "pipes"] == ["retired"]


async def test_valid_to_in_the_past_excludes_by_as_of(model, output_dir):
    _spatial()
    _write_geojson(output_dir / "pipes.geojson", _network())
    reg = await model(
        "sources",
        action="register",
        path="pipes.geojson",
        role="network",
        crs="local:feet",
        acquired="2019-01-01T00:00:00Z",
    )
    assert reg["source"]["valid_from"].startswith("2019-01-01")
    await model("sources", action="retire", source_id="pipes", valid_to="2021-01-01T00:00:00Z")
    args = {"session_id": "s", "element_kind": "link", "element_id": "C2"}
    inside = await model("locate", as_of="2020-06-01T00:00:00Z", **args)
    after = await model("locate", as_of="2022-06-01T00:00:00Z", **args)
    early = await model("locate", as_of="2018-06-01T00:00:00Z", **args)
    assert inside["features"] and not after["features"] and not early["features"]
    assert after["excluded"]["retired"] >= 1
    assert early["excluded"]["not_yet_valid"] >= 1


# --- 9: update ------------------------------------------------------------------------------


async def test_changed_file_is_an_update_with_stale_edge_warning(model, output_dir):
    first = await _register_network(model, output_dir)
    await _link_ids(model)
    changed = await _register_network(model, output_dir, extra=[_point(1, 1, "J3", "added")])
    assert first["status"] == "new" and changed["status"] == "updated"
    assert changed["source"]["sha256"] != first["source"]["sha256"]
    assert any("lineage edges refer to the previous version" in w for w in changed["warnings"])
    listed = await model("sources", action="list")
    rows = [s for s in listed["sources"] if s["source_id"] == "pipes"]
    assert len(rows) == 2  # an update is two rows: the old version closed, the new active
    by_status = {r["status"]: r["sha256"] for r in rows}
    assert by_status == {
        "active": changed["source"]["sha256"],
        "superseded": first["source"]["sha256"],
    }
    loc = await model("locate", session_id="s", element_kind="node", element_id="J2")
    assert loc["excluded"]["retired"] == 0  # an update is not a retirement
    again = await _register_network(model, output_dir, extra=[_point(1, 1, "J3", "added")])
    assert again["status"] == "unchanged"


# --- 10: degraded mode ----------------------------------------------------------------------


async def test_degraded_locate_without_spatial_modules(model, output_dir, monkeypatch):
    await _register_network(model, output_dir)
    await _link_ids(model)
    import openswmm_mcp.tools.ledger as ledger_module

    monkeypatch.setattr(ledger_module, "_spatial_available", lambda: False)
    loc = await model("locate", session_id="s", element_kind="link", element_id="C2")
    assert loc["provenance"]["spatial"] is False
    assert any("shapely" in w or "spatial" in w.lower() for w in loc["warnings"])
    assert [f["feature_key"] for f in loc["features"]] == ["p2"]
    assert loc["features"][0]["relation"] == "lineage"
    await _expect(model, "DEPENDENCY_MISSING", "locate", session_id="s", point=[1221.0, 1005.0])
    await _expect(
        model,
        "DEPENDENCY_MISSING",
        "sources",
        action="link",
        session_id="s",
        source_id="pipes",
        rule="nearest",
        params={"max_distance": 5},
    )


# --- 11: sources_root -----------------------------------------------------------------------


async def test_sources_root_matches_listed_sources_and_changes_with_update(model, output_dir):
    first = await model("tally", action="commit", session_id="s")
    assert first["sources_root"] is None
    await _register_network(model, output_dir)
    await _link_ids(model)
    one = await model("tally", action="commit", session_id="s")
    assert one["sources_root"]
    listed = await model("sources", action="list")
    entries = [
        {"source_id": s["source_id"], "sha256": s["sha256"], "role": s["role"]}
        for s in listed["sources"]
        if s["source_id"] == "pipes"
    ]
    assert one["sources_root"] == merkle.sources_root(entries)
    await _register_network(model, output_dir, extra=[_point(1, 1, "J3", "added")])
    await _link_ids(model)
    two = await model("tally", action="commit", session_id="s")
    assert two["sources_root"] and two["sources_root"] != one["sources_root"]


# --- review fixes --------------------------------------------------------------------------


_NO_GEOM = {
    "type": "Feature",
    "id": "n11-nogeom",
    "properties": {"FACILITYID": "J11"},
    "geometry": None,
}


async def test_id_match_and_manual_see_null_geometry_features(model, output_dir):
    _write_geojson(output_dir / "table.geojson", [_NO_GEOM, _point(*J2, "J2", "n2")])
    await model("sources", action="register", path="table.geojson", role="nodes", crs="local:feet")
    link = await model(
        "sources", action="link", session_id="s", source_id="table", rule="id_match",
        params={"key": "FACILITYID", "kinds": ["node"]},
    )  # fmt: skip
    matched = {(e["element_id"], e["feature_key"]) for e in link["edges"]}
    assert ("J11", "n11-nogeom") in matched and ("J2", "n2") in matched
    manual = await model(
        "sources", action="link", session_id="s", source_id="table", rule="manual",
        params={"edges": [{"element_kind": "node", "element_id": "J10",
                           "feature_key": "n11-nogeom"}]},
    )  # fmt: skip
    assert manual["added"] == 1 and manual["unmatched"]["count"] == 0


async def test_nearest_skips_null_geometry_with_warning(model, output_dir):
    _spatial()
    _write_geojson(output_dir / "table.geojson", [_NO_GEOM, _point(J2[0] + 3, J2[1], "x", "k")])
    await model("sources", action="register", path="table.geojson", role="nodes", crs="local:feet")
    link = await model(
        "sources", action="link", session_id="s", source_id="table", rule="nearest",
        params={"max_distance": 5, "kinds": ["node"]},
    )  # fmt: skip
    assert [(e["element_id"], e["feature_key"]) for e in link["edges"]] == [("J2", "k")]
    assert any("no geometry" in w for w in link["warnings"])


async def test_corrected_csv_with_same_span_is_an_update(model, output_dir):
    rain = output_dir / "rain.csv"
    rain.write_text("time,rain\n2024-03-01T00:00:00,0.1\n2024-03-01T06:00:00,0.4\n")
    first = await model("sources", action="register", path="rain.csv", role="rain")
    assert first["source"]["valid_from"].startswith("2024-03-01T00:00:00")
    rain.write_text("time,rain\n2024-03-01T00:00:00,0.1\n2024-03-01T06:00:00,0.5\n")
    second = await model("sources", action="register", path="rain.csv", role="rain")
    assert second["status"] == "updated"
    listed = await model("sources", action="list")
    rows = [r for r in listed["sources"] if r["source_id"] == "rain"]
    assert sorted(r["status"] for r in rows) == ["active", "superseded"]


async def test_one_bad_geometry_skips_that_feature_only(model, output_dir):
    _spatial()
    bad = {
        "type": "Feature",
        "id": "flat",
        "properties": {"FACILITYID": "x"},
        "geometry": {"type": "Polygon", "coordinates": [[list(J2), list(J11)]]},
    }
    await _register_network(model, output_dir, extra=[bad])
    loc = await model("locate", session_id="s", element_kind="node", element_id="J2")
    keys = {f["feature_key"] for f in loc["features"]}
    assert {"n2", "p2"} <= keys and "flat" not in keys
    assert loc["excluded"]["crs_incompatible"] == []
    assert any("'flat'" in w for w in loc["warnings"])
    link = await model(
        "sources", action="link", session_id="s", source_id="pipes", rule="nearest",
        params={"max_distance": 5, "kinds": ["node"]},
    )  # fmt: skip
    assert ("J2", "n2") in {(e["element_id"], e["feature_key"]) for e in link["edges"]}
    assert any("'flat'" in w for w in link["warnings"])


async def test_crs_none_refused_for_spatial_layers(model, output_dir):
    _write_geojson(output_dir / "pipes.geojson", _network())
    msg = await _expect(
        model, "[VALIDATION_ERROR] bad_value:", "sources", action="register",
        path="pipes.geojson", role="network", crs="none",
    )  # fmt: skip
    assert "local:" in msg


async def test_degraded_provenance_lists_only_sources_read(model, output_dir, monkeypatch):
    await _register_network(model, output_dir)
    await _link_ids(model)
    _write_geojson(output_dir / "other.geojson", [_point(*J2, "zz", "o1")])
    await model("sources", action="register", path="other.geojson", role="survey", crs="local:feet")
    import openswmm_mcp.tools.ledger as ledger_module

    monkeypatch.setattr(ledger_module, "_spatial_available", lambda: False)
    loc = await model("locate", session_id="s", element_kind="link", element_id="C2")
    assert [s["id"] for s in loc["provenance"]["sources"]] == ["pipes"]
