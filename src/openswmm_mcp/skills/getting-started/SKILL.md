---
name: getting-started
description: "A start-to-finish tour of every tool namespace this server exposes -- lifecycle, inspection, structural editing, domain configuration (controls/forcing/infrastructure/quality), the 2D surface, running, and analysis. Use this when the user is new to this server, asks 'what can you do', 'show me everything', 'walk me through this model', wants a broad multi-namespace tour rather than one narrow task, or when no other bundled skill's description matches but the request clearly spans more than one tool call. For a specific expert workflow instead of a tour, prefer calibrate-model, capacity-assessment, design-storm-check, model-qa-check, operational-optimization, or overland-flood-mapping -- check swmm://skills for their exact triggers before defaulting to this one."
---

# A tour of the openswmm.mcp tool surface

This server wraps the OpenSWMM 6 engine as roughly 500 tools. They're organized into
namespaces by the prefix before the underscore (`lifecycle_*`, `nodes_*`, `twod_*`, ...). This
skill walks the natural order a session actually goes through, start to finish, so you can
orient yourself in an unfamiliar model or a fresh conversation without guessing which namespace
has what you need.

Every session-scoped tool takes `session_id` (default `"default"`). Every numeric value in your
answer must come from an actual tool call -- never state a depth, flow, ID, or count you
haven't just read back.

## 1. Open and diagnose

- `lifecycle_open_model` -- parse a `.inp` and start a session. Pass `lenient_open=true` if you
  want to inspect a possibly-broken file instead of failing outright.
- `lifecycle_get_open_diagnostics` -- after a lenient open, the parser's own recorded errors and
  warnings. Read these before trusting anything else about the model.
- `lifecycle_get_simulation_state` / `lifecycle_get_simulation_time` -- where the session
  actually is (`opened` / `initialized` / `running` / `ended`). Several tool families require a
  specific state and will tell you which if you're in the wrong one.
- `lifecycle_close_model` -- release a session. Do this before re-opening the same `.inp` if you
  need a clean run (re-running an already-`ended` session reuses stale results).

## 2. Inspect before you touch anything

- `query_get_system_summary` -- counts, units, routing model, current time. Your first call on
  any unfamiliar model.
- `query_get_node_info` / `query_get_link_info` / `query_get_subcatchment_info` /
  `query_get_gage_info` / `query_get_pollutant_info` -- properties and current state, one
  element or the whole model at once (paginated via `start_index`/`limit` on the bulk form).
- `query_find_elements` -- regex search by ID and/or type across the whole model.
- `nodes_get_ids_bulk` / `links_get_ids_bulk` / `subcatchments_get_ids_bulk` -- fast ID-only
  listings when you don't need full property dumps.

## 3. Build or edit the model definition

Only reach for this section when the user actually wants a structural change -- confirm in
plain language first, especially before adding or deleting nodes/links/subcatchments, since
those need map coordinates you should ask for rather than invent.

- `building_create_model` -- start a model from nothing.
- `building_add_node` / `building_add_link` / `building_add_subcatchment` / `building_add_gage`
  / `building_add_curve` / `building_add_timeseries` / `building_pop_last_node` /
  `building_pop_last_link` -- add or undo-the-last-add of core objects.
- `editing_set_node_properties` / `editing_set_link_properties` /
  `editing_set_subcatchment_properties` / `editing_convert_node` / `editing_convert_link` /
  `editing_rename_*` / `editing_delete_object` -- modify or remove what's already there.
- `editing_analyze_impact` -- **preview** what a proposed change would affect before you commit
  it. Worth using on anything non-trivial; it's cheaper than editing first and discovering the
  blast radius after.
- `building_validate_model` -- run the engine's own validator. A model with no messages back is
  valid; not the same as "modeling-judgment-sound" (see the `model-qa-check` skill for that).
- `building_write_model` -- save. Write structural-edit sessions to a **new** path rather than
  overwriting the original, unless the user explicitly asks to replace it.

