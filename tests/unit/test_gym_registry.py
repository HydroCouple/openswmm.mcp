"""Unit tests for the gym kind registry (plan Phase 1).

Schema/metadata tests run everywhere (no gym extra needed); tests that
resolve or construct real gym classes skip cleanly when
``openswmm_gymnasium`` (and the compiled engine it requires) is absent.
"""

from __future__ import annotations

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.gym_support import registry

# ---------------------------------------------------------------------------
# Metadata (no gym extra required)
# ---------------------------------------------------------------------------

EXPECTED_KINDS = {
    "reward_term": {
        "flooding_volume",
        "cso_volume",
        "peak_outflow",
        "reliability_margin",
        "setpoint_smoothness",
        "uncontrolled_discharge",
        "storage_underutilization",
        "pump_energy",
    },
    "runtime_factory": {"orifice_setting", "node_lateral_inflow"},
    "design_factory": {
        "link_roughness",
        "link_length",
        "link_diameter",
        "node_max_depth",
        "subcatch_gw_outflow_coeff",
        "storage_volume",
        "lid_placement",
        "rdii_unit_hydrograph",
    },
    "policy_factory": {"control_curve"},
    "wrapper": {
        "record_trajectory",
        "rescale_box_actions",
        "linear_scalarize",
        "tchebycheff_scalarize",
        "mask_runtime",
        "mask_design",
    },
}


def test_list_kinds_covers_expected_vocabulary():
    for category, expected in EXPECTED_KINDS.items():
        names = {spec.kind for spec in registry.list_kinds(category)}
        assert names == expected, f"{category}: {names} != {expected}"


def test_list_kinds_all_matches_union_of_categories():
    all_kinds = {(s.category, s.kind) for s in registry.list_kinds()}
    expected = {(category, kind) for category, kinds in EXPECTED_KINDS.items() for kind in kinds}
    assert all_kinds == expected


def test_every_kind_has_description_and_import_path():
    for spec in registry.list_kinds():
        assert spec.description, spec.kind
        module, sep, cls = spec.import_path.partition(":")
        assert sep == ":" and module.startswith("openswmm_gymnasium") and cls, spec.kind


def test_every_params_model_emits_json_schema():
    # gym_list_capabilities derives its output from these schemas.
    for spec in registry.list_kinds():
        schema = spec.params_model.model_json_schema()
        assert schema.get("type") == "object", spec.kind


def test_get_kind_unknown_raises_actionable_tool_error():
    with pytest.raises(ToolError) as excinfo:
        registry.get_kind("reward_term", "not_a_term")
    msg = str(excinfo.value)
    assert "VALIDATION_ERROR" in msg
    assert "flooding_volume" in msg  # lists valid alternatives


def test_validate_params_accepts_valid_and_rejects_invalid():
    ok = registry.validate_params(
        "runtime_factory", "node_lateral_inflow", {"node_ids": ["J1"], "max_inflow": 2.5}
    )
    assert ok.model_dump()["max_inflow"] == 2.5

    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        registry.validate_params(
            "runtime_factory", "node_lateral_inflow", {"node_ids": ["J1"], "max_inflow": -1.0}
        )
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        registry.validate_params("reward_term", "cso_volume", {"node_ids": []})
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        # extra="forbid": typo'd param names fail fast
        registry.validate_params("reward_term", "flooding_volume", {"nod_ids": ["J1"]})


def test_construct_kind_refuses_wrappers():
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        registry.construct_kind("wrapper", "mask_design", {})


def test_construct_kind_refuses_policy_factory():
    # policy_factory kinds (like wrappers) are built by build_env, not here.
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        registry.construct_kind(
            "policy_factory",
            "control_curve",
            {"assets": [{"link_id": "Or1", "obs_node": "J1", "x_knots": [0.0, 1.0]}]},
        )


def test_control_curve_schema_exposes_curve_fields():
    spec = registry.get_kind("policy_factory", "control_curve")
    schema = spec.params_model.model_json_schema()
    top = set(schema["properties"])
    assert {"assets", "x_normalized", "control_interval_steps", "rate_limit"} <= top
    # The per-asset sub-schema lists the curve fields.
    asset_schema = schema["$defs"]["ControlCurveAssetParams"]["properties"]
    assert {"link_id", "obs_node", "x_knots", "y_low", "y_high", "monotonic"} <= set(asset_schema)


