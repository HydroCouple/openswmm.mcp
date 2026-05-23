# Tools

The OpenSWMM MCP server exposes **~317 tools** across **19 domain
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
| `lifecycle_*` | 16 | Open / step / run / close models, list sessions, manage event windows, steady-state skip. |
| `query_*` | 7 | Read-only "what does this model contain?" queries (nodes, links, subcatchments, gages, pollutants, system summary, free-form search). |
| `model_*` | 24 | Project-level metadata: title, options + extension options, CRS, user flags, plugins, file-section paths. |
| `building_*` | 13 | Programmatic model construction: nodes, links, subcatchments, gages, options, time series, curves, pollutants, validation + write. |
| `editing_*` | 12 | In-place edits on a parsed model: cascade-delete, type conversion, property setters, renames. |
| `nodes_*` | 34 | Per-node accessors: bulk arrays, storage curves, outfall configuration, dividers, exfiltration, quality. |
| `links_*` | 40 | Per-link accessors: bulk arrays, control settings, pump / weir / orifice / culvert parameters, statistics. |
| `subcatchments_*` | 23 | Per-subcatchment accessors: runoff state, infiltration, coverage, ponded quality, statistics. |
| `inflows_*` | 18 | External / DWF / RDII inflows, unit hydrographs, exponential IA decay. |
| `controls_*` | 8 | SWMM control rules: add, clear, set link setting / status. |
| `forcing_*` | 5 | Per-step overrides (lateral inflow, rainfall, link control). |
| `pollutants_*` | 21 | Pollutant identity + properties (decay, rain/GW/RDII concentrations, co-pollutant, snow-only, runtime injection). |
| `quality_*` | 14 | Buildup / washoff / treatment kinetics; landuse and street-sweeping setup. |
| `tables_*` | 14 | Time series, curves, patterns; lookup helpers. |
| `infrastructure_*` | 17 | Transects, streets, inlets, LID controls and LID-usage. |
| `hotstart_*` | 9 | Hot-start save / load, session cloning, scheduled-save registry. |
| `analysis_*` | 20 | Post-run analytics: statistics, mass balance, flooding / capacity summaries, scenario compare, full output-reader breadth (snapshot + series + attribute). |
| `spatial_*` | 15 | Coordinates, polylines, polygons, CRS, project-wide geometry export, LID placement. |
| `geopackage_*` | 7 | GeoPackage I/O: open, list simulations, read result series / summaries, compare simulated vs observed. |

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

Tools: `open_model`, `run_simulation`, `step_simulation`,
`get_simulation_time`, `get_simulation_state`, `close_model`,
`list_sessions`, `events_count`, `events_get`, `events_add`,
`events_set`, `events_remove`, `events_clear`, `is_between_events`,
`get_steady_state_skip`, `set_steady_state_skip`.

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
user flags (free-form scratch slots), plugins, file-section paths.

Tools: `get_title_count`, `get_title_line`, `get_title`,
`add_title_line`, `set_title`, `clear_title`, `get_option`,
`set_option`, `get_option_ext`, `set_option_ext`, `get_crs`,
`get_userflag_bool` / `set_userflag_bool`, `get_userflag_int` /
`set_userflag_int`, `get_userflag_real` / `set_userflag_real`,
`plugins_count`, `plugin_get`, `plugin_set`, `plugin_remove`,
`files_get`, `files_set`, `write_with_plugin`.

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
`set_subcatchment_properties`, `configure_gage`, `rename_node`,
`rename_link`, `rename_subcatchment`, `rename_gage`.

Worked example — preview a delete, then commit:

```python
preview = editing_analyze_impact(
    session_id="run1", kind="node", id_or_index="J5",
)
# inspect preview.affected …
editing_delete_object(session_id="run1", kind="node", id_or_index="J5")
```

----

## Nodes (`nodes_*`)

Fine-grained per-node accessors.  Bulk arrays let you pull every
node's depth / head / inflow / overflow / quality in one call.

Tools: bulk getters (`get_depths_bulk`, `get_heads_bulk`,
`get_inflows_bulk`, `get_overflows_bulk`, `get_quality_bulk`,
`set_depths_bulk`, `set_lat_inflows_bulk`); storage parameters
(`get_storage_curve` / `set_storage_curve`,
`get_storage_functional` / `set_storage_functional`,
`get_storage_seep_rate` / `set_storage_seep_rate`,
`get_exfil_params` / `set_exfil_params`); outfall configuration
(`get_outfall_type`, `set_outfall_type`, `get_outfall_param`,
`set_outfall_stage`, `set_outfall_tidal`, `set_outfall_timeseries`,
`get_outfall_flap_gate` / `set_outfall_flap_gate`,
`get_outfall_route_to` / `set_outfall_route_to`); dividers
(`get_divider_type` / `set_divider_type`); quality
(`get_quality`, `set_quality_mass_flux`); statistics
(`stat_max_depth`, `stat_max_overflow`, `stat_vol_flooded`,
`stat_time_flooded`); helper (`depth_from_volume`).

