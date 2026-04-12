# Tools

The OpenSWMM MCP Server provides over 35 tools organised into seven
namespaced phases. Each tool name is prefixed with its namespace
(e.g. `lifecycle_open_model`, `query_get_node_info`).

## Lifecycle Tools (`lifecycle_*`)

Tools for opening, running, stepping, and closing SWMM models.

### `lifecycle_open_model`

Open a SWMM model file and initialise the engine.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `inp_path` | `str` | *required* | Path to the `.inp` file. |
| `session_id` | `str` | `"default"` | Session identifier. |
| `rpt_path` | `str` | `None` | Custom path for the `.rpt` report file. |
| `out_path` | `str` | `None` | Custom path for the `.out` binary output file. |

**Returns:** `ModelSummary` with element counts, flow units, routing model, and time range.

### `lifecycle_run_simulation`

Run the full simulation to completion. This is a background task that reports
progress as a percentage.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |

**Returns:** `SimulationResult` with wall time, step count, and continuity errors.

### `lifecycle_step_simulation`

Advance the simulation by one or more timesteps.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `num_steps` | `int` | `1` | Number of timesteps to advance. |

**Returns:** `StepResult` with current simulation time and completion status.

### `lifecycle_get_simulation_time`

Return the current simulation timing information (start, end, current time,
elapsed fraction, routing timestep).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |

### `lifecycle_get_simulation_state`

Return the current session and solver state including model metadata.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |

### `lifecycle_close_model`

Close and clean up a simulation session.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |

### `lifecycle_list_sessions`

List all active simulation sessions.

---

## Query Tools (`query_*`)

Read-only tools for inspecting model elements and searching across the model.

### `query_get_node_info`

Return properties and state for one or all nodes.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `node_id` | `str` | `None` | Node ID. Omit to get all nodes. |
| `properties` | `list[str]` | `None` | Filter returned fields (reserved for future use). |

**Returns:** `NodeInfo` or `list[NodeInfo]` with ID, type, invert elevation, max depth, and runtime state (depth, head, volume, lateral inflow, overflow).

### `query_get_link_info`

Return properties and state for one or all links.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `link_id` | `str` | `None` | Link ID. Omit to get all links. |

**Returns:** `LinkInfo` or `list[LinkInfo]` with ID, type, from/to nodes, length, roughness, and runtime state (flow, depth, velocity, capacity).

### `query_get_subcatchment_info`

Return properties and state for one or all subcatchments.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `subcatch_id` | `str` | `None` | Subcatchment ID. Omit to get all. |

**Returns:** `SubcatchmentInfo` or `list[SubcatchmentInfo]` with ID, area, imperviousness, slope, width, and runtime state (rainfall, runoff).

### `query_get_gage_info`

Return properties and state for one or all rain gages.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `gage_id` | `str` | `None` | Gage ID. Omit to get all. |

**Returns:** `GageInfo` or `list[GageInfo]` with ID, data source, rain type, and current rainfall.

### `query_get_system_summary`

Return a full system summary including counts, options, and timing.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |

**Returns:** `SystemSummary` with element counts, flow units, routing model, and current simulation time.

### `query_find_elements`

Search for model elements by ID pattern and/or type.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `pattern` | `str` | `None` | Python regex matched against element IDs (case-insensitive). |
| `element_type` | `str` | `None` | Filter by `"node"`, `"link"`, `"subcatchment"`, or `"gage"`. |

**Returns:** `list[ElementSearchResult]` with element type, ID, and index.

---

## Forcing Tools (`forcing_*`)

Tools for applying runtime forcing overrides and manipulating control rules
during a running simulation. All require the session to be in the `"running"`
state.

### `forcing_set_forcing`

Apply a runtime forcing override to a model element.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `target_type` | `str` | *required* | `"node"`, `"link"`, `"subcatchment"`, or `"gage"`. |
| `element_id` | `str` | *required* | Element identifier. |
| `variable` | `str` | *required* | Variable to override (see table below). |
| `value` | `float` | `0.0` | Forcing value. |
| `mode` | `str` | `"replace"` | `"replace"` or `"add"`. |
| `persist` | `bool` | `False` | If `True`, override persists across timesteps. |

**Valid variables by target type:**

| Target Type | Variables |
|---|---|
| `node` | `lateral_inflow`, `head`, `quality` |
| `link` | `flow`, `setting` |
| `subcatchment` | `rainfall`, `evap` |
| `gage` | `rainfall` |

### `forcing_clear_forcing`

Clear forcing overrides. Omit both parameters to clear all; provide both to
clear a specific element.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `target_type` | `str` | `None` | Element category. |
| `element_id` | `str` | `None` | Element identifier. |