def test_control_curve_params_validation():
    base = {"assets": [{"link_id": "Or1", "obs_node": "J1", "x_knots": [0.0, 0.5, 1.0]}]}
    ok = registry.validate_params("policy_factory", "control_curve", base)
    assert ok.assets[0].link_id == "Or1"

    # x_knots must be strictly increasing.
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        registry.validate_params(
            "policy_factory",
            "control_curve",
            {"assets": [{"link_id": "Or1", "obs_node": "J1", "x_knots": [0.0, 0.0]}]},
        )
    # y_low > y_high.
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        registry.validate_params(
            "policy_factory",
            "control_curve",
            {
                "assets": [
                    {
                        "link_id": "Or1",
                        "obs_node": "J1",
                        "x_knots": [0.0, 1.0],
                        "y_low": 0.9,
                        "y_high": 0.1,
                    }
                ]
            },
        )
    # y_init length mismatch.
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        registry.validate_params(
            "policy_factory",
            "control_curve",
            {
                "assets": [
                    {
                        "link_id": "Or1",
                        "obs_node": "J1",
                        "x_knots": [0.0, 0.5, 1.0],
                        "y_init": [1.0, 1.0],
                    }
                ]
            },
        )
    # empty assets.
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        registry.validate_params("policy_factory", "control_curve", {"assets": []})


def test_storage_volume_params_validation():
    # scalar mode: scalar bounds, high > low.
    ok = registry.validate_params(
        "design_factory", "storage_volume", {"node_ids": ["T1"], "low": 0.5, "high": 3.0}
    )
    assert ok.model_dump()["mode"] == "scalar"
    # coeffs mode: length-3 (a, b, c) bounds.
    registry.validate_params(
        "design_factory",
        "storage_volume",
        {
            "node_ids": ["T1"],
            "low": [100.0, 0.0, 0.0],
            "high": [9000.0, 2.0, 500.0],
            "mode": "coeffs",
        },
    )
    for bad in (
        {"node_ids": ["T1"], "low": 1.0, "high": 1.0},  # high <= low
        {"node_ids": ["T1"], "low": [1, 2], "high": [3, 4], "mode": "coeffs"},  # len != 3
        {"node_ids": ["T1"], "low": [1, 0, 0], "high": 9.0, "mode": "coeffs"},  # mixed shapes
        {"node_ids": [], "low": 0.5, "high": 3.0},  # empty ids
    ):
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            registry.validate_params("design_factory", "storage_volume", bad)


def test_lid_placement_params_validation():
    ok = registry.validate_params(
        "design_factory",
        "lid_placement",
        {
            "subcatch_ids": ["S1"],
            "lid_controls": ["BIO", "PAVE"],
            "area_low": 100.0,
            "area_high": 2000.0,
        },
    )
    assert ok.model_dump()["number"] == 1
    for bad in (
        {"subcatch_ids": ["S1"], "lid_controls": [], "area_low": 100.0, "area_high": 2000.0},
        {"subcatch_ids": ["S1"], "lid_controls": ["BIO"], "area_low": 200.0, "area_high": 100.0},
        {"subcatch_ids": [], "lid_controls": ["BIO"], "area_low": 100.0, "area_high": 2000.0},
    ):
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            registry.validate_params("design_factory", "lid_placement", bad)


def test_rdii_unit_hydrograph_params_validation():
    # R-only is the default and needs no IA bounds.
    ok = registry.validate_params(
        "design_factory",
        "rdii_unit_hydrograph",
        {"targets": [["SanSewer", -1, 0]], "r_low": 0.0, "r_high": 0.5},
    )
    assert ok.model_dump()["include_ia"] is False
    # Opt into IA with non-degenerate bounds.
    registry.validate_params(
        "design_factory",
        "rdii_unit_hydrograph",
        {
            "targets": [["SanSewer", -1, 0]],
            "r_low": 0.0,
            "r_high": 0.5,
            "include_ia": True,
            "ia_low": [0.0, 0.0, 0.0],
            "ia_high": [0.5, 2.0, 0.2],
        },
    )
    for bad in (
        {"targets": [["SanSewer", -1, 0]], "r_low": 0.5, "r_high": 0.5},  # r high <= low
        {"targets": [], "r_low": 0.0, "r_high": 0.5},  # empty targets
        {  # include_ia with degenerate IA bounds
            "targets": [["SanSewer", -1, 0]],
            "r_low": 0.0,
            "r_high": 0.5,
            "include_ia": True,
        },
    ):
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            registry.validate_params("design_factory", "rdii_unit_hydrograph", bad)


# ---------------------------------------------------------------------------
# Resolution / construction (requires the gym extra + engine)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_resolve_and_construct_every_kind():
    pytest.importorskip("openswmm_gymnasium")

    for spec in registry.list_kinds():
        cls = registry.resolve_kind(spec.category, spec.kind)
        assert isinstance(cls, type), spec.kind

    term = registry.construct_kind("reward_term", "flooding_volume", {"node_ids": ["J1"]})
    assert type(term).__name__ == "FloodingVolume"
    factory = registry.construct_kind(
        "design_factory",
        "link_diameter",
        {"link_ids": ["C1"], "low": 0.5, "high": 2.0},
    )
    assert type(factory).__name__ == "LinkDiameter"
