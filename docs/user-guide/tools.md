# Tools

The OpenSWMM MCP server exposes **650 tools** across **30 domain
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
| `lifecycle_*` | 28 | Open / step / run / close models (incl. lenient open + open-diagnostics read-back), list sessions, manage event windows, steady-state skip, runoff-interface files. |
| `query_*` | 7 | Read-only "what does this model contain?" queries (nodes, links, subcatchments, gages, pollutants, system summary, free-form search). |
| `model_*` | 38 | Project-level metadata: title, options + extension options, CRS, unit system, scalar user flags + user-flag schema / per-object values, plugins, file-section paths + typed external-file path slots, aquifer / snowpack listings, pattern factors, report start. |
| `building_*` | 13 | Programmatic model construction: nodes, links, subcatchments, gages, options, time series, curves, pollutants, validation + write. |
| `editing_*` | 34 | In-place edits on a parsed model: cascade-delete + impact analysis (nodes, links, subcatchments, gages, tables, transects, pollutants, patterns, aquifers, snowpacks, LID controls, streets, inlets, land uses, hydrographs), type conversion, inlet-junction promote / split-in / fuse-out, property setters, renames, gage scale factor. |
| `nodes_*` | 49 | Per-node accessors: bulk arrays, storage curves, outfall configuration, dividers, exfiltration, quality, tags, head boundary, virtual- and inlet-junction flags + eligibility checks. |
| `links_*` | 59 | Per-link accessors: bulk arrays, control settings, pump / weir / orifice / outlet / culvert parameters, cross-section, tags, statistics. |
| `subcatchments_*` | 56 | Per-subcatchment accessors: runoff state, infiltration (incl. model switch), coverage, ponded quality, statistics, tags, outlet routing, aquifer / groundwater assignment + `[GROUNDWATER]` params. |
| `inflows_*` | 35 | External / DWF / RDII inflows, unit hydrographs, exponential IA decay — full read / edit / remove lifecycle. |
| `controls_*` | 11 | SWMM control rules: add, clear, validate, remove one rule, find rules referencing an object, set link setting / status. |
| `forcing_*` | 11 | Per-step overrides (lateral inflow, rainfall, PET, link control, link quality) and climate-evaporation read-back. |
| `pollutants_*` | 23 | Pollutant identity + properties (decay, rain/GW/RDII/DWF concentrations, co-pollutant, snow-only, runtime injection). |
| `quality_*` | 15 | Buildup / washoff / treatment kinetics; landuse and street-sweeping setup. |
| `tables_*` | 16 | Time series, curves, patterns; type query, lookup helpers, pattern removal. |
| `infrastructure_*` | 50 | Transects (full profile / bank / encroachment / modifier editing), streets, inlets (full `[INLETS]` design records + the inlet placement table shared by `[INLET_USAGE]` rows and inlet junctions), LID controls and LID-usage (add / count / get / remove). |
| `hotstart_*` | 11 | Hot-start save / load, state seeding, session cloning, scheduled-save registry. |
| `analysis_*` | 22 | Post-run analytics: statistics, mass balance, flooding / capacity summaries, quality losses, scenario compare, results export, full output-reader breadth (snapshot + series + attribute). |
| `spatial_*` | 17 | Coordinates, polylines, polygons, CRS, gage coords, bulk node coords, project-wide geometry export, LID placement. |
| `geopackage_*` | 14 | GeoPackage I/O: plugin registration, open, list simulations, read result series / summaries, raw SQL scalars, topology, compare simulated vs observed, observed-value writes. |
| `twod_*` | 35 | 2D overland-flow surface: mesh queries + edits (vertex Z per-vertex or whole-mesh, triangle Manning's n, triangle/vertex tags, vertex→node coupling), per-triangle / per-vertex state (incl. vertex render depths), edge geometry, statistics + mass balance, runtime forcing, solver parameters, edge boundary conditions (incl. driver-name read-back), edge conveyance. |
| `climate_*` | 7 | Climate *configuration* before a run: `[TEMPERATURE]`, `[EVAPORATION]`, `[WINDSPEED]`, `[ADJUSTMENTS]`, snowmelt globals, areal-depletion curves. |
| `heat_*` | 18 | Heat transport: `[HEAT_FLUXES]` module toggles, `[RADIATIVE_FLUXES]` / `[SOLAR_RADIATION]` / `[CLOUD_COVER]` parameters, shortwave mode, `[HEAT_SOURCES]` inlet temperatures + per-node overrides. Temperatures are degC and refused (never clamped) outside `[-50, 100]`. |
| `water_age_*` | 6 | `[WATER_AGE_SOURCES]`: per-pathway global ages plus `dwf` / `external_inflow` node overrides. Values are HOURS and may be negative (age extraction). |
| `reactions_*` | 26 | Multi-species reactions: species / coefficients / terms, pipe + tank expressions, `[REACTION_OPTIONS]`, initial quality, whole-file `.rxn` text, expression pre-flight, and the static completer vocabulary. |
| `initial_quality_*` | 3 | `[INITIAL_QUALITY]` per-element seeds — pollutant concentrations plus the reserved `__WATER_AGE__` (hours) and `__TEMPERATURE__` (degC) species. |
| `process_components_*` | 4 | `[PROCESS_COMPONENTS]` registrations: which process components load, from which config path, and what that path resolved to. |
| `infil2d_*` | 12 | Per-cell 2D mesh infiltration: `[2D_INFILTRATION_OPTIONS]`, tag defaults, per-cell overrides, rate / cumulative / total-volume readback. |
| `xsect_*` | 14 | Pure cross-section geometry maths (area, depth, hydraulic radius, section factor, critical depth) — no open session required. |
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
`rename_link`, `rename_subcatchment`, `rename_gage`; inlet junctions
(`set_node_inlet`, `split_conduit_inlet`, `fuse_inlet_junction`).

An **inlet junction** (`[INLET_JUNCTIONS]`, refactored engine only) is a
virtual junction between two STREET conduits that also carries a street
inlet: it captures the gutter flow arriving on the upstream conduit,
delivers it to a capture node, and passes the bypass on downstream.
`set_node_inlet` promotes an eligible node (preview with
`nodes_inlet_eligible`) or demotes it back to a virtual junction;
`split_conduit_inlet` inserts one into a street conduit together with its
placement row in a single step; `fuse_inlet_junction` removes it and
re-fuses the two conduits. The placement itself (design, capture node,
`[INLET_USAGE]` tail) is edited through `infrastructure_inlet_usage_*`.

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
`set_head_boundary`); virtual / inlet junctions (`is_virtual`,
`virtual_eligible`, `is_inlet`, `inlet_eligible` — the eligibility checks
are read-only dry runs returning the violated rule code, 609-621 for the
virtual-junction rules and 623 when the conduits are not STREET, or 0).

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
(`inlet_count`, `add_inlet`, `set_inlet_params`,
`get_inlet_design` / `set_inlet_design`); inlet placements
(`inlet_usage_count`, `inlet_usage_list`, `inlet_usage_get`,
`inlet_usage_find`, `inlet_usage_set`, `inlet_usage_remove`); LIDs
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

