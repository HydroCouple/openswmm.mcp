"""Spatial helpers for the ledger and the require_spatial refusal."""

from __future__ import annotations

import sys

import pytest

from openswmm_mcp.dependencies import require_spatial
from openswmm_mcp.errors import ToolError


def test_require_spatial_refusal_text(monkeypatch):
    monkeypatch.setitem(sys.modules, "shapely", None)
    with pytest.raises(ToolError) as exc:
        require_spatial("locate with a point")
    text = str(exc.value)
    assert "DEPENDENCY_MISSING" in text
    assert "locate with a point needs shapely and pyproj." in text
    assert "pip install 'openswmm.mcp[ledger]'" in text
    assert "mentions work without them." in text


pytest.importorskip("shapely")
pytest.importorskip("pyproj")

from openswmm_mcp.ledger import common, spatial  # noqa: E402


def pt(x, y):
    return {"type": "Point", "coordinates": [x, y]}


def line(*c):
    return {"type": "LineString", "coordinates": [list(p) for p in c]}


def test_local_frame_unit_and_usable():
    f = spatial.resolve_frame("local:feet", ["local:feet", "local:meters", "unknown", "EPSG:4326"])
    assert f.local and f.unit == "ft" and f.working_crs == "local:feet"
    assert f.usable == ["local:feet"]
    assert {k: v.slug for k, v in f.excluded.items()} == {
        "local:meters": "crs_mismatch",
        "unknown": "crs_unknown",
        "EPSG:4326": "crs_mismatch",
    }
    assert f.reprojected == []
    assert spatial.resolve_frame("local:none", []).unit == "map unit"


def test_local_degrees_refused():
    with pytest.raises(common.CrsRefusal) as exc:
        spatial.resolve_frame("local:degrees", [])
    assert exc.value.slug == "crs_required"


def test_within_distance_and_relation():
    f = spatial.resolve_frame("local:feet", ["local:feet"])
    anchor = spatial.element_geometry(
        {"kind": "link", "id": "C1", "geometry": line((0, 0), (10, 0))}, f
    )
    cands = [
        ("on", spatial.source_geometry(line((5, -5), (5, 5)), "local:feet", f)),
        ("near", spatial.source_geometry(pt(5, 3), "local:feet", f)),
        ("far", spatial.source_geometry(pt(5, 40), "local:feet", f)),
    ]
    got = spatial.within(anchor, cands, 25.0)
    assert got == [("on", 0.0, "intersects"), ("near", 3.0, "within_buffer")]


def test_nearest_ties_lowest_key():
    a = spatial.shape(pt(0, 0))
    feats = [
        ("b", spatial.shape(pt(3, 0))),
        ("a", spatial.shape(pt(0, 3))),
        ("z", spatial.shape(pt(1, 0))),
    ]
    assert spatial.nearest([("e", a)], feats, 10) == [("e", "z", 1.0)]
    feats = [("b", spatial.shape(pt(3, 0))), ("a", spatial.shape(pt(0, 3)))]
    assert spatial.nearest([("e", a)], feats, 10) == [("e", "a", 3.0)]
    assert spatial.nearest([("e", a)], feats, 2) == []


def test_projected_round_trip_and_reproject():
    f = spatial.resolve_frame("EPSG:2236", ["EPSG:4326", "EPSG:2236", "local:feet"])
    assert f.working_crs == "EPSG:2236" and f.unit == "ftUS"
    assert f.usable == ["EPSG:4326", "EPSG:2236"]
    assert f.reprojected == ["EPSG:4326"]
    assert f.excluded["local:feet"].slug == "crs_mismatch"
    x, y = 600000.0, 500000.0
    fwd = f.transformer("EPSG:2236", "EPSG:4326")
    lon, lat = fwd.transform(x, y)
    g = f.transform_from("EPSG:4326")(spatial.shape(pt(lon, lat)))
    assert abs(g.x - x) < 1e-6 and abs(g.y - y) < 1e-6


def test_geographic_model_uses_utm():
    f = spatial.resolve_frame("EPSG:4326", ["EPSG:4326"], anchor_xy=(-81.0, 29.0))
    assert f.working_crs == "EPSG:32617" and f.unit == "m"
    assert f.reprojected == ["EPSG:4326"]
    with pytest.raises(ValueError):
        spatial.resolve_frame("EPSG:4326", [])
    a = spatial.element_geometry({"kind": "node", "id": "J1", "geometry": pt(-81.0, 29.0)}, f)
    b = spatial.source_geometry(pt(-81.0, 29.0009), "EPSG:4326", f)
    d = a.distance(b)
    assert 95 < d < 105  # about 100 m north


def test_envelope_for():
    f = spatial.resolve_frame("local:feet", ["local:feet"])
    assert spatial.envelope_for("local:feet", f, (0, 0, 1, 1)) == (0, 0, 1, 1)
    f = spatial.resolve_frame("EPSG:2236", ["EPSG:4326"])
    w = spatial.envelope_for("EPSG:4326", f, (600000, 500000, 600100, 500100))
    assert -90 < w[1] < w[3] < 90 and w[0] < w[2] < 0


def test_linear_unit_names():
    assert spatial.linear_unit("EPSG:32617") == "m"
    assert spatial.linear_unit("EPSG:2236") == "ftUS"
