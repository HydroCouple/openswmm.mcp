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
  |-- mount("lifecycle", lifecycle_mcp, namespace="lifecycle")
  |-- mount("query",     query_mcp,     namespace="query")
  |-- mount("forcing",   forcing_mcp,   namespace="forcing")
  |-- mount("analysis",  analysis_mcp,  namespace="analysis")
  |-- mount("building",  building_mcp,  namespace="building")
  |-- mount("hotstart",  hotstart_mcp,  namespace="hotstart")
  |-- mount("spatial",   spatial_quality_mcp, namespace="spatial")
  |
  |-- mount("resources", resources_mcp)   # no namespace
  |-- mount("prompts",   prompts_mcp)     # no namespace
```

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

- `solver`: The `openswmm.engine.Solver` instance.
- `state`: Current lifecycle state string.
- `working_dir`: Path for session-specific files.
- Lazy properties for all 16 domain accessor objects (`Nodes`, `Links`,
  `Subcatchments`, `Gages`, `Forcing`, `MassBalance`, `Pollutants`,
  `Statistics`, `Spatial`, `Tables`, `Controls`, `Inflows`, `Infrastructure`,
  `Quality`, `HotStart`, `OutputReader`).

Domain accessors are created on first access and cached for the lifetime of
the session.

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
| `tools/*.py` | Seven tool sub-servers |
| `resources/model.py` | `swmm://` URI resource handlers |
| `prompts/workflows.py` | Seven guided-workflow prompt templates |
| `_util/formatting.py` | numpy-to-list, time formatting utilities |
| `_util/validation.py` | Path resolution, element validation helpers |