`get_inlet_design` / `set_inlet_design` cover the whole `[INLETS]` record
for every design type — `grate`, `curb`, `combo` (grate plus curb opening),
`slotted`, `drop_grate`, `drop_curb`, `custom` (a DIVERSION or RATING
capture curve) — with enum fields as lower-case tokens (`curved_vane`,
`inclined`, ...). `set_inlet_design` is a read-modify-write: pass only the
fields that change.

The inlet placement table is shared by conduit-hosted `[INLET_USAGE]` rows
(`host_kind="link"`) and inlet junctions (`host_kind="node"`,
`[INLET_JUNCTIONS]`). Each row names the inlet design, the capture node the
captured flow is delivered to, and the `[INLET_USAGE]` tail (`num_inlets`,
`pct_clogged`, `flow_limit`, `local_depress`, `local_width`, `placement`);
`inlet_usage_find` looks a host up, `inlet_usage_set` creates or replaces
the host's single row, `inlet_usage_remove` deletes one by index.

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
  `set_vertex_z_bulk` (every vertex elevation from one positional
  array — one mesh rescan instead of one per vertex),
  `set_triangle_mannings` (per-triangle roughness edits),
  `get_triangle_tag` / `set_triangle_tag` and
  `get_vertex_tag` / `set_vertex_tag` (`[2D_TRIANGLES]` / `[2D_VERTICES]`
  TAG columns), `get_coupling_map` and `set_vertex_coupled_node` (which
  vertices / triangles exchange flow with which 1D nodes; the setter
  couples a vertex to a 1D node by id, `""` clears it).
