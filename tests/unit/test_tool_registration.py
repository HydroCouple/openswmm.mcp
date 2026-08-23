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
