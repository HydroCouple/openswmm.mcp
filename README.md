<p align="center">
  <img src="images/hydrocouplecomposer.png" alt="OpenSWMM MCP" width="120">
</p>

# openswmm-mcp

![PyPI](https://img.shields.io/pypi/v/openswmm-mcp)
![Python](https://img.shields.io/pypi/pyversions/openswmm-mcp)
![CI](https://github.com/HydroCouple/openswmm.mcp/actions/workflows/ci.yml/badge.svg)
![Docs](https://github.com/HydroCouple/openswmm.mcp/actions/workflows/docs.yml/badge.svg)
![License](https://img.shields.io/pypi/l/openswmm-mcp)

## Overview

**openswmm-mcp** is a [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server that wraps the [OpenSWMM](https://github.com/HydroCouple/OpenSWMMCore) stormwater engine (v6.0), built with [FastMCP 3.x](https://gofastmcp.com/). It enables LLM clients -- such as Claude, ChatGPT, or any MCP-compatible agent -- to interact with the full SWMM simulation lifecycle through natural language:

- **Build** SWMM models programmatically from scratch
- **Run** simulations with progress reporting
- **Query** nodes, links, subcatchments, and gages in real time
- **Apply** runtime forcing overrides and control rules for what-if scenarios
- **Analyze** flooding, pipe capacity, mass balance, and time-series output
- **Branch** simulations via hot-start save/load and session cloning
- **Export** results to CSV or JSON for downstream analysis

## Features

The server exposes **358 tools** organized across 20 namespaced sub-servers, along with **9 URI-based resources**, **7 prompt templates**, and full authentication support.

### Tool Namespaces

- **lifecycle\_\***: Open models, run or step simulations, inspect session state, manage event windows and runoff-interface files, manage multiple concurrent sessions
- **query\_\***: Read properties and runtime state for nodes, links, subcatchments, and gages; search elements by regex pattern
- **model\_\***: Project-level metadata — title, options, CRS, unit system, scalar user flags plus user-flag schema / per-object values, plugins, file-section paths and typed external-file path slots
- **building\_\***: Construct models from scratch — add nodes, links, subcatchments, gages, time series, curves; pop (undo) the last added element; set options; validate and write `.inp` files
- **editing\_\***: Delete model objects with cascade analysis, preview impacts non-destructively, and convert nodes or links to different types in place
- **nodes\_\*** / **links\_\*** / **subcatchments\_\***: Fine-grained per-element accessors — bulk state arrays, configuration setters, per-element statistics
- **inflows\_\***: External / dry-weather / RDII inflows, unit hydrographs, exponential IA decay
- **controls\_\***: SWMM control rules — add, list, clear, set link setting / status
- **forcing\_\***: Apply runtime forcing overrides (rainfall, inflows, PET, boundary conditions), read back the climate evaporation rate, set link controls, add control rules
- **pollutants\_\*** / **quality\_\***: Pollutant properties, buildup / washoff / treatment kinetics, landuse and street sweeping
- **tables\_\***: Time series, curves, and patterns with lookup helpers
- **infrastructure\_\***: Transects, streets, inlets, LID controls and usage
- **hotstart\_\***: Save and load simulation state checkpoints; seed state; clone sessions for scenario branching
- **analysis\_\***: Retrieve post-simulation statistics, mass balance, time series, flooding summaries, capacity summaries, scenario comparison, and CSV/JSON export
- **spatial\_\***: Query and set element coordinates, retrieve water-quality concentrations, assign treatment expressions, add LID controls
- **geopackage\_\***: GeoPackage I/O — simulations, result series, observed-data comparison
- **twod\_\***: 2D overland-flow surface — mesh queries, per-triangle state, statistics and mass balance, runtime forcing, solver parameters, edge boundary conditions, edge conveyance

See the [tools guide](docs/user-guide/tools.md) for the per-namespace tool inventory.

### Additional Capabilities

- **9 URI-based resources** (`swmm://sessions`, `swmm://session/{id}/nodes`, etc.) for structured data access
- **7 prompt templates** for guided workflows (model analysis, flooding diagnosis, scenario comparison, design review, what-if setup, model construction, result explanation)
- **Full OAuth/JWT authentication** for HTTP transport via `fastmcp[auth]`
- **Background task support** with progress reporting for long-running simulations
- **Multi-session management** with configurable session limits

## Installation

```bash
pip install openswmm-mcp
```

Authentication support is included by default through the `fastmcp[auth]` dependency. To install with development and documentation extras:

```bash
pip install "openswmm-mcp[dev,docs]"
```

### Requirements

- Python 3.10+
- `openswmm >= 6.0.0a1` (the OpenSWMM engine Python bindings)
- `fastmcp >= 3.0`
- `pydantic >= 2.0`
- `numpy >= 1.21`

## Quick Start

### With Claude Code

Add the following to your MCP configuration (e.g. `~/.claude/settings.json` or `.mcp.json`):

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "python",
      "args": ["-m", "openswmm_mcp"],
      "env": {
        "OPENSWMM_MCP_WORKING_DIR": "/path/to/models"
      }
    }
  }
}
```

### With uv

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "uv",
      "args": ["run", "--with", "openswmm-mcp", "openswmm-mcp"]
    }
  }
}
```

### Example Conversation

```
User:  Open the model at /data/site_drainage.inp and run the simulation.

Claude: [calls lifecycle_open_model] Model opened -- 12 nodes, 11 links, 8 subcatchments, CFS units, DYNWAVE routing.
        [calls lifecycle_run_simulation] Completed in 2.3 s, 1440 steps. Routing continuity error: -0.02%.

User:  Which nodes flooded?

Claude: [calls analysis_get_flooding_summary] 2 nodes flooded:
        - J5: 1,247 cu ft total volume, peak overflow 3.8 CFS, flooded for 45 min
        - J8: 312 cu ft total volume, peak overflow 1.1 CFS, flooded for 12 min

User:  What if we upsize the pipe from J4 to J5 to 24 inches?

Claude: [calls hotstart_clone_session] Cloned "default" to "whatif_upsize".
        [calls building_set_option] Updated conduit C4 diameter to 2.0 ft.
        [calls lifecycle_run_simulation] Completed. Routing error: -0.01%.
        [calls analysis_compare_scenarios] Flooding at J5 reduced by 89%, J8 eliminated.
```

## Available Tools

### lifecycle (Session and Simulation Management)

| Tool | Description |
|------|-------------|
| `lifecycle_open_model` | Open a SWMM `.inp` file and initialize the engine |
| `lifecycle_run_simulation` | Run the full simulation to completion with progress reporting |
| `lifecycle_step_simulation` | Advance by one or more timesteps |
| `lifecycle_get_simulation_time` | Return current simulation timing information |
| `lifecycle_get_simulation_state` | Return session and solver state metadata |
| `lifecycle_close_model` | Close and clean up a simulation session |
| `lifecycle_list_sessions` | List all active simulation sessions |

### query (Model Inspection)

| Tool | Description |
|------|-------------|
| `query_get_node_info` | Properties and runtime state for one or all nodes |
| `query_get_link_info` | Properties and runtime state for one or all links |
| `query_get_subcatchment_info` | Properties and runtime state for one or all subcatchments |
| `query_get_gage_info` | Properties and state for one or all rain gages |
| `query_get_system_summary` | Full system summary including counts, options, and timing |
| `query_find_elements` | Search elements by regex pattern and/or type |

### forcing (Runtime Overrides)

| Tool | Description |
|------|-------------|
| `forcing_set_forcing` | Apply a runtime forcing override to any element variable |
| `forcing_set_persistent_forcing` | Apply an override that persists across timesteps |
| `forcing_clear_forcing` | Clear forcing overrides (single element or all) |
| `forcing_get_climate_evap_rate` | Read back the climate-derived PET rate for caller-side composition |
| `forcing_set_link_control` | Override a link's control setting (pump speed, orifice opening) |
| `forcing_add_control_rule` | Add a new control rule in SWMM rule syntax |
| `forcing_set_rainfall_override` | Convenience shortcut for persistent rainfall override on a gage |

### analysis (Post-Simulation Analysis)

| Tool | Description |
|------|-------------|
| `analysis_get_statistics` | Peak/max values and duration statistics for a single element |
| `analysis_get_mass_balance` | Continuity errors and volumetric totals (runoff, routing, quality) |
| `analysis_get_time_series` | Time-series output from the binary `.out` file with downsampling |
| `analysis_get_flooding_summary` | Flooding across all nodes, sorted by volume |
| `analysis_get_capacity_summary` | Hydraulic capacity utilization across all links |
| `analysis_compare_scenarios` | Compare time-series output between two sessions |
| `analysis_export_results` | Export node and link results to CSV or JSON |

### building (Programmatic Model Construction)

| Tool | Description |
|------|-------------|
| `building_create_model` | Create an empty model and start a building session |
| `building_add_node` | Add a junction, outfall, storage, or divider node |
| `building_pop_last_node` | Remove the most recently added node (undo) |
| `building_add_link` | Add a conduit, pump, orifice, weir, or outlet |
| `building_pop_last_link` | Remove the most recently added link (undo) |
| `building_add_subcatchment` | Add a subcatchment with hydrological parameters |
| `building_add_gage` | Add a rain gage |
| `building_set_option` | Set a simulation option (flow units, routing model, etc.) |
| `building_add_timeseries` | Add a time series (e.g. rainfall hyetograph) |
| `building_add_curve` | Add a curve (storage, pump, rating, diversion, etc.) |
| `building_validate_model` | Run built-in validation checks |
| `building_write_model` | Finalize and write the model to an `.inp` file |

### editing (Object Deletion and Type Conversion)

| Tool | Description |
|------|-------------|
| `editing_analyze_impact` | Preview what would be deleted or nullified without making any changes |
| `editing_delete_object` | Delete a model object and cascade-delete or nullify all references |
| `editing_convert_node` | Convert a node to a different type in place, preserving common properties |
| `editing_convert_link` | Convert a link to a different type in place, preserving endpoints and offsets |

### hotstart (State Management)

| Tool | Description |
|------|-------------|
| `hotstart_save_hotstart` | Save current simulation state to a hot-start file |
| `hotstart_load_hotstart` | Load a hot-start file into a session |
| `hotstart_clone_session` | Clone a session by saving and re-applying hot-start state |

### spatial (Coordinates, Quality, and LID)

| Tool | Description |
|------|-------------|
| `spatial_get_coordinates` | Retrieve spatial coordinates for a model element |
| `spatial_set_coordinates` | Set spatial coordinates for a model element |
| `spatial_get_quality` | Retrieve water-quality concentrations |
| `spatial_set_treatment` | Assign a treatment expression to a node |
| `spatial_add_lid` | Add a Low Impact Development control to a subcatchment |

### twod (2D Overland-Flow Surface)

| Tool | Description |
|------|-------------|
| `twod_get_mesh_summary` | Mesh activity flag, vertex/triangle counts, boundary edges, 1D couplings |
| `twod_get_mesh_geometry` | Windowed triangle geometry: vertices, area, centroid, Manning's n, neighbours |
| `twod_get_state` / `twod_get_state_bulk` | Per-triangle depth/head/rainfall/coupling state, single or summarised |
| `twod_get_stats` | Per-triangle max depth/velocity/continuity-residual hot spots |
| `twod_get_mass_balance` | Global 2D mass-balance terms and continuity error |
| `twod_force_rainfall` / `twod_force_coupling_flux` / `twod_force_clear` | Runtime 2D forcing overrides |
| `twod_get_edge_bc` / `twod_set_edge_bc` | Edge boundary conditions (wall, normal flow, stage, flow, rating curve) |
| `twod_get_edge_conveyance` / `twod_set_edge_conveyance` / `twod_reset_edge_conveyance` | Per-edge conveyance factors (berms / barriers) |

The full twod inventory (28 tools, including `twod_get_coupling_map`, `twod_get_totals`, `twod_set_vertex_z`, and `twod_get_solver_params` / `twod_set_solver_params`) is in the [tools guide](docs/user-guide/tools.md).

## Available Resources

| URI Pattern | Description |
|-------------|-------------|
| `swmm://sessions` | JSON array of all active sessions |
| `swmm://session/{id}/summary` | Model summary (counts, flow units, routing, time range) |
| `swmm://session/{id}/nodes` | All node IDs with types |
| `swmm://session/{id}/nodes/{node_id}` | Full properties and state for a single node |
| `swmm://session/{id}/links` | All link IDs with types |
| `swmm://session/{id}/links/{link_id}` | Full properties and state for a single link |
| `swmm://session/{id}/subcatchments` | All subcatchment IDs |
| `swmm://session/{id}/mass_balance` | Continuity errors and volumetric totals |
| `swmm://session/{id}/options` | Simulation options (flow units, routing method, time-step) |

## Available Prompts

| Prompt | Description | Parameters |
|--------|-------------|------------|
| `analyze_model` | Comprehensive model review and assessment | `inp_path` |
| `diagnose_flooding` | Investigate flooding causes and suggest mitigations | `session_id`, `node_ids` (optional, comma-separated) |
| `compare_scenarios` | Side-by-side comparison of two simulation sessions | `session_a`, `session_b` |
| `design_review` | Check model against design standards | `session_id`, `standard` (optional) |
| `what_if` | Set up and evaluate a what-if scenario | `session_id`, `description` |
| `build_simple_model` | Guided construction of a model from a text description | `description` |
| `explain_results` | Plain-language result explanation for stakeholders | `session_id` |

## Configuration

All settings are controlled via environment variables with the `OPENSWMM_MCP_` prefix:

| Environment Variable | Default | Description |
|----------------------|---------|-------------|
| `OPENSWMM_MCP_WORKING_DIR` | `"./"` | Base directory for model files and session data |
| `OPENSWMM_MCP_MAX_SESSIONS` | `5` | Maximum number of concurrent simulation sessions |
| `OPENSWMM_MCP_TRANSPORT` | `"stdio"` | Transport protocol: `stdio`, `http`, or `sse` |
| `OPENSWMM_MCP_HTTP_PORT` | `8080` | Port for HTTP/SSE transport |
| `OPENSWMM_MCP_LOG_LEVEL` | `"INFO"` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `OPENSWMM_MCP_OAUTH_ISSUER` | `None` | OAuth issuer URL for JWT validation |
| `OPENSWMM_MCP_OAUTH_AUDIENCE` | `None` | Expected OAuth audience claim |
| `OPENSWMM_MCP_JWT_JWKS_URL` | `None` | JWKS endpoint URL for JWT signature verification |

## HTTP Transport with Authentication

To run the server over HTTP with OAuth/JWT authentication:

```bash
OPENSWMM_MCP_TRANSPORT=http \
OPENSWMM_MCP_HTTP_PORT=8000 \
OPENSWMM_MCP_OAUTH_ISSUER=https://auth.example.com \
OPENSWMM_MCP_OAUTH_AUDIENCE=openswmm-mcp \
OPENSWMM_MCP_JWT_JWKS_URL=https://auth.example.com/.well-known/jwks.json \
python -m openswmm_mcp
```

For unauthenticated HTTP (development only):

```bash
OPENSWMM_MCP_TRANSPORT=http OPENSWMM_MCP_HTTP_PORT=8000 python -m openswmm_mcp
```

## Development

### Setup

```bash
git clone https://github.com/HydroCouple/openswmm.mcp.git
cd openswmm.mcp
pip install -e ".[dev,docs]"
```

### Running Tests

```bash
pytest tests/unit/ -v
pytest tests/unit/ --cov=openswmm_mcp         # with coverage
OPENSWMM_RUN_INTEGRATION=1 pytest tests/integration/ -v  # integration tests (requires engine)
```

### Linting

```bash
ruff check src/ tests/
ruff format src/ tests/
```

### Building Docs

```bash
sphinx-build -b html docs docs/_build/html
```

The documentation site uses [PyData Sphinx Theme](https://pydata-sphinx-theme.readthedocs.io/) with [MyST Parser](https://myst-parser.readthedocs.io/) for Markdown support.

## Architecture

```
                          MCP Client (Claude, etc.)
                                    |
                              MCP Protocol
                                    |
                        +-----------+-----------+
                        |  FastMCP Root Server  |
                        |  (openswmm-mcp)       |
                        +-----------+-----------+
                                    |
          +-------+-------+--------+--------+---------+-------+--------+--------+
          |       |       |        |        |         |       |        |        |
     lifecycle  query  forcing  analysis  building editing hotstart spatial
       (mcp)   (mcp)   (mcp)    (mcp)     (mcp)    (mcp)   (mcp)    (mcp)
          |       |       |        |        |         |       |        |        |
          +-------+-------+--------+--------+---------+-------+--------+--------+
                                    |
                           SessionManager
                          /      |       \
                     Session  Session  Session ...
                        |
               +--------+--------+
               |        |        |
            Solver   Nodes    Links   ...
               |
         openswmm.engine (C++ bindings)
```

Each tool namespace is a separate `FastMCP` sub-server, mounted on the root server with a namespace prefix. The `SessionManager` manages concurrent simulation sessions, each wrapping an `openswmm.engine.Solver` instance and its associated domain accessors (nodes, links, subcatchments, gages, pollutants, mass balance, statistics, forcing, controls, spatial, quality, hot-start, and infrastructure).

Resources and prompts are mounted without a namespace prefix to keep their URIs concise.

## Contributing

Contributions are welcome! Please read our [Contributing Guide](CONTRIBUTING.md) for details on the development workflow, code style, and how to submit pull requests.

This project follows the [Contributor Covenant 3.0 Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold this code.

In brief:

1. Fork the repository and create a feature branch.
2. Install development dependencies: `pip install -e ".[dev,docs]"`
3. Make your changes and add or update tests.
4. Ensure `ruff check` and `ruff format` pass with no issues.
5. Ensure `pytest tests/unit/` passes.
6. Open a pull request against `main` with a clear description of your changes.

## Authors

See [AUTHORS.md](AUTHORS.md) for the full list of contributors to this project.

## License

Apache License, Version 2.0 -- see [LICENSE](LICENSE) for the full text and [NOTICE](NOTICE) for required attribution.

## Acknowledgements

- [OpenSWMM Engine](https://github.com/HydroCouple/OpenSWMMCore) -- the next-generation SWMM computational engine
- [FastMCP](https://gofastmcp.com/) -- the Python framework for building MCP servers
- [US EPA SWMM](https://www.epa.gov/water-research/storm-water-management-model-swmm) -- the original Storm Water Management Model on which OpenSWMM is based