### `forcing_set_link_control`

Directly set the control setting on a link (e.g. pump speed, orifice
opening fraction).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `link_id` | `str` | *required* | Link identifier. |
| `setting` | `float` | `0.0` | New control setting value. |

### `forcing_add_control_rule`

Add a new control rule in SWMM rule syntax that takes effect immediately.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `rule_text` | `str` | *required* | SWMM rule syntax string. |

**Example rule text:** `"RULE R1\nIF NODE J1 DEPTH > 5\nTHEN PUMP P1 STATUS = ON"`

### `forcing_set_rainfall_override`

Convenience shortcut to override rainfall on a rain gage with a persistent
replacement value.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `gage_id` | `str` | *required* | Rain gage identifier. |
| `rainfall` | `float` | `0.0` | Rainfall intensity (model units). |

---

## Analysis Tools (`analysis_*`)

Post-simulation analysis tools for statistics, time series, flooding, and
capacity summaries.

### `analysis_get_statistics`

Retrieve post-simulation statistics for a single model element.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `element_type` | `str` | `"node"` | `"node"`, `"link"`, or `"subcatchment"`. |
| `element_id` | `str` | *required* | Element identifier. |

### `analysis_get_mass_balance`

Retrieve mass-balance continuity errors and volumetric totals.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |

**Returns:** `MassBalanceResult` with runoff/routing/quality continuity errors and volume breakdowns.

### `analysis_get_time_series`

Retrieve a time series of output results from the `.out` binary file.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `element_type` | `str` | `"node"` | `"node"`, `"link"`, `"subcatchment"`, or `"system"`. |
| `element_id` | `str` | *required* | Element identifier (ignored for `"system"`). |
| `variable` | `str` | `"depth"` | Output variable (e.g. `"depth"`, `"flow"`, `"runoff"`). |
| `start_period` | `int` | `0` | First reporting period (0-indexed). |
| `end_period` | `int` | `-1` | Last reporting period (`-1` = all). |
| `downsample` | `int` | `1` | Take every N-th value. |

**Returns:** `TimeSeries` with timestamps and values.

### `analysis_get_flooding_summary`

Summarise flooding across all nodes in the model.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `min_flood_volume` | `float` | `0.0` | Minimum volume threshold. |

**Returns:** `list[FloodingSummaryItem]` sorted by total flood volume (descending).

### `analysis_get_capacity_summary`

Summarise hydraulic capacity usage across all links.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `max_filling_threshold` | `float` | `1.0` | Minimum filling ratio threshold. |

**Returns:** `list[CapacitySummaryItem]` sorted by filling ratio (descending).

### `analysis_compare_scenarios`