## 4. Configure domain-specific behavior

- **Controls (RTC rules):** `controls_add_rule` / `controls_list_rules` / `controls_get_rule` /
  `controls_validate_rule` / `controls_remove_rule` / `controls_set_link_setting` /
  `controls_set_link_status`.
- **Forcing (live, mid-run overrides):** `forcing_set_forcing` / `forcing_set_climate_forcing` /
  `forcing_set_rainfall_override` / `forcing_add_control_rule` / `forcing_clear_forcing` --
  scenario-test a "what if it rained more here" without editing the model definition itself.
- **Infrastructure:** `infrastructure_add_lid` / `_add_street` / `_add_inlet` /
  `_add_transect` and their `_get_*`/`_set_*` counterparts -- LID controls, streets/inlets for
  dual-drainage, irregular cross-section transects.
- **Subcatchments (hydrology detail):** `subcatchments_set_infil_horton` / `_green_ampt` /
  `_curve_number`, `subcatchments_set_gw_params`, `subcatchments_snowpack_*`.
- **Quality:** `pollutants_add` / `_set_*_conc`, `quality_buildup_set` / `quality_washoff_set`,
  `quality_treatment_get`/`validate_expression`.
- **Tables:** `tables_add_curve` / `tables_add_timeseries` / `tables_pattern_add` -- curves,
  time series, and monthly/daily/hourly patterns all live under this one namespace.

## 5. The 2D surface (if present)

Check first -- most models don't have one. `twod_get_mesh_summary` reports `active: false` on
any model without native `[2D_*]` `.inp` sections (including files exported from other
2D-capable tools that store their mesh elsewhere -- that's expected, not an error).

- `twod_get_mesh_summary` / `twod_get_mesh_geometry` -- size and per-triangle geometry.
- `twod_get_state` / `twod_get_state_bulk` -- the mesh's state **right now**, one instant.
- `twod_get_stats` -- **cumulative** max depth/velocity per triangle over the whole run so far
  -- the one you want for hazard/worst-case questions, not the instantaneous one.
- `twod_force_rainfall` / `_force_evap` / `_force_coupling_flux` -- live overrides, same idea as
  1D `forcing_*`.

## 6. Run it

- `lifecycle_run_simulation` -- run to completion.
- `lifecycle_run_for_steps` / `lifecycle_step_simulation` -- partial or single-step advance,
  useful for inspecting intermediate state or a quick smoke check without waiting for a full run.

## 7. Analyze results

- `analysis_get_report_snapshot` -- the .rpt-equivalent summary: continuity, storage, link flow,
  pumps.
- `analysis_get_flooding_summary` / `analysis_get_capacity_summary` -- per-node flood volume;
  per-conduit hmax/Hmax (filling ratio), max flow, surcharge time.
- `analysis_get_mass_balance` -- check this before trusting any of the above; a large continuity
  error means the results themselves are suspect.
- `analysis_get_time_series` / `analysis_output_*` -- pull a specific series, or read the raw
  binary `.out` file directly (node/link/subcatchment attributes, period counts, system totals).
- `analysis_export_results` -- write results out for the user rather than just describing them.

## 8. Advanced: persistence and comparison

- `hotstart_save_hotstart` / `hotstart_load_hotstart` / `hotstart_clone_session` -- checkpoint a
  run and branch cheaply from it instead of re-simulating from time zero.
- `geopackage_open_geopackage` / `_import_observed_data` / `_compare_sim_vs_observed` -- bring
  in observed data and score simulated-vs-observed fit.

## When a bundled expert Skill fits better

Six other skills on this server encode a specific, disciplined multi-step workflow rather than
a general tour: `calibrate-model`, `capacity-assessment`, `design-storm-check`,
`model-qa-check`, `operational-optimization`, `overland-flood-mapping`. Read `swmm://skills` for
their exact trigger conditions and load one with `use_skill` when the user's request matches --
they'll get you a better result than improvising the same workflow from this tour's raw tools.
