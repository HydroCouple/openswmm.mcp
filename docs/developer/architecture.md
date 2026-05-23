# Architecture

This document describes the internal architecture of the OpenSWMM MCP Server.

## Overview

The server is built on [FastMCP 3.x](https://github.com/jlowin/fastmcp) and
follows a composition-based architecture where the root server mounts
domain-specific sub-servers, each providing tools scoped to a particular
concern.

```
Root FastMCP ("OpenSWMM MCP Server")
  |
  |-- lifespan  -->  ServerSettings + SessionManager
  |
  |   # Lifecycle, query, and forcing
  |-- mount(lifecycle_mcp,       namespace="lifecycle")
  |-- mount(query_mcp,           namespace="query")
  |-- mount(forcing_mcp,         namespace="forcing")
  |
  |   # Analysis + post-processing
  |-- mount(analysis_mcp,        namespace="analysis")
  |
  |   # Model construction + editing
  |-- mount(building_mcp,        namespace="building")
  |-- mount(editing_mcp,         namespace="editing")
  |-- mount(model_mcp,           namespace="model")
  |
  |   # Fine-grained element accessors
  |-- mount(nodes_mcp,           namespace="nodes")
  |-- mount(links_mcp,           namespace="links")
  |-- mount(subcatchments_mcp,   namespace="subcatchments")
  |
  |   # Forcing / control inputs
  |-- mount(inflows_mcp,         namespace="inflows")
  |-- mount(controls_mcp,        namespace="controls")
  |
  |   # Water quality + hydrology configuration
  |-- mount(pollutants_mcp,      namespace="pollutants")
  |-- mount(quality_mcp,         namespace="quality")
  |-- mount(tables_mcp,          namespace="tables")
  |-- mount(infrastructure_mcp,  namespace="infrastructure")
  |
  |   # State / IO
  |-- mount(hotstart_mcp,        namespace="hotstart")
  |-- mount(spatial_quality_mcp, namespace="spatial")
  |-- mount(geopackage_mcp,      namespace="geopackage")
  |
  |-- mount(resources_mcp)       # no namespace
  |-- mount(prompts_mcp)         # no namespace
```

Nineteen tool sub-servers are mounted in total — each declared in its
own `openswmm_mcp/tools/<name>.py` module as a `FastMCP("<name>")`
instance.  The full list (and mount order) is in
`openswmm_mcp/server.py`.

## Server Composition via `mount()`

Each tool phase is defined as its own `FastMCP` instance in a separate module
under `openswmm_mcp/tools/`. The root server in `server.py` mounts these
sub-servers with **namespace** prefixes, so tool names are automatically
scoped:

- `lifecycle_mcp` tools become `lifecycle_open_model`, `lifecycle_run_simulation`, etc.
- `query_mcp` tools become `query_get_node_info`, `query_get_link_info`, etc.

Resources and prompts are mounted **without** namespaces so their URIs remain
short (e.g. `swmm://sessions` instead of `resources_swmm://sessions`).

## Lifespan and Session Lifecycle

### Server Lifespan

The `server_lifespan` async context manager (in `dependencies.py`) runs once
when the server starts and:

1. Creates a `ServerSettings` instance from environment variables.
2. Creates a `SessionManager` with the configured working directory and max
   session count.
3. Yields a context dict containing both objects.
4. On shutdown, calls `SessionManager.cleanup_all()` to close all sessions.

All mounted sub-servers inherit this lifespan context through the `mount()`
mechanism, so every tool can access the `SessionManager` via
`ctx.lifespan_context["session_manager"]`.

### Session States

A `SimSession` progresses through these states:

```
created --> opened --> initialized --> running --> ended --> closed
                                                    |
building ----> (finalized via write_model) ---------+
```

- **created**: Session object exists but solver not yet opened.
- **opened**: `.inp` file parsed.
- **initialized**: Engine memory allocated and ready to simulate.
- **running**: Simulation in progress (stepping).
- **ended**: Simulation complete, results available.
- **closed**: All resources released.
- **building**: Model being constructed programmatically via `ModelBuilder`.

### SimSession

The `SimSession` dataclass holds:

- `backend`: A `Backend` subclass (`OpenSWMMBackend` or
  `LegacyBackend`) that wraps either the refactored
  `openswmm.engine.Solver` or the SWMM 5 solver.  This is the engine-
  abstraction seam — see *Backends* below.
- `state`: Current lifecycle state string.
- `working_dir`: Path for session-specific files.
- `inp_path` / `rpt_path` / `out_path`: Resolved file paths.
- `model_builder`: A `ModelBuilder` instance while the session is in
  the `building` state (before a `.inp` is parsed).

`SimSession.__getattr__` delegates unknown attributes (`nodes`,
`links`, `subcatchments`, `gages`, `forcing`, `mass_balance`,
`pollutants`, `statistics`, `spatial`, `tables`, `controls`,
`inflows`, `infrastructure`, `quality`, `hotstart`, `model`,
`editor`, …) to the active backend, so tools call
`session.nodes.get_depth(...)` regardless of which engine is in use.
The backend itself owns the domain accessor objects and caches them
on first access.

### Backends

The `backends/` subpackage holds the engine-abstraction layer:

- `backends.base.Backend` — abstract base class declaring the
  domain-accessor surface (`nodes`, `links`, …), lifecycle
  delegation (`open`, `initialize`, `start`, `step`, `end`,
  `report`, `close`), and `engine_kind`.
- `backends.openswmm.OpenSWMMBackend` — wraps
  `openswmm.engine.Solver` and exposes the v6.0 domain classes.
- `backends.legacy.LegacyBackend` — wraps the SWMM 5 solver via
  `openswmm.legacy.engine`.

When a tool calls `lifecycle_open_model(engine="openswmm")` the
matching `Backend` subclass is instantiated and attached to the
session; switching to `engine="legacy"` swaps in `LegacyBackend`
without any tool needing to know which solver is underneath.

## Threading Model

All `openswmm.engine` Cython bindings are synchronous. The server wraps every
engine call in `asyncio.to_thread()` to avoid blocking the async event loop.
This ensures that long-running operations (e.g. `solver.step()` during
`run_simulation`) do not prevent other MCP requests from being processed.

## Background Tasks

The `run_simulation` tool is decorated with `@mcp.tool(task=True)`, which
means:

1. The client receives a task ID immediately.
2. The server runs the simulation loop in the background.
3. Progress is reported via `ctx.report_progress(pct, 100)` at each timestep.
4. The client can poll for the task result.

This is essential for large models where simulation can take minutes.

## Error Handling

All tool errors are raised as `ToolError` exceptions with structured error
codes defined in `errors.py`:

- `SESSION_NOT_FOUND`
- `INVALID_STATE`
- `ELEMENT_NOT_FOUND`
- `ENGINE_ERROR`
- `VALIDATION_ERROR`
- `MAX_SESSIONS_REACHED`

The `require_state()` helper validates that a session is in an acceptable
state before proceeding, raising `ToolError` with `INVALID_STATE` otherwise.

## Authentication

Authentication is only active when the transport is HTTP or SSE. The `auth.py`
module provides:

- **OAuth**: For interactive users authenticating through browser-based flows.
- **JWT**: For service-to-service authentication using signed tokens.

Both are composed via `MultiAuth`. When running in stdio mode (the default for
Claude Code), authentication is bypassed entirely.

## Module Map

| Module | Purpose |
|---|---|
| `__init__.py` | Package version |
| `__main__.py` | CLI entry point (`python -m openswmm_mcp`) |
| `server.py` | Root FastMCP instance, mounts sub-servers |
| `config.py` | `ServerSettings` via pydantic-settings |
| `session.py` | `SimSession` and `SessionManager` |
| `models.py` | Pydantic response models |
| `errors.py` | Error codes and `ToolError` helpers |
| `dependencies.py` | Lifespan context manager and dependency functions |
| `auth.py` | OAuth/JWT authentication providers |
| `tools/*.py` | Nineteen tool sub-servers (one FastMCP per namespace) |
| `backends/base.py` | `Backend` abstract base — engine-agnostic interface |
| `backends/openswmm.py` | OpenSWMM v6.0 backend (refactored engine) |
| `backends/legacy.py` | Legacy SWMM 5 backend |
| `resources/model.py` | `swmm://` URI resource handlers |
| `prompts/workflows.py` | Seven guided-workflow prompt templates |
| `_util/formatting.py` | numpy-to-list, time formatting utilities |
| `_util/validation.py` | Path resolution, element validation helpers |