Compare time-series output between two simulation sessions.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_a` | `str` | *required* | Baseline session identifier. |
| `session_b` | `str` | *required* | Comparison session identifier. |
| `element_type` | `str` | `"node"` | `"node"`, `"link"`, or `"subcatchment"`. |
| `variable` | `str` | `"depth"` | Output variable to compare. |

### `analysis_export_results`

Export node and link time-series results to CSV or JSON.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `output_path` | `str` | *required* | Destination file path. |
| `format` | `str` | `"csv"` | `"csv"` or `"json"`. |

**Returns:** `ExportResult` with status, path, format, and record count.

---

## Building Tools (`building_*`)

Programmatic model construction tools for creating SWMM models from scratch.

### `building_create_model`

Create an empty SWMM model and start a building session.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier for the new model. |

### `building_add_node`

Add a node to the model being built.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `node_id` | `str` | *required* | Unique node identifier. |
| `node_type` | `str` | `"junction"` | `"junction"`, `"outfall"`, `"storage"`, or `"divider"`. |
| `invert_elev` | `float` | `0.0` | Invert elevation. |
| `max_depth` | `float` | `0.0` | Maximum depth above invert. |
| `x`, `y` | `float` | `None` | Optional coordinate position. |

### `building_add_link`

Add a link (conduit, pump, orifice, weir, or outlet) to the model.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `link_id` | `str` | *required* | Unique link identifier. |
| `link_type` | `str` | `"conduit"` | `"conduit"`, `"pump"`, `"orifice"`, `"weir"`, or `"outlet"`. |
| `from_node` | `str` | *required* | Upstream node ID. |
| `to_node` | `str` | *required* | Downstream node ID. |
| `length` | `float` | `100.0` | Conduit length. |
| `roughness` | `float` | `0.013` | Manning's roughness coefficient. |
| `xsect_shape` | `str` | `"circular"` | `"circular"`, `"rect_closed"`, `"rect_open"`, `"trapezoidal"`, `"triangular"`. |
| `xsect_geom1` | `float` | `1.0` | Primary geometry parameter (e.g. diameter). |
| `xsect_geom2`-`4` | `float` | `0.0` | Additional geometry parameters. |

### `building_add_subcatchment`

Add a subcatchment to the model.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `subcatch_id` | `str` | *required* | Unique subcatchment identifier. |
| `area` | `float` | `1.0` | Subcatchment area. |
| `imperv_pct` | `float` | `50.0` | Percent imperviousness (0--100). |
| `slope` | `float` | `0.5` | Average surface slope (percent). |
| `width` | `float` | `100.0` | Characteristic width. |
| `outlet_node` | `str` | `""` | Receiving node ID. |

### `building_add_gage`

Add a rain gage to the model.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `gage_id` | `str` | *required* | Unique gage identifier. |

### `building_set_option`

Set a simulation option on the model being built (e.g. `FLOW_UNITS`,
`ROUTING_MODEL`, `REPORT_STEP`).

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `option` | `str` | *required* | Option name. |
| `value` | `str` | *required* | Option value. |

### `building_add_timeseries`

Add a time series to the model.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `name` | `str` | *required* | Time series name. |
| `times` | `list[float]` | *required* | Time values (hours from simulation start). |
| `values` | `list[float]` | *required* | Corresponding data values. |

### `building_add_curve`

Add a curve to the model.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `name` | `str` | *required* | Curve name. |
| `curve_type` | `str` | `"storage"` | `"storage"`, `"pump"`, `"rating"`, `"diversion"`, `"tidal"`, `"shape"`, `"weir"`, `"control"`. |
| `x_values` | `list[float]` | *required* | X-axis values. |
| `y_values` | `list[float]` | *required* | Y-axis values. |

### `building_validate_model`

Run the engine's built-in validation checks on the model being built.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |

### `building_write_model`

Finalize and write the model to an `.inp` file.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `output_path` | `str` | *required* | File path for the output `.inp`. |

---

## Hot-Start Tools (`hotstart_*`)

Tools for saving and restoring simulation state checkpoints.

### `hotstart_save_hotstart`

Save the current simulation state to a hot-start file.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `path` | `str` | `""` | File path (auto-generated if empty). |

**Returns:** `HotStartResult` with status and file path.

### `hotstart_load_hotstart`

Load a previously saved hot-start file into a session.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `path` | `str` | *required* | Path to the `.hsf` file. |

**Returns:** `HotStartResult` with status and file path.

### `hotstart_clone_session`

Clone an existing session by saving and re-applying its hot-start state.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `source_id` | `str` | *required* | Session to clone. |
| `target_id` | `str` | *required* | New session identifier. |

---

## Spatial and Quality Tools (`spatial_*`)

Tools for coordinates, water quality, treatment, and LID controls.

### `spatial_get_coordinates`

Retrieve the spatial coordinates for a model element.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `element_type` | `str` | `"node"` | `"node"`, `"link"`, or `"subcatchment"`. |
| `element_id` | `str` | *required* | Element identifier. |

**Returns:** `SpatialResult` with x, y coordinates or vertex list.

### `spatial_set_coordinates`

Set the spatial coordinates for a model element.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `element_type` | `str` | `"node"` | `"node"`, `"link"`, or `"subcatchment"`. |
| `element_id` | `str` | *required* | Element identifier. |
| `x` | `float` | `0.0` | X coordinate. |
| `y` | `float` | `0.0` | Y coordinate. |

### `spatial_get_quality`

Retrieve water-quality concentrations for a model element.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `element_type` | `str` | `"node"` | `"node"`, `"link"`, or `"subcatchment"`. |
| `element_id` | `str` | *required* | Element identifier. |
| `pollutant` | `str` | `None` | Specific pollutant name (omit for all). |

### `spatial_set_treatment`

Assign a treatment expression to a node for a given pollutant.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `node_id` | `str` | *required* | Node identifier. |
| `pollutant` | `str` | *required* | Pollutant name. |
| `expression` | `str` | *required* | SWMM treatment expression (e.g. `"R = 0.5 * C"`). |

### `spatial_add_lid`

Add a Low Impact Development control to a subcatchment.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session identifier. |
| `subcatch_id` | `str` | *required* | Subcatchment identifier. |
| `lid_type` | `str` | *required* | LID type (`"BC"`, `"RG"`, `"PP"`, etc.). |
| `area` | `float` | *required* | Surface area of the LID unit. |
