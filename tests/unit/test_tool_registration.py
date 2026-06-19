"""Tool-surface drift guards.

Two cheap invariants that have each been violated once before:

1. **Forcing-mode codes.** The MCP string modes must map onto the engine's
   ``ForcingMode`` / ``SurfaceForcingMode`` C codes (``REPLACE/OVERRIDE = 1``,
   ``ADD = 2``; ``0`` is the engine-internal "no forcing" state). A previous
   mapping of ``{"replace": 0, "add": 1}`` silently disabled "replace" and
   turned "add" into a replace on the v1 engine.

2. **Tool registration.** Every tool namespace added to ``server.py`` stays
   mounted, and the tools added by the 2026-06-10 gap closure
   (``twod_*``, user-flag schema, typed file-path slots, climate-evap
   read-back) remain present.

The registration check imports the full server module graph, which imports
``openswmm.engine`` — it skips when the engine is not installed. The
mode-code check is pure Python and always runs.
"""

from __future__ import annotations

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


class TestToolRegistration:
    _EXPECTED_NAMESPACES = {
        "lifecycle", "query", "forcing", "analysis", "building", "editing",
        "hotstart", "spatial", "geopackage", "tables", "inflows", "controls",
        "infrastructure", "nodes", "links", "subcatchments", "pollutants",
        "model", "quality", "twod", "gym",
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
        "twod_force_evap",
        "twod_get_mesh_summary",
        "twod_get_mesh_geometry",
        "twod_set_vertex_z",
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
    }

    async def test_namespaces_and_new_tools_registered(self):
        pytest.importorskip("openswmm.engine")
        from openswmm_mcp.server import mcp

        tools = await mcp.list_tools()
        names = {t.name for t in tools}

        namespaces = {n.split("_")[0] for n in names}
        missing_ns = self._EXPECTED_NAMESPACES - namespaces
        assert not missing_ns, f"unmounted tool namespaces: {sorted(missing_ns)}"

        missing = self._EXPECTED_NEW_TOOLS - names
        assert not missing, f"missing tools: {sorted(missing)}"

        missing_gym = self._EXPECTED_GYM_TOOLS - names
        assert not missing_gym, f"missing gym tools: {sorted(missing_gym)}"