* State — `get_state` (one triangle: depth, head, rainfall, net
  source, coupling flux), `get_vertex_head` (reconstructed
  water-surface head at one vertex, plus that vertex's own x / y / z),
  `get_state_bulk` (`depth` /
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
  timeseries, or rating-curve drivers). The getter mirrors back the
  bound driver names (`tseries_name`, `flow_tseries_name`,
  `rating_curve_name`); `""` means the slot is clear and the edge
  falls back to its constant head / flow.
* Conveyance — `get_edge_conveyance` (single edge or whole-mesh scan
  of restricted edges), `set_edge_conveyance` (0 = wall, 1 = open;
  interior edges mirror to the neighbour), `reset_edge_conveyance`.

----

## 2D Infiltration (`infil2d_*`)

Per-cell infiltration on the 2D overland-flow mesh — the `GROUNDWATER
OFF` loss model. **Row parameters are in project units** (the numbers a
user types into a legacy `[INFILTRATION]` row); every readback channel is
SI, like the rest of the 2D surface (rate m/s, cumulative depth m, total
volume m³). Resolution is most-specific-wins: per-cell override > tag row
> `'*'` row > no infiltration.

Tools:

* Options — `get_options`, `set_options` (`[2D_INFILTRATION_OPTIONS]`,
  including the evaluation cadence).
* Tag defaults — `get_defaults`, `set_default`, `remove_default`
  (`[2D_INFILTRATION_DEFAULTS]`; the tag `"*"` is the mesh-wide fallback).
* Per-cell overrides — `get_cell`, `set_cell`, `set_cells`,
  `clear_cells` (`[2D_INFILTRATION]`).
* Readback — `get_rate_bulk`, `get_cum_bulk`, `get_total_volume`.

----

## Climate configuration (`climate_*`)

The model's climate *configuration*, edited before a run — as distinct
from the runtime climate *forcing* in `forcing_*`, which overrides live
temperature / wind / evaporation while a simulation is running. Values
use the project's display units, exactly as in the `.inp` file. Reads
work from `opened` onwards; edits require `opened`, because once the
engine is initialized the configuration has been baked into the
climate / snow solver.

Tools: `get_climate_config`, `set_temperature_config`,
`set_evaporation_config`, `set_windspeed_config`,
`set_snowmelt_config`, `set_areal_depletion`, `set_adjustments`.

----

## Cross-section geometry (`xsect_*`)

Pure cross-section geometry maths — **no open session required**. Given a
shape and its dimensions, these compute the hydraulic properties SWMM
itself uses, so a caller can size a conduit or invert a rating without
constructing a model first.

Tools:

* Properties — `properties` (a shape by name + dimensions),
  `properties_from_transect`, `properties_from_street`,
  `properties_from_curve`, `list_shapes` (the authoritative shape
  vocabulary).
* Forward lookups — `area_of_depth`, `width_of_depth`,
  `hydrad_of_depth`, `sectfactor_of_area`, `hydrad_of_area`.
* Inverse lookups — `depth_of_area`, `area_of_sectfactor`,
  `critical_depth`, `dsda`.

----

## Heat transport (`heat_*`)

Heat-transport configuration: which flux modules run, the radiative /
solar / cloud parameters they read, and the temperature water enters the
network at. **Temperatures are degrees Celsius; radiation is W/m²;
albedo, emissivity and cloud fraction are fractions in `[0, 1]`, never
percents.**

Two contracts matter before writing. First, **values are refused, not
clamped** — every setter mirrors the `.inp` parser's own range check, and
a refused write does not take effect at all. Inlet temperatures must lie
in `[-50, 100]` degC. Second, **edits are live**: the flux modules re-read
their configuration every routing step, so these tools work in `opened`
through `ended` and a mid-run edit applies on the next step.

Three refusals worth anticipating rather than discovering:
`computed` shortwave requires BOTH latitude and longitude to have been set
(check `solar_sited` on `get_config` first — the engine will not borrow the
`[TEMPERATURE]` SNOWMELT latitude, which defaults to 0); the `shortwave`
radiative constant is writable only while the mode is `constant`; and only
the `dwf` and `external_inflow` sources take per-node overrides.

Tools:

* Overview — `get_config` (enabled flag, all module toggles, shortwave
  mode, `solar_sited`, `cloud_configured`, and the current resolved
  shortwave / cloud state), `set_module`.
* Radiative — `get_radiative`, `set_radiative` (keyed by parameter name;
  omit the name to read every parameter at once).
* Solar — `get_solar`, `set_solar` (`[SOLAR_RADIATION]`; latitude degrees
  north, longitude degrees east, elevation metres, timezone hours from UTC).
* Cloud — `get_cloud`, `set_cloud`, `set_cloud_timeseries`, `clear_cloud`.
* Shortwave — `set_shortwave_mode` (`constant` / `timeseries` /
  `computed`; pass a `timeseries` name to bind and select in one call).
* Inlet temperatures — `list_sources`, `set_source_temp`,
  `clear_source_temp`, `list_node_overrides`, `set_node_override`,
  `remove_node_override`, `get_effective_source_temp` (the resolved value
  the engine itself uses, override-then-global).

----

## Water age (`water_age_*`)

The `[WATER_AGE_SOURCES]` table: the age carried by water entering the
network on each pathway. **Ages are HOURS**, and **negative values are
legal and meaningful** — a negative source age extracts age-volume rather
than adding it, clamped so a computed age never falls below zero.

Like the heat table, edits are LIVE (the loaders re-read every routing
step) and only `dwf` and `external_inflow` take per-node overrides; every
other pathway refuses NODE scope exactly as the parser does. Initial
per-element ages are seeded through `[INITIAL_QUALITY]`'s reserved
`__WATER_AGE__` constituent — see `initial_quality_*`.

Tools: `get_config` (the `[OPTIONS] WATER_AGE` switch plus every
pathway's global age), `set_global_source`, `list_node_overrides`,
`set_node_override`, `remove_node_override` (keyed on the source / node
pair, not a row position), `save_sources`.

----

## Reactions (`reactions_*`)

The multi-species reaction system — `[REACTION_*]` sections and the
`.rxn` whole-file surface.

**Pre-flight expressions rather than submitting blind.**
`validate_expression` compiles ONE expression against the model's live
vocabulary and returns a 1-based error column; `check_text` dry-runs a
whole `.rxn` file. Neither changes state on success or failure.

**The vocabulary is authoritative, not guessable.** Enumerate what an
expression may reference from `list_species`, `list_coefficients`,
`list_terms`, `hydraulic_variables` and `functions` rather than
hard-coding identifier lists. The last two are engine-less statics —
they take **no session** and work before any model is open.

**Mutation is transactional and BUILDING/OPENED only.** Every mutator
recompiles the whole system before returning and rolls back anything that
would leave an expression uncompilable, so the model can never store
something that will not compile. Removing a species, coefficient or term
that is still referenced is refused. `apply_text` is staged the same way
and invalidates every index on success.

Tools:

* Species — `list_species`, `add_species`, `remove_species`,
  `get_species_expression`, `set_species_expression` (`pipe` / `tank`
  scope; form `rate` / `equil` / `formula` / `none`, where `none` clears).
* Coefficients — `list_coefficients`, `add_coefficient`,
  `set_coefficient_value`, `remove_coefficient`.
* Terms — `list_terms`, `add_term`, `set_term_expression`, `remove_term`.
* Options — `get_option`, `set_option` (`SOLVER`, `COUPLING`,
  `RATE_UNITS`, `AREA_UNITS`, `TIMESTEP`, `ATOL`, `RTOL`).
* Initial quality — `list_initial_quality`, `set_initial_global`,
  `set_initial_element`, `remove_initial_element`.
* Validation — `validate_expression`, `check_text`.
* Whole file — `serialize`, `apply_text`, `save`. `serialize` →
  `apply_text` → `serialize` is byte-identical, so serialize-edit-apply
  is a safe editing loop.
* Static vocabulary (no session) — `hydraulic_variables`, `functions`.

----

## Initial quality (`initial_quality_*`)

The `[INITIAL_QUALITY]` table: per-element starting values, in contrast to
`pollutants_set_init_conc`, which sets one network-wide default per
pollutant.

The table is an **upsert** keyed on (element kind, element, constituent),
so writing the same triple twice edits the existing row. Two constituent
names are reserved: `__WATER_AGE__` (hours) and `__TEMPERATURE__` (degC),
both of which accept negative values; every other constituent must be a
`[POLLUTANTS]` pollutant whose value is a non-negative concentration in
its own declared units.

Rows only seed state at initialize(), so mutation is `building` /
`opened` only; reads work in later states. Removal shifts later row
indices down, so re-enumerate between removals.

Tools: `list_entries`, `set_entry`, `remove_entry`.

----

## Process components (`process_components_*`)

The `[PROCESS_COMPONENTS]` registrations — which process components (heat,
water age, reactions, …) a model loads and from which config file.

Each registration reports `config` (the argument as written, possibly
empty or relative) and `resolved` (the path the config was actually READ
from at the last open, empty until resolution). When a component is not
behaving as expected, `resolved` is what says which file it really loaded.

**A config path need not exist yet.** Registering against a file you have
not written is legal and is the intended order: register first, write the
file second, resolve at the next open — so an empty `resolved` on a fresh
registration is expected, not a failure. Registration and removal are
`building` / `opened` only, and a duplicate id is refused.

Tools: `list_components`, `find_component`, `register_component`,
`remove_component`.

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
