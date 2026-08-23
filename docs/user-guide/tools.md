# Tools

The OpenSWMM MCP server exposes **473 tools** across **22 domain
namespaces**.  Every tool is a FastMCP `@tool` decorated function in
`src/openswmm_mcp/tools/<namespace>.py` that returns either a plain
value or a Pydantic model from `openswmm_mcp.models`.

Tools are namespaced: a function `open_model` in `tools/lifecycle.py`
is mounted as the MCP tool `lifecycle_open_model`.  Every tool in the
tables below follows that `<namespace>_<function>` convention.

For full parameter signatures, return types, and docstrings see the
auto-generated {doc}`/api/index` — every namespace has its own
section there.

## At a glance

| Namespace | Tools | Purpose |
|-----------|------:|---------|
| `lifecycle_*` | 23 | Open / step / run / close models (incl. lenient open + open-diagnostics read-back), list sessions, manage event windows, steady-state skip, runoff-interface files. |
| `query_*` | 7 | Read-only "what does this model contain?" queries (nodes, links, subcatchments, gages, pollutants, system summary, free-form search). |
| `model_*` | 38 | Project-level metadata: title, options + extension options, CRS, unit system, scalar user flags + user-flag schema / per-object values, plugins, file-section paths + typed external-file path slots, aquifer / snowpack listings, pattern factors, report start. |
| `building_*` | 13 | Programmatic model construction: nodes, links, subcatchments, gages, options, time series, curves, pollutants, validation + write. |
| `editing_*` | 14 | In-place edits on a parsed model: cascade-delete + impact analysis (nodes, links, subcatchments, gages, tables, transects, pollutants, patterns, aquifers, snowpacks, LID controls, streets, inlets, land uses, hydrographs), type conversion, property setters, renames, gage scale factor. |
| `nodes_*` | 40 | Per-node accessors: bulk arrays, storage curves, outfall configuration, dividers, exfiltration, quality, tags, head boundary. |
| `links_*` | 56 | Per-link accessors: bulk arrays, control settings, pump / weir / orifice / outlet / culvert parameters, cross-section, tags, statistics. |
| `subcatchments_*` | 38 | Per-subcatchment accessors: runoff state, infiltration (incl. model switch), coverage, ponded quality, statistics, tags, outlet routing, aquifer / groundwater assignment + `[GROUNDWATER]` params. |
| `inflows_*` | 35 | External / DWF / RDII inflows, unit hydrographs, exponential IA decay — full read / edit / remove lifecycle. |
| `controls_*` | 11 | SWMM control rules: add, clear, validate, remove one rule, find rules referencing an object, set link setting / status. |
| `forcing_*` | 11 | Per-step overrides (lateral inflow, rainfall, PET, link control, link quality) and climate-evaporation read-back. |
| `pollutants_*` | 23 | Pollutant identity + properties (decay, rain/GW/RDII/DWF concentrations, co-pollutant, snow-only, runtime injection). |
| `quality_*` | 14 | Buildup / washoff / treatment kinetics; landuse and street-sweeping setup. |
| `tables_*` | 16 | Time series, curves, patterns; type query, lookup helpers, pattern removal. |
| `infrastructure_*` | 34 | Transects (full profile / bank / encroachment / modifier editing), streets, inlets, LID controls and LID-usage (add / count / get / remove). |
| `hotstart_*` | 10 | Hot-start save / load, state seeding, session cloning, scheduled-save registry. |
| `analysis_*` | 22 | Post-run analytics: statistics, mass balance, flooding / capacity summaries, quality losses, scenario compare, results export, full output-reader breadth (snapshot + series + attribute). |
| `spatial_*` | 17 | Coordinates, polylines, polygons, CRS, gage coords, bulk node coords, project-wide geometry export, LID placement. |
| `geopackage_*` | 14 | GeoPackage I/O: plugin registration, open, list simulations, read result series / summaries, raw SQL scalars, topology, compare simulated vs observed, observed-value writes. |
| `twod_*` | 28 | 2D overland-flow surface: mesh queries + edits (vertex Z, triangle Manning's n, triangle/vertex tags, vertex→node coupling), per-triangle / per-vertex state (incl. vertex render depths), edge geometry, statistics + mass balance, runtime forcing, solver parameters, edge boundary conditions, edge conveyance. |
| `datetime_*` | 6 | Pure SWMM-DateTime conversion utilities (encode / decode / arithmetic) — no open session required. |
| `gym_*` | 23 | RL / optimization orchestration via `openswmm.gymnasium`: declarative env configs, episode rollouts, interactive LLM-as-controller stepping, background design- and policy-search jobs (random / grid / Platypus MOEAs), reactive `control_curve` policy tuning + decode, Pareto scoring, apply-design model bridge. See {doc}`/user-guide/optimization`. |

----

## Calling tools

Every tool is invoked through MCP's standard `tools/call` request.  In
Claude Code, Claude Desktop, or any MCP client this happens implicitly
when the LLM decides to call a tool; if you are driving the server
programmatically the JSON-RPC envelope looks like:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "lifecycle_open_model",
    "arguments": {
      "inp_path": "/abs/path/to/model.inp",
      "session_id": "default"
    }
  }
}
```

All session-bound tools accept a `session_id` argument (default
`"default"`) so multiple independent simulations can coexist in the
same server process — see {doc}`/developer/architecture` for the
session model.

----

## Lifecycle (`lifecycle_*`)

Opens a model and drives its state-machine: `OPENED → INITIALIZED →
RUNNING → ENDED → CLOSED`.  Also manages event windows and the
steady-state skip optimisation.

Tools: `open_model`, `get_open_diagnostics`, `run_simulation`,
`step_simulation`, `get_simulation_time`, `get_simulation_state`,
`close_model`, `list_sessions`, `events_count`, `events_get`,
`events_add`, `events_set`, `events_remove`, `events_clear`,
`is_between_events`, `get_steady_state_skip`, `set_steady_state_skip`.

`open_model` accepts `lenient_open=True` (new engine only): a permissive
open that records post-parse validation problems instead of raising and
leaves the session in the editable `opened` state (the model is *not*
initialised). Read the recorded issues with `get_open_diagnostics`
(`errors` / `warnings` accumulators), fix them via the editing tools,
then initialise / run.

Worked example — open and run to completion:

```python
lifecycle_open_model(
    inp_path="/models/site_drainage.inp",
    session_id="run1",
    rpt_path="/out/site_drainage.rpt",
    out_path="/out/site_drainage.out",
    engine="openswmm",
)
lifecycle_run_simulation(session_id="run1")
lifecycle_close_model(session_id="run1")
```

----

## Query (`query_*`)

Read-only structural queries on the parsed model — useful when the
LLM needs to learn what the model contains before acting on it.

Tools: `get_node_info`, `get_link_info`, `get_subcatchment_info`,
`get_gage_info`, `get_pollutant_info`, `get_system_summary`,
`find_elements`.

Worked example — locate every outfall node:

```python
query_find_elements(
    session_id="run1",
    element_type="node",
    filter={"type": "OUTFALL"},
)
```

----

## Model (`model_*`)

Project-level metadata that lives outside any element table: input
title, simulation options (`[OPTIONS]` and extension options), CRS,
unit system, user flags (scalar scratch slots plus the `[USER_FLAGS]`
schema and `[USER_FLAG_VALUES]` per-object values), plugins,
file-section paths and the typed external-file path slots.

Tools: `get_title_count`, `get_title_line`, `get_title`,
`add_title_line`, `set_title`, `clear_title`, `get_option`,
`set_option`, `get_option_ext`, `set_option_ext`, `get_crs`,
`get_unit_system`, `get_pattern_factors`, `get_report_start`,
`set_report_start`, `list_aquifers`,
`list_snowpacks`,
`get_userflag_bool` / `set_userflag_bool`, `get_userflag_int` /
`set_userflag_int`, `get_userflag_real` / `set_userflag_real`,
`userflag_define`, `userflag_undefine`, `userflag_list_defs`,
`userflag_get_value`, `userflag_set_value`, `userflag_clear_value`,
`plugins_count`, `plugin_get`, `plugin_set`, `plugin_remove`,
`files_get`, `files_set`, `file_path_get`, `file_path_set`,
`write_with_plugin`.

User-flag schema tools carry typed definitions (`BOOLEAN` / `INTEGER` /
`REAL` / `STRING`) and per-object values keyed by object type + name
(e.g. tag node `J1` with `PRIORITY = 2`); values round-trip through the
INP `[USER_FLAGS]` / `[USER_FLAG_VALUES]` sections. The typed
external-file path slots (`file_path_get` / `file_path_set`) reach every
external-file reference in the model — interface files, the climate
file, per-gage data files, per-series data files, hot-start saves — and
report both the engine-resolved absolute path and the original token as
authored.

----

## Building (`building_*`)

Build a model in memory before (or instead of) loading a `.inp` file.

Tools: `create_model`, `add_node`, `pop_last_node`, `add_link`,
`pop_last_link`, `add_subcatchment`, `add_gage`, `add_pollutant`,
`set_option`, `add_timeseries`, `add_curve`, `validate_model`,
`write_model`.

Worked example — a two-node, one-conduit model:

```python
building_create_model(session_id="new1")
building_add_node(session_id="new1", id="J1",   type="JUNCTION")
building_add_node(session_id="new1", id="OUT1", type="OUTFALL")
building_add_link(
    session_id="new1", id="C1", type="CONDUIT",
    from_node="J1", to_node="OUT1",
)
building_validate_model(session_id="new1")
building_write_model(session_id="new1", path="/out/new1.inp")
```

----

## Editing (`editing_*`)

In-place mutation of an already-parsed model.  Deletions cascade
through dependent objects (links removed when a node is deleted,
inflows removed with their node, etc.) — see
{doc}`/developer/architecture` for the cascade policy.

Tools: `analyze_impact`, `delete_object`, `convert_node`,
`convert_link`, `set_node_properties`, `set_link_properties`,
`set_subcatchment_properties`, `configure_gage`,
`get_gage_scale_factor`, `set_gage_scale_factor`, `rename_node`,
`rename_link`, `rename_subcatchment`, `rename_gage`.

`analyze_impact` and `delete_object` dispatch on `object_type`, which is
one of `node`, `link`, `subcatchment`, `gage`, `table`, `transect`,
`pollutant`, `pattern`, `aquifer`, `snowpack`, `lid`, `street`, `inlet`,
`landuse`, or `hydrograph`. Both return the same impact report (the list
of objects that were, or would be, deleted or nullified).

Worked example — preview a delete, then commit:

```python
preview = editing_analyze_impact(
    session_id="run1", object_type="node", object_id="J5",
)
# inspect preview.impacts …
editing_delete_object(session_id="run1", object_type="node", object_id="J5")
```

----

## Nodes (`nodes_*`)

Fine-grained per-node accessors.  Bulk arrays let you pull every
node's depth / head / inflow / overflow / quality in one call.

Tools: bulk getters (`get_depths_bulk`, `get_heads_bulk`,
`get_inflows_bulk`, `get_overflows_bulk`, `get_quality_bulk`,
`set_depths_bulk`, `set_lat_inflows_bulk`, `get_ids_bulk`); storage parameters
(`get_storage_curve` / `set_storage_curve`,
`get_storage_functional` / `set_storage_functional`,
`get_storage_seep_rate` / `set_storage_seep_rate`,
`get_exfil_params` / `set_exfil_params`); outfall configuration
(`get_outfall_type`, `set_outfall_type`, `get_outfall_param`,
`set_outfall_stage`, `set_outfall_tidal`, `set_outfall_timeseries`,
`get_outfall_tidal`, `get_outfall_timeseries`,
`get_outfall_flap_gate` / `set_outfall_flap_gate`,
`get_outfall_route_to` / `set_outfall_route_to`); dividers
(`get_divider_type` / `set_divider_type`); quality
(`get_quality`, `set_quality_mass_flux`); tags
(`get_tag` / `set_tag`); statistics
(`stat_max_depth`, `stat_max_overflow`, `stat_vol_flooded`,
`stat_time_flooded`); helpers (`depth_from_volume`,
`set_head_boundary`).

----

## Links (`links_*`)

Fine-grained per-link accessors plus link-statistic accumulators.

Tools: bulk arrays (`get_flows_bulk`, `get_depths_bulk`,
`get_quality_bulk`, `set_flows_bulk`, `get_ids_bulk`,
`get_control_settings_bulk`, `get_target_settings_bulk`); control settings
(`get_control_setting` / `set_control_setting`,
`get_target_setting` / `set_target_setting`,
`get_closed` / `set_closed`); pumps
(`get_pump_curve` / `set_pump_curve`,
`get_pump_init_state` / `set_pump_init_state`,
`get_pump_shutoff_depth` / `set_pump_shutoff_depth`,
`get_pump_startup_depth` / `set_pump_startup_depth`); culverts and barrels
(`get_barrels` / `set_barrels`,
`get_culvert_code` / `set_culvert_code`); losses + flap gates
(`get_loss_coeff` / `set_loss_coeff`,
`get_seep_rate` / `set_seep_rate`,
`get_flap_gate` / `set_flap_gate`); weir / orifice
(`get_crest_height` / `set_crest_height`,
`get_discharge_coeff` / `set_discharge_coeff`,
`get_end_contractions` / `set_end_contractions`,
`get_orifice_open_close_rate` / `set_orifice_open_close_rate`); outlets
(`get_outlet_expon` / `set_outlet_expon`,
`get_outlet_rating_type` / `set_outlet_rating_type`);
cross-section (`get_xsect`); tags (`get_tag` / `set_tag`); quality
(`get_quality`); power (`hyd_power`); statistics
(`stat_max_flow`, `stat_max_velocity`, `stat_max_filling`,
`stat_vol_flow`, `stat_surcharge_time`, `stat_pump_cycles`,
`stat_pump_on_time`, `stat_pump_volume`).

----

## Subcatchments (`subcatchments_*`)

Per-subcatchment hydrology + quality.

Tools: bulk arrays (`get_runoff_bulk`, `get_quality_bulk`,
`get_ids_bulk`); state
(`get_runoff`, `get_rainfall`, `get_evap`, `get_groundwater`,
`get_snow_depth`, `get_infil`); coverage
(`get_coverage`, `set_coverage`); routing
(`set_outlet_subcatchment`); infiltration
(`get_infil_model`, `get_infil_horton` / `set_infil_horton`,
`get_infil_green_ampt` / `set_infil_green_ampt`,
`get_infil_curve_number` / `set_infil_curve_number`,
`set_infil_model`); aquifer / groundwater
(`aquifer_get_param` / `aquifer_set_param`,
`get_aquifer` / `set_aquifer`, `get_gw_node` / `set_gw_node`,
`get_gw_params` / `set_gw_params`); quality
(`get_quality`, `get_ponded_quality`, `set_ponded_quality`);
tags (`get_tag` / `set_tag`);
statistics (`stat_precip`, `stat_runoff_vol`, `stat_max_runoff`).

The aquifer/groundwater setters assign or detach the subcatchment's
aquifer (`set_aquifer`, pass `-1` to detach) and groundwater receiving
node (`set_gw_node`), and read/write the `[GROUNDWATER]` flow parameters
(`get_gw_params` / `set_gw_params`, which require an aquifer to be
assigned).

----

## Inflows (`inflows_*`)

External time-series inflows, dry-weather flow (DWF), RDII, unit
hydrographs (`[HYDROGRAPHS]`), and exponential initial-abstraction
decay (`[RDII_DECAY]`).

Tools: external (`add_external`, `get_external`, `remove_external`,
`set_external_baseline`, `set_external_scale`, `ext_inflow_count`); DWF
(`add_dwf`, `get_dwf`, `remove_dwf`, `set_dwf_baseline`, `dwf_count`);
RDII (`add_rdii`, `get_rdii`, `remove_rdii`,
`rdii_count`); unit hydrographs (`add_hydrograph`,
`get_hydrograph`, `hydrograph_count`, `add_hydrograph_gage`,
`get_hydrograph_gage`, `hydrograph_gage_count`,
`hydrograph_group_count`, `list_hydrograph_groups`,
`set_hydrograph_rtk`, `set_hydrograph_ia`, `remove_hydrograph_entry`,
`remove_hydrograph_group`, `clear_hydrograph_group_months`,
`rename_hydrograph_group`, `set_hydrograph_gage`); IA decay
(`add_rdii_decay`, `get_rdii_decay`, `rdii_decay_count`,
`set_rdii_decay`, `remove_rdii_decay`).

----

## Controls (`controls_*`)

SWMM control rules (`[CONTROLS]`).

Tools: `count`, `get_rule`, `get_id`, `list_rules`, `add_rule`,
`validate_rule`, `clear_rules`, `remove_rule`, `find_references`,
`set_link_setting`, `set_link_status`.

`remove_rule` deletes a single rule by index (later rules renumber down);
`find_references` returns the indices of rules that reference an object by
name — call it before deleting or renaming an object to see which rules
are affected.

----

## Forcing (`forcing_*`)

Per-step overrides — useful for closed-loop or interactive control.

Tools: `set_forcing`, `set_persistent_forcing`, `clear_forcing`,
`set_link_control`, `add_control_rule`, `set_rainfall_override`,
`set_link_quality`, `get_climate_evap_rate`.

`get_climate_evap_rate` reads back the climate-derived PET rate the
engine would apply on its own (in/day US, mm/day SI) so a client can
compose its own adjustment and prescribe the result via
`set_forcing(target_type="subcatchment", variable="evap")`.

----

## Pollutants (`pollutants_*`)

Identity and properties of the modeled pollutants, plus runtime
injection.

Tools: identity (`count`, `add`); properties
(`get_units`,
`get_kdecay` / `set_kdecay`,
`get_rain_conc` / `set_rain_conc`,
`get_gw_conc` / `set_gw_conc`,
`get_rdii_conc` / `set_rdii_conc`,
`get_dwf_conc` / `set_dwf_conc`,
`get_init_conc` / `set_init_conc`,
`get_mwt` / `set_mwt`,
`get_snow_only` / `set_snow_only`,
`get_co_pollutant` / `set_co_pollutant`); runtime injection
(`set_node_quality`, `set_link_quality`).

----

## Quality (`quality_*`)

Buildup / washoff / treatment kinetics, landuse identity, sweeping.

Tools: landuse (`landuse_count`, `landuse_add`, `landuse_id`,
`landuse_index`); sweeping (`get_sweep_interval` /
`set_sweep_interval`, `get_sweep_removal` / `set_sweep_removal`);
buildup (`buildup_get`, `buildup_set`); washoff (`washoff_get`,
`washoff_set`); treatment (`treatment_get`, `treatment_clear`).

----

## Tables (`tables_*`)

Time series, curves, and patterns — the shared "table" abstraction in
SWMM.

Tools: identity (`count`, `get_id`, `get_index`, `get_type`); add
(`add_timeseries`, `add_curve`); points (`add_point`, `get_point`,
`get_point_count`, `get_points`, `clear_points`, `lookup`); patterns
(`pattern_count`, `pattern_add`, `pattern_set_factors`,
`pattern_remove`).

----

## Date/time (`datetime_*`)

Pure SWMM-DateTime conversion utilities — thin wrappers over the SWMM
DateTime C API. A SWMM DateTime is a `double` whose integer part is days
since 1899-12-30 and whose fractional part is the time-of-day fraction.
These tools take no `session_id` and touch no engine state, so they work
without an open model.

Tools: `encode_date`, `encode_time`, `decode_date`, `decode_time`,
`add_seconds`, `time_diff`.

----

## Infrastructure (`infrastructure_*`)

Transects, streets, inlets, LID controls.

Tools: transects (`transect_count`, `add_transect`, `remove_transect`,
`set_transect_roughness`, `get_transect_roughness`,
`add_transect_station`, `get_station`, `station_count`,
`clear_stations`, `get_bank_stations` / `set_bank_stations`,
`get_encroachment_stations` / `set_encroachment_stations`,
`get_modifiers` / `set_modifiers`,
`get_comments` / `set_comments`); streets
(`street_count`, `add_street`, `set_street_params`,
`get_street_params`); inlets
(`inlet_count`, `add_inlet`, `set_inlet_params`); LIDs
(`lid_count`, `add_lid`, `get_lid_surface` / `set_lid_surface`,
`get_lid_soil` / `set_lid_soil`, `get_lid_storage` / `set_lid_storage`,
`get_lid_drain` / `set_lid_drain`, `get_lid_pavement` / `set_lid_pavement`,
`get_lid_drainmat` / `set_lid_drainmat`, `add_lid_usage`,
`lid_usage_count`, `lid_usage_get`, `lid_usage_remove`).

All six LID layers now round-trip: every `set_lid_*` tool has a matching
`get_lid_*` returning the same parameter keys, so a configuration written
through MCP can be read back and compared. The layer tools accept either a
string LID ID or an integer index for `lid_index`.

The LID-usage read/remove tools let the `[LID_USAGE]` block round-trip:
`lid_usage_count` reports the number of placement rows, `lid_usage_get`
returns one row by global index, and `lid_usage_remove` deletes one.

----

## Hot-start (`hotstart_*`)

Hot-start file save / load, session cloning, and the engine-side
scheduled-save registry (`[SAVE HOTSTART]`).

Tools: `save_hotstart`, `load_hotstart`, `clone_session`,
`saves_count`, `saves_get`, `saves_add`, `saves_set`, `saves_remove`,
`saves_clear`.

----

## Analysis (`analysis_*`)

Post-run analytics built on the binary `.out` file plus engine
statistics.

Tools: high-level (`get_statistics`, `get_mass_balance`,
`get_time_series`, `get_flooding_summary`, `get_capacity_summary`,
`get_pump_summary`, `get_report_snapshot`, `get_quality_losses`,
`compare_scenarios`,
`export_results`); output-reader breadth (`output_metadata`,
`output_period_count`, `output_pollutant_count`,
`output_period_time`, `output_node_attribute`, `output_link_attribute`,
`output_subcatch_attribute`, `output_system_result`,
`output_node_results`, `output_link_results`,
`output_subcatch_results`).

Worked example — peak flooded volume per node:

```python
analysis_get_flooding_summary(session_id="run1")
```

----

## Spatial (`spatial_*`)

Coordinates, polylines, polygons, and the project-wide CRS.

Tools: per-element (`get_coordinates`, `set_coordinates`,
`get_vertices`, `set_vertices`, `get_polygon`, `set_polygon`,
`set_gage_coord`, `get_quality`, `set_treatment`, `add_lid`);
project-wide (`get_all_coordinates`, `set_node_coords_bulk`,
`get_crs`, `set_crs`, `get_all_vertices`,
`get_all_polygons`, `get_model_geometry`).

----

## GeoPackage (`geopackage_*`)

GeoPackage I/O — bind a `.gpkg` "data warehouse" for both simulated
and observed time series.

Tools: `is_registered`, `register`, `open_geopackage`,
`list_simulations`, `get_result_timeseries`, `get_result_summary`,
`import_observed_data`, `write_observed_value`,
`compare_sim_vs_observed`, `query_int`, `query_double`,
`topology_edge_count`, `last_error`, `close_geopackage`.

----

## 2D Surface (`twod_*`)

The 2D overland-flow surface — available when the model carries
`[2D_*]` sections (or an external mesh file). All tools require the
new `openswmm` engine backend; `get_mesh_summary` reports
`active: false` on 1D-only models and every other tool fails fast with
a clear error. Bulk per-triangle results return summary statistics
(count / min / max / mean) plus an optional `offset` / `limit` window,
so responses stay compact on large meshes.

Tools:

* Mesh — `get_mesh_summary` (counts + couplings), `get_mesh_geometry`
  (windowed triangle geometry: vertices, area, centroid, Manning's n,
  neighbours), `get_edge_geometry_bulk` (time-invariant per-edge
  geometry for the whole mesh), `set_vertex_z` (terrain edits),
  `set_triangle_mannings` (per-triangle roughness edits),
  `get_triangle_tag` / `set_triangle_tag` and
  `get_vertex_tag` / `set_vertex_tag` (`[2D_TRIANGLES]` / `[2D_VERTICES]`
  TAG columns), `get_coupling_map` and `set_vertex_coupled_node` (which
  vertices / triangles exchange flow with which 1D nodes; the setter
  couples a vertex to a 1D node by id, `""` clears it).
* State — `get_state` (one triangle: depth, head, rainfall, net
  source, coupling flux), `get_vertex_head` (reconstructed
  water-surface head at one vertex), `get_state_bulk` (`depth` /
  `head` / `vertex_head` / `vertex_render_depth` / `coupling_flux` /
  `edge_flux` summaries; `vertex_render_depth` is the signed
  `eta_v - z_v` field for water-surface rendering),
  `get_totals` (max depth, ponded volume, exchange flow,
  internal-stepper diagnostics).
* Results — `get_stats` (per-triangle max depth / velocity /
  continuity-residual hot spots, ranked top-N), `get_mass_balance`
  (global m³ terms + continuity error).
* Forcing — `force_rainfall` (uniform or per-triangle, replace / add,
  optional persist), `force_evap`, `force_coupling_flux`, `force_clear`.
* Solver — `get_solver_params` / `set_solver_params` (dry depth; the
  explicit-marcher `[2D_OPTIONS]` keys such as `THETA`, `CFL_NUMBER`
  and `LTS_TIERS` are read / written with `model_get_option_ext` /
  `model_set_option_ext`).
* Boundaries — `get_edge_bc` / `set_edge_bc` (WALL, NORMAL_FLOW,
  SPECIFIED_STAGE, SPECIFIED_FLOW, RATING_CURVE; constants,
  timeseries, or rating-curve drivers).
* Conveyance — `get_edge_conveyance` (single edge or whole-mesh scan
  of restricted edges), `set_edge_conveyance` (0 = wall, 1 = open;
  interior edges mirror to the neighbour), `reset_edge_conveyance`.

----

## See also

* {doc}`/api/index` — full auto-generated signatures and docstrings
  for every tool.
* {doc}`/user-guide/resources` — `swmm://` resource URIs that
  complement these tools.
* {doc}`/user-guide/prompts` — guided-workflow prompts that compose
  tools end-to-end.
* {doc}`/user-guide/examples` — full transcripts illustrating common
  tool sequences.
