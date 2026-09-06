"""Tool-surface drift guards.

Three cheap invariants that have each been violated once before:

1. **Forcing-mode codes.** The MCP string modes must map onto the engine's
   ``ForcingMode`` / ``SurfaceForcingMode`` C codes (``REPLACE/OVERRIDE = 1``,
   ``ADD = 2``; ``0`` is the engine-internal "no forcing" state). A previous
   mapping of ``{"replace": 0, "add": 1}`` silently disabled "replace" and
   turned "add" into a replace on the v1 engine.

2. **Tool registration.** Every tool namespace added to ``server.py`` stays
   mounted, and the tools added by the 2026-06-10 gap closure
   (``twod_*``, user-flag schema, typed file-path slots, climate-evap
   read-back) remain present.

3. **``wraps:`` provenance markers.** Aggregating tools pin the C symbols they
   dispatch to with a ``# wraps: swmm_x swmm_y`` line, which the engine's
   ``plans/parity/build_matrix_provenance.py`` reads to clear false
   ``mcp-review`` rows. The builder's regex only sees symbols on the *same*
   line as the ``wraps:`` token, so a marker wrapped across lines by a
   formatter is silently ignored — the failure mode this guards.

The registration check imports the full server module graph, which imports
``openswmm.engine`` — it skips when the engine is not installed. The
mode-code and marker checks are pure Python and always run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


class TestForcingModeCodes:
    def test_1d_modes_mirror_engine_codes(self):
        from openswmm_mcp.tools.forcing import _resolve_forcing_mode

        assert _resolve_forcing_mode("replace") == 1
        assert _resolve_forcing_mode("add") == 2
        assert _resolve_forcing_mode(" Replace ") == 1

    def test_2d_modes_mirror_engine_codes(self):
        from openswmm_mcp.tools.twod import _FORCING_MODES

        assert _FORCING_MODES == {"replace": 1, "add": 2}

    def test_engine_enums_agree(self):
        eng = pytest.importorskip("openswmm.engine")
        from openswmm_mcp.tools.forcing import _resolve_forcing_mode
        from openswmm_mcp.tools.twod import _FORCING_MODES

        assert _resolve_forcing_mode("replace") == int(eng.ForcingMode.REPLACE)
        assert _resolve_forcing_mode("add") == int(eng.ForcingMode.ADD)
        assert _FORCING_MODES["replace"] == int(eng.SurfaceForcingMode.OVERRIDE)
        assert _FORCING_MODES["add"] == int(eng.SurfaceForcingMode.ADD)


class TestXsectShapeCodes:
    """Shape names must map onto the engine's post-6.0 ``XSectShape`` codes.

    The maps that previously lived in ``tools/editing.py`` and
    ``tools/building.py`` carried the legacy SWMM 5 ``XsectType`` ordering,
    so every shape except ``circular`` resolved to a different geometry than
    the caller asked for -- ``trapezoidal`` was stored as ``RECT_OPEN``,
    ``irregular`` as ``RECT_ROUND``. Both now share
    ``_util.xsect_shapes``, which derives its map from the enum.
    """

    def test_map_is_derived_from_the_engine_enum(self):
        eng = pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util.xsect_shapes import LEGACY_ALIASES, shape_codes

        codes = shape_codes()
        for shape in eng.XSectShape:
            assert codes[shape.name.lower()] == int(shape)
        for alias, target in LEGACY_ALIASES.items():
            assert codes[alias] == codes[target]

    def test_the_renumbered_shapes_resolve_correctly(self):
        eng = pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util.xsect_shapes import resolve_shape

        # The four the legacy map got most visibly wrong.
        assert resolve_shape("trapezoidal") == int(eng.XSectShape.TRAPEZOIDAL)
        assert resolve_shape("irregular") == int(eng.XSectShape.IRREGULAR)
        assert resolve_shape("force_main") == int(eng.XSectShape.FORCE_MAIN)
        assert resolve_shape("filled_circular") == int(eng.XSectShape.FILLED_CIRCULAR)

    def test_editing_and_building_share_the_map(self):
        pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util.xsect_shapes import resolve_shape
        from openswmm_mcp.tools.building import _resolve_xsect_shape

        assert _resolve_xsect_shape("trapezoidal") == resolve_shape("trapezoidal")

    def test_unknown_shape_raises(self):
        pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util.xsect_shapes import resolve_shape
        from openswmm_mcp.errors import ToolError

        with pytest.raises(ToolError):
            resolve_shape("not_a_shape")


class TestTransportEnumCodes:
    """Transport-configuration tokens must map onto the engine's own enums.

    Mirrors :class:`TestXsectShapeCodes`. ``_util.transport_enums`` derives
    every map from the engine ``IntEnum`` rather than transcribing it, for the
    same reason: a hand-copied enum is a second source of truth, and its drift
    shows up as a value silently written to the wrong parameter -- a wrong
    latitude, a wrong emissivity -- rather than as an error.
    """

    def test_maps_are_derived_from_the_engine_enums(self):
        eng = pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util import transport_enums as te

        cases = [
            (eng.HeatFluxModule, te.heat_flux_module_codes),
            (eng.HeatShortwaveMode, te.heat_shortwave_mode_codes),
            (eng.HeatRadiativeParam, te.heat_radiative_param_codes),
            (eng.HeatSolarParam, te.heat_solar_param_codes),
            (eng.HeatCloudParam, te.heat_cloud_param_codes),
            (eng.HeatSourceKind, te.heat_source_kind_codes),
            (eng.WaterAgeSource, te.water_age_source_codes),
            (eng.ReactionScope, te.reaction_scope_codes),
            (eng.ReactionExprForm, te.reaction_expr_form_codes),
        ]
        for enum, accessor in cases:
            codes = accessor()
            assert len(codes) == len(list(enum)), (
                f"{enum.__name__}: map has {len(codes)} entries, enum has {len(list(enum))}"
            )
            for member in enum:
                assert codes[member.name.lower()] == int(member), (
                    f"{enum.__name__}.{member.name} maps to "
                    f"{codes.get(member.name.lower())}, expected {int(member)}"
                )

    def test_resolvers_agree_with_the_enums(self):
        eng = pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util import transport_enums as te

        # The three an agent is most likely to pass by name.
        assert te.resolve_heat_shortwave_mode("computed") == int(eng.HeatShortwaveMode.COMPUTED)
        assert te.resolve_heat_source_kind("external_inflow") == int(
            eng.HeatSourceKind.EXTERNAL_INFLOW
        )
        assert te.resolve_water_age_source("dwf") == int(eng.WaterAgeSource.DWF)
        assert te.resolve_reaction_scope("tank") == int(eng.ReactionScope.TANK)

    def test_coerce_accepts_names_and_codes(self):
        pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util.transport_enums import coerce_enum, heat_cloud_param_codes

        codes = heat_cloud_param_codes()
        assert coerce_enum("FRACTION", codes, "cloud param") == codes["fraction"]
        assert coerce_enum(codes["fraction"], codes, "cloud param") == codes["fraction"]

    def test_unknown_token_raises(self):
        pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util import transport_enums as te
        from openswmm_mcp.errors import ToolError

        resolvers = [
            te.resolve_heat_flux_module,
            te.resolve_heat_shortwave_mode,
            te.resolve_heat_radiative_param,
            te.resolve_heat_solar_param,
            te.resolve_heat_cloud_param,
            te.resolve_heat_source_kind,
            te.resolve_water_age_source,
            te.resolve_reaction_scope,
            te.resolve_reaction_expr_form,
        ]
        for resolve in resolvers:
            with pytest.raises(ToolError):
                resolve("not_a_valid_token")

    def test_out_of_range_code_raises(self):
        pytest.importorskip("openswmm.engine")
        from openswmm_mcp._util.transport_enums import coerce_enum, reaction_scope_codes
        from openswmm_mcp.errors import ToolError

        with pytest.raises(ToolError):
            coerce_enum(999, reaction_scope_codes(), "reaction scope")


class TestWrapsMarkers:
    """``# wraps:`` markers must stay machine-readable by the parity builder."""

    # Verbatim from plans/parity/tools/build_matrix_provenance.py.
    _WRAPS_RE = re.compile(r"wraps:\s*((?:swmm_[a-z0-9_]+\s*,?\s*)+)", re.IGNORECASE)
    _SRC = Path(__file__).resolve().parents[2] / "src" / "openswmm_mcp"

    def _marker_lines(self) -> list[tuple[Path, int, str]]:
        out: list[tuple[Path, int, str]] = []
        for sub in ("tools", "resources", "prompts"):
            d = self._SRC / sub
            if not d.is_dir():
                continue
            for path in sorted(d.glob("*.py")):
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if "wraps:" in line:
                        out.append((path, n, line))
        return out

    def test_markers_exist(self):
        assert self._marker_lines(), "no `wraps:` markers found — did the tools tree move?"

    def test_every_marker_is_parsed_by_the_builder(self):
        for path, lineno, line in self._marker_lines():
            match = self._WRAPS_RE.search(line)
            assert match, f"{path.name}:{lineno} `wraps:` marker names no swmm_* symbol: {line!r}"
            # The regex stops at the first non-symbol token, so a symbol listed
            # after a comma-and-newline (or any prose) would be dropped.
            named = set(re.findall(r"swmm_[a-z0-9_]+", match.group(1)))
            on_line = set(re.findall(r"swmm_[a-z0-9_]+", line))
            assert named == on_line, (
                f"{path.name}:{lineno} the builder would only see {sorted(named)} "
                f"but the line names {sorted(on_line)} — keep every symbol on one line"
            )


