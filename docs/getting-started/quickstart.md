# Quick Start

This guide walks through a minimal workflow: opening a SWMM model, running a
simulation, and querying the results. All interactions happen through MCP
tools.

## Prerequisites

- The OpenSWMM MCP Server is installed and configured (see
  {doc}`installation` and {doc}`configuration`).
- You have a SWMM `.inp` model file available. The examples below use
  `site_drainage.inp`.

## Step 1: Open a Model

Use the `lifecycle_open_model` tool to load a SWMM input file:

```
Tool: lifecycle_open_model
Arguments:
  inp_path: "/path/to/site_drainage.inp"
  session_id: "demo"
```

The server parses the `.inp` file, initialises the engine, and returns a
`ModelSummary` with element counts, flow units, and simulation time range:

```json
{
  "session_id": "demo",
  "state": "initialized",
  "node_count": 12,
  "link_count": 11,
  "subcatchment_count": 6,
  "gage_count": 1,
  "pollutant_count": 0,
  "flow_units": "CFS",
  "route_model": "DYNWAVE",
  "start_time": 0.0,
  "end_time": 1.0,
  "routing_step": 30.0
}
```

## Step 2: Run the Simulation

Use the `lifecycle_run_simulation` tool. This is a background task that
reports progress as a percentage:

```
Tool: lifecycle_run_simulation
Arguments:
  session_id: "demo"
```

When complete, the tool returns continuity errors and timing:

```json
{
  "session_id": "demo",
  "elapsed_wall_time": 0.342,
  "steps_completed": 2880,
  "runoff_continuity_error": -0.012,
  "routing_continuity_error": -0.008,
  "quality_continuity_error": null
}
```

## Step 3: Query Results

### Inspect a Node

```
Tool: query_get_node_info
Arguments:
  session_id: "demo"
  node_id: "J1"
```

Returns the node's properties and final simulation state (depth, head, volume,
lateral inflow, overflow).

### Get Flooding Summary

```
Tool: analysis_get_flooding_summary
Arguments:
  session_id: "demo"
```

Returns a list of nodes that experienced flooding, sorted by total flood
volume.

### Retrieve a Time Series

```
Tool: analysis_get_time_series
Arguments:
  session_id: "demo"
  element_type: "node"
  element_id: "J1"
  variable: "depth"
```

Returns the full depth hydrograph for node J1 across all reporting periods.

## Step 4: Close the Session

When you are done, close the session to release engine resources:

```
Tool: lifecycle_close_model
Arguments:
  session_id: "demo"
```

## Next Steps

- Explore all available tools in the {doc}`../user-guide/tools` reference.
- Browse model data through `swmm://` resources described in
  {doc}`../user-guide/resources`.
- Use guided prompts for common workflows in {doc}`../user-guide/prompts`.
- Try a what-if scenario or build a model from scratch with the examples in
  {doc}`../user-guide/examples`.