----

## Links (`links_*`)

Fine-grained per-link accessors plus link-statistic accumulators.

Tools: bulk arrays (`get_flows_bulk`, `get_depths_bulk`,
`get_quality_bulk`, `set_flows_bulk`); control settings
(`get_control_setting` / `set_control_setting`,
`get_target_setting` / `set_target_setting`,
`get_closed` / `set_closed`); pumps
(`get_pump_curve` / `set_pump_curve`,
`get_pump_init_state` / `set_pump_init_state`); culverts and barrels
(`get_barrels` / `set_barrels`,
`get_culvert_code` / `set_culvert_code`); losses + flap gates
(`get_loss_coeff` / `set_loss_coeff`,
`get_seep_rate` / `set_seep_rate`,
`get_flap_gate` / `set_flap_gate`); weir / orifice
(`get_crest_height` / `set_crest_height`,
`get_discharge_coeff` / `set_discharge_coeff`,
`get_end_contractions` / `set_end_contractions`); quality
(`get_quality`); power (`hyd_power`); statistics
(`stat_max_flow`, `stat_max_velocity`, `stat_max_filling`,
`stat_vol_flow`, `stat_surcharge_time`, `stat_pump_cycles`,
`stat_pump_on_time`, `stat_pump_volume`).

----

## Subcatchments (`subcatchments_*`)

Per-subcatchment hydrology + quality.

Tools: bulk arrays (`get_runoff_bulk`, `get_quality_bulk`); state
(`get_runoff`, `get_rainfall`, `get_evap`, `get_groundwater`,
`get_snow_depth`, `get_infil`); coverage
(`get_coverage`, `set_coverage`); infiltration
(`get_infil_model`, `get_infil_horton` / `set_infil_horton`,
`get_infil_green_ampt` / `set_infil_green_ampt`,
`get_infil_curve_number` / `set_infil_curve_number`); quality
(`get_quality`, `get_ponded_quality`, `set_ponded_quality`);
statistics (`stat_precip`, `stat_runoff_vol`, `stat_max_runoff`).

----

## Inflows (`inflows_*`)

External time-series inflows, dry-weather flow (DWF), RDII, unit
hydrographs (`[HYDROGRAPHS]`), and exponential initial-abstraction
decay (`[RDII_DECAY]`).

Tools: external (`add_external`, `ext_inflow_count`); DWF
(`add_dwf`, `dwf_count`); RDII (`add_rdii`, `get_rdii`,
`rdii_count`); unit hydrographs (`add_hydrograph`,
`get_hydrograph`, `hydrograph_count`, `add_hydrograph_gage`,
`get_hydrograph_gage`, `hydrograph_gage_count`,
`hydrograph_group_count`, `list_hydrograph_groups`); IA decay
(`add_rdii_decay`, `get_rdii_decay`, `rdii_decay_count`).

----

## Controls (`controls_*`)

SWMM control rules (`[CONTROLS]`).

Tools: `count`, `get_rule`, `get_id`, `list_rules`, `add_rule`,
`clear_rules`, `set_link_setting`, `set_link_status`.

----

## Forcing (`forcing_*`)

Per-step overrides — useful for closed-loop or interactive control.

Tools: `set_forcing`, `clear_forcing`, `set_link_control`,
`add_control_rule`, `set_rainfall_override`.

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

Tools: identity (`count`, `get_id`, `get_index`); add
(`add_timeseries`, `add_curve`); points (`add_point`, `get_point`,
`get_point_count`, `get_points`, `clear_points`, `lookup`); patterns
(`pattern_count`, `pattern_add`, `pattern_set_factors`).

----

## Infrastructure (`infrastructure_*`)

Transects, streets, inlets, LID controls.

Tools: transects (`transect_count`, `add_transect`,
`set_transect_roughness`, `add_transect_station`); streets
(`street_count`, `add_street`, `set_street_params`); inlets
(`inlet_count`, `add_inlet`, `set_inlet_params`); LIDs
(`lid_count`, `add_lid`, `set_lid_surface`, `set_lid_soil`,
`set_lid_storage`, `set_lid_drain`, `add_lid_usage`).

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
`get_pump_summary`, `get_report_snapshot`, `compare_scenarios`,
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
`get_quality`, `set_treatment`, `add_lid`); project-wide
(`get_all_coordinates`, `get_crs`, `set_crs`, `get_all_vertices`,
`get_all_polygons`, `get_model_geometry`).

----

## GeoPackage (`geopackage_*`)

GeoPackage I/O — bind a `.gpkg` "data warehouse" for both simulated
and observed time series.

Tools: `open_geopackage`, `list_simulations`,
`get_result_timeseries`, `get_result_summary`,
`import_observed_data`, `compare_sim_vs_observed`,
`close_geopackage`.

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
