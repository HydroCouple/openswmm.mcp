"""The provenance ledger's shared rules: canonical times and CRS compatibility (no engine)."""

from __future__ import annotations

import pytest

from openswmm_mcp.ledger.common import (
    CrsRefusal,
    canonical_time,
    check_model_crs,
    crs_label,
    crs_verdict,
    next_after,
    resolve_model_crs,
)


def test_canonical_time_is_fixed_width_utc():
    assert canonical_time("2024-06-03") == "2024-06-03T00:00:00.000000Z"
    assert canonical_time("2024-06-03T14:15:00+02:00") == "2024-06-03T12:15:00.000000Z"
    assert canonical_time("2024-06-03T14:15:00Z") == "2024-06-03T14:15:00.000000Z"
    assert canonical_time(None) is None
    assert next_after("2024-06-03T00:00:00.999999Z") == "2024-06-03T00:00:01.000000Z"
    with pytest.raises(ValueError):
        canonical_time("June 3rd")


def test_crs_labels():
    assert crs_label("urn:ogc:def:crs:EPSG::2236") == "EPSG:2236"
    assert crs_label("epsg:4326") == "EPSG:4326"
    assert crs_label("", "Feet") == "local:feet"
    assert crs_label(None) == "unknown"
    with pytest.raises(ValueError):
        crs_label("local:furlongs")


@pytest.mark.parametrize(
    ("model", "source", "slug"),
    [
        ("local:feet", "local:feet", None),
        ("local:feet", "none", None),
        ("local:feet", "local:meters", "crs_mismatch"),
        ("local:feet", "EPSG:2236", "crs_mismatch"),
        ("local:feet", "unknown", "crs_unknown"),
        ("EPSG:2236", "EPSG:4326", None),
        ("EPSG:2236", "local:feet", "crs_mismatch"),
        ("EPSG:2236", "unknown", "crs_unknown"),
    ],
)
def test_crs_verdict_table(model, source, slug):
    verdict = crs_verdict(model, source)
    assert (verdict.slug if verdict else None) == slug


def test_model_crs_resolution_order_and_refusals():
    assert resolve_model_crs(None, None, "", "feet") == ("local:feet", "map_units", [])
    assert resolve_model_crs(None, None, "EPSG:2236", "feet")[:2] == ("EPSG:2236", "engine")
    label, source, warnings = resolve_model_crs(None, "EPSG:2236", "EPSG:3735", "feet")
    assert (label, source) == ("EPSG:2236", "registered") and warnings
    with pytest.raises(CrsRefusal) as conflict:
        resolve_model_crs("EPSG:1", "EPSG:2", None, None)
    assert conflict.value.slug == "crs_conflict"
    with pytest.raises(CrsRefusal) as degrees:
        check_model_crs("local:degrees")
    assert degrees.value.slug == "crs_required"
    check_model_crs("local:feet")
