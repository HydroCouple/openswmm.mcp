# Examples

This page demonstrates end-to-end workflows using the OpenSWMM MCP Server
tools, resources, and prompts.

## Example 1: Basic Simulation Workflow

A minimal open-run-query-close workflow.

### 1. Open the model

```
Tool: lifecycle_open_model
Arguments:
  inp_path: "/models/site_drainage.inp"
  session_id: "basic"
```

Response confirms 12 nodes, 11 links, 6 subcatchments, dynamic wave routing.

### 2. Run the simulation

```
Tool: lifecycle_run_simulation
Arguments:
  session_id: "basic"
```

Progress reports stream from 0 % to 100 %. Final response shows 2880 steps
completed, runoff continuity error of -0.012 %, routing continuity error
of -0.008 %.

### 3. Check for flooding

```
Tool: analysis_get_flooding_summary
Arguments:
  session_id: "basic"
```

Returns a sorted list of flooded nodes with peak overflow rates and total
flood volumes.

### 4. Extract a time series

```
Tool: analysis_get_time_series
Arguments:
  session_id: "basic"
  element_type: "node"
  element_id: "J3"
  variable: "depth"
```

Returns a `TimeSeries` object with timestamps and depth values for node J3
across all reporting periods.

### 5. Export results

```
Tool: analysis_export_results
Arguments:
  session_id: "basic"
  output_path: "/models/results.csv"
  format: "csv"
```

### 6. Close the session

```
Tool: lifecycle_close_model
Arguments:
  session_id: "basic"
```

---

## Example 2: What-If Scenario Analysis

Compare a baseline simulation against an alternative with increased rainfall.

### 1. Run the baseline

```
Tool: lifecycle_open_model
Arguments:
  inp_path: "/models/site_drainage.inp"
  session_id: "baseline"
```

```
Tool: lifecycle_run_simulation
Arguments:
  session_id: "baseline"
```

### 2. Clone and modify

```
Tool: hotstart_clone_session
Arguments:
  source_id: "baseline"
  target_id: "high_rain"
```

```
Tool: forcing_set_rainfall_override
Arguments:
  session_id: "high_rain"
  gage_id: "RG1"
  rainfall: 2.5
```

### 3. Run the alternative

```
Tool: lifecycle_run_simulation
Arguments:
  session_id: "high_rain"
```

### 4. Compare results

```
Tool: analysis_compare_scenarios
Arguments:
  session_a: "baseline"
  session_b: "high_rain"
  element_type: "node"
  variable: "depth"
```

Returns per-element peak differences and summary statistics (mean, max, min
of absolute differences).

### 5. Review flooding differences

```
Tool: analysis_get_flooding_summary
Arguments:
  session_id: "baseline"
```

```
Tool: analysis_get_flooding_summary
Arguments:
  session_id: "high_rain"
```

Compare the two summaries to identify nodes with increased flooding.

### 6. Clean up

```
Tool: lifecycle_close_model
Arguments:
  session_id: "baseline"
```

```
Tool: lifecycle_close_model
Arguments:
  session_id: "high_rain"
```

---

## Example 3: Programmatic Model Building

Create a simple drainage network entirely through the building tools.

### 1. Create an empty model

```
Tool: building_create_model
Arguments:
  session_id: "new_model"
```

### 2. Set simulation options

```
Tool: building_set_option
Arguments:
  session_id: "new_model"
  option: "FLOW_UNITS"
  value: "CFS"
```

```
Tool: building_set_option
Arguments:
  session_id: "new_model"
  option: "ROUTING_MODEL"
  value: "DYNWAVE"
```

### 3. Add nodes

```
Tool: building_add_node
Arguments:
  session_id: "new_model"
  node_id: "J1"
  node_type: "junction"
  invert_elev: 100.0
  max_depth: 6.0
  x: 0.0
  y: 100.0
```

```
Tool: building_add_node
Arguments:
  session_id: "new_model"
  node_id: "J2"
  node_type: "junction"
  invert_elev: 95.0
  max_depth: 6.0
  x: 200.0
  y: 100.0
```

```
Tool: building_add_node
Arguments:
  session_id: "new_model"
  node_id: "OUT1"
  node_type: "outfall"
  invert_elev: 90.0
  x: 400.0
  y: 100.0
```

### 4. Add links

```
Tool: building_add_link
Arguments:
  session_id: "new_model"
  link_id: "C1"
  link_type: "conduit"
  from_node: "J1"
  to_node: "J2"
  length: 200.0
  roughness: 0.013
  xsect_shape: "circular"
  xsect_geom1: 2.0
```

```
Tool: building_add_link
Arguments:
  session_id: "new_model"
  link_id: "C2"
  link_type: "conduit"
  from_node: "J2"
  to_node: "OUT1"
  length: 200.0
  roughness: 0.013
  xsect_shape: "circular"
  xsect_geom1: 2.5
```

### 5. Add a subcatchment

```
Tool: building_add_subcatchment
Arguments:
  session_id: "new_model"
  subcatch_id: "S1"
  area: 10.0
  imperv_pct: 50.0
  slope: 0.5
  width: 500.0
  outlet_node: "J1"
```

### 6. Add a rain gage and time series

```
Tool: building_add_gage
Arguments:
  session_id: "new_model"
  gage_id: "RG1"
```

```
Tool: building_add_timeseries
Arguments:
  session_id: "new_model"
  name: "design_storm"
  times: [0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]
  values: [0.1, 0.5, 1.2, 2.0, 1.2, 0.5, 0.1]
```

### 7. Validate and write

```
Tool: building_validate_model
Arguments:
  session_id: "new_model"
```

```
Tool: building_write_model
Arguments:
  session_id: "new_model"
  output_path: "/models/new_drainage.inp"
```

### 8. Open the new model and test

```
Tool: lifecycle_open_model
Arguments:
  inp_path: "/models/new_drainage.inp"
  session_id: "test_run"
```

```
Tool: lifecycle_run_simulation
Arguments:
  session_id: "test_run"
```

Verify that the simulation completes with acceptable continuity errors.