class TestToolRegistration:
    _EXPECTED_NAMESPACES = {
        "lifecycle",
        "query",
        "forcing",
        "analysis",
        "building",
        "editing",
        "hotstart",
        "spatial",
        "geopackage",
        "tables",
        "inflows",
        "controls",
        "infrastructure",
        "nodes",
        "links",
        "subcatchments",
        "pollutants",
        "model",
        "quality",
        "twod",
        "gym",
        "xsect",
        # Transport-configuration surface (heat / reactions / water age /
        # initial quality / process components).
        "heat",
        "reactions",
        "water_age",
        "initial_quality",
        "process_components",
    }

    # Gym tool domain (GYMNASIUM_INTEGRATION_PLAN.md Phase 2).
    _EXPECTED_GYM_TOOLS = {
        "gym_list_capabilities",
        "gym_describe_benchmark",
        "gym_create_env_config",
        "gym_get_env_config",
        "gym_list_env_configs",
        "gym_delete_env_config",
        "gym_validate_env_config",
        # Phase 3: episodes + interactive control.
        "gym_run_episode",
        "gym_env_open",
        "gym_env_reset",
        "gym_env_step",
        "gym_env_close",
        "gym_list_envs",
        # Phase 4: background optimization jobs.
        "gym_start_optimization",
        "gym_get_job",
        "gym_list_jobs",
        "gym_cancel_job",
        "gym_get_job_results",
        "gym_decode_policy",
        # Phase 5: scoring + model bridge.
        "gym_pareto_filter",
        "gym_score_front",
        "gym_compare_runs",
        "gym_apply_design",
    }

    # Tools added by the 2026-06-10 gap closure; removing any of these is
    # a regression, not a refactor.
    _EXPECTED_NEW_TOOLS = {
        "model_userflag_define",
        "model_userflag_undefine",
        "model_userflag_list_defs",
        "model_userflag_get_value",
        "model_userflag_set_value",
        "model_userflag_clear_value",
        "model_file_path_get",
        "model_file_path_set",
        "forcing_get_climate_evap_rate",
        "forcing_set_climate_forcing",
        "forcing_set_climate_dry_only",
        "forcing_get_climate_state",
        "subcatchments_set_gw_state",
        "subcatchments_set_snow_state",
        "subcatchments_get_aquifer",
        "subcatchments_set_aquifer",
        "subcatchments_get_gw_node",
        "subcatchments_set_gw_node",
        "subcatchments_get_gw_params",
        "subcatchments_set_gw_params",
        "subcatchments_set_infil_model",
        "infrastructure_lid_usage_count",
        "infrastructure_lid_usage_get",
        "infrastructure_lid_usage_remove",
        # Inlet-junction round (engine 544c79f8 / d38152ea): full [INLETS]
        # record, the inlet placement table, the node flag, and the
        # promote / split / fuse edits.
        "infrastructure_get_inlet_design",
        "infrastructure_set_inlet_design",
        "infrastructure_inlet_usage_count",
        "infrastructure_inlet_usage_list",
        "infrastructure_inlet_usage_get",
        "infrastructure_inlet_usage_find",
        "infrastructure_inlet_usage_set",
        "infrastructure_inlet_usage_remove",
        "nodes_is_inlet",
        "nodes_inlet_eligible",
        "editing_set_node_inlet",
        "editing_split_conduit_inlet",
        "editing_fuse_inlet_junction",
        "twod_set_triangle_mannings",
        "twod_set_triangle_tag",
        "twod_get_triangle_tag",
        "twod_set_vertex_tag",
        "twod_get_vertex_tag",
        "twod_set_vertex_coupled_node",
        "twod_force_evap",
        "twod_get_mesh_summary",
        "twod_get_mesh_geometry",
        "twod_set_vertex_z",
        "twod_set_vertex_z_bulk",
        "twod_get_coupling_map",
        "twod_get_state",
        "twod_get_state_bulk",
        "twod_get_totals",
        "twod_get_stats",
        "twod_get_mass_balance",
        "twod_force_rainfall",
        "twod_force_coupling_flux",
        "twod_force_clear",
        "twod_get_solver_params",
        "twod_set_solver_params",
        "twod_get_edge_bc",
        "twod_set_edge_bc",
        "twod_get_edge_conveyance",
        "twod_set_edge_conveyance",
        "twod_reset_edge_conveyance",
        # New-engine surface (lenient open, controls references / removal).
        "lifecycle_get_open_diagnostics",
        "controls_remove_rule",
        "controls_find_references",
        # ------------------------------------------------------------------
        # Transport-configuration surface: the five namespaces added for the
        # heat / reactions / water-age / initial-quality / process-component
        # bindings, plus the residual gaps closed in existing namespaces.
        # ------------------------------------------------------------------
        "heat_clear_cloud",
        "heat_clear_source_temp",
        "heat_get_cloud",
        "heat_get_config",
        "heat_get_effective_source_temp",
        "heat_get_radiative",
        "heat_get_solar",
        "heat_list_node_overrides",
        "heat_list_sources",
        "heat_remove_node_override",
        "heat_set_cloud",
        "heat_set_cloud_timeseries",
        "heat_set_module",
        "heat_set_node_override",
        "heat_set_radiative",
        "heat_set_shortwave_mode",
        "heat_set_solar",
        "heat_set_source_temp",
        "initial_quality_list_entries",
        "initial_quality_remove_entry",
        "initial_quality_set_entry",
        "process_components_find_component",
        "process_components_list_components",
        "process_components_register_component",
        "process_components_remove_component",
        "reactions_add_coefficient",
        "reactions_add_species",
        "reactions_add_term",
        "reactions_apply_text",
        "reactions_check_text",
        "reactions_functions",
        "reactions_get_option",
        "reactions_get_species_expression",
        "reactions_hydraulic_variables",
        "reactions_list_coefficients",
        "reactions_list_initial_quality",
        "reactions_list_species",
        "reactions_list_terms",
        "reactions_remove_coefficient",
        "reactions_remove_initial_element",
        "reactions_remove_species",
        "reactions_remove_term",
        "reactions_save",
        "reactions_serialize",
        "reactions_set_coefficient_value",
        "reactions_set_initial_element",
        "reactions_set_initial_global",
        "reactions_set_option",
        "reactions_set_species_expression",
        "reactions_set_term_expression",
        "reactions_validate_expression",
        "water_age_get_config",
        "water_age_list_node_overrides",
        "water_age_remove_node_override",
        "water_age_save_sources",
        "water_age_set_global_source",
        "water_age_set_node_override",
        # Residual gaps closed in pre-existing namespaces.
        "editing_set_gage_file_format",
        "editing_get_gage_rainfall_series",
        "editing_rename_aquifer",
        "editing_rename_snowpack",
        "nodes_get_rim_depth",
        "nodes_set_rim_depth",
        "nodes_get_inflow",
        "links_get_slot_volume",
        "links_stat_slot_share",
        "links_stat_peak_slot_share",
        "lifecycle_save_runoff_step",
        "lifecycle_read_runoff_step",
        "lifecycle_close_runoff_interface",
        "lifecycle_write_report",
        "lifecycle_run_model_file",
    }

    async def test_namespaces_and_new_tools_registered(self):
        pytest.importorskip("openswmm.engine")
        from openswmm_mcp.server import mcp

        tools = await mcp.list_tools()
        names = {t.name for t in tools}

        # Check each expected namespace by prefix rather than deriving the
        # namespace set as ``{n.split("_")[0] for n in names}``. That heuristic
        # assumed every namespace is a single underscore-free token, which
        # stopped being true with ``water_age`` / ``initial_quality`` /
        # ``process_components``: ``water_age_get_config`` split to ``water``,
        # so a correctly mounted namespace read as missing. The namespaces are
        # named by the ``mcp.mount(..., namespace=...)`` calls in server.py,
        # and this set mirrors them, so prefix-matching tests the real thing.
        missing_ns = {
            ns
            for ns in self._EXPECTED_NAMESPACES
            if not any(n.startswith(f"{ns}_") for n in names)
        }
        assert not missing_ns, f"unmounted tool namespaces: {sorted(missing_ns)}"

        missing = self._EXPECTED_NEW_TOOLS - names
        assert not missing, f"missing tools: {sorted(missing)}"

        missing_gym = self._EXPECTED_GYM_TOOLS - names
        assert not missing_gym, f"missing gym tools: {sorted(missing_gym)}"
