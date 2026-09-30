# Changelog

All notable changes to **openswmm.mcp** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

A clean break from the v1 tool surface. v1 registered 663 tools (about 143k
tokens of definitions), more than MCP clients such as Claude Desktop load.
v2 registers 14 core tools (about 12k characters) that reach every property
and method of the engine's Python API through the engine catalog.
`docs/user-guide/tools.md` maps every v1 namespace to its v2 tool.

### Removed

- **All 663 v1 tools** (`lifecycle_*`, `query_*`, `nodes_*`, `links_*`,
  `forcing_*`, `twod_*`, `hotstart_*`, `analysis_*`, `gym_*`, ...). No aliases
  are kept.
- **The SWMM 5 (legacy) backend.** Every session runs the handle-based
  OpenSWMM 6 engine. `save(profile="SWMM5")` still writes a SWMM 5 compatible
  `.inp`.
- The `engine` optional extra: `openswmm>=6.0.0a4` is now a required
  dependency, and the server needs an engine build that ships
  `openswmm.engine.catalog`.

### Added

- **14 core tools**: `open_model`, `run`, `session`, `save`, `describe`,
  `find`, `get`, `set`, `call`, `edit`, `timeseries`, `report`, `compare`,
  `export`. `describe`, `find`, `get`, `set` and `call` are driven by the
  engine catalog, so new engine capabilities need no new tool code.
- **5 gym tools** (`gym_describe`, `gym_config`, `gym_env`, `gym_job`,
  `gym_score`), registered with `OPENSWMM_MCP_TOOLSETS=core,gym`. Environment
  specs can observe any numeric engine field (`observations.fields`) and
  actuate any writable one (`field_setpoint`).
- **`run_python`** (opt-in, stdio only, `OPENSWMM_MCP_ENABLE_PYTHON=true`):
  Python with the session's Solver in scope.
- Resources `swmm://catalog`, `swmm://catalog/{target}`, `swmm://sessions` and
  `swmm://session/{id}/summary`; the workflow prompts and skills are rewritten
  for the new tools.
- Structural edits (`edit`, editor methods through `call`) are rebuilt on the
  next `run`: the model is written to `<name>_edited.inp`, test-opened and
  reopened, so a model the engine refuses leaves the session unchanged.
- `session(action="clone")` continues from the source's time and hydraulic
  state; `session(action="reset")` keeps earlier edits.
- CI tests every catalogued field and service against real models
  (`test_catalog_contract.py`) and caps the tool count and definition size
  (`test_server_budget.py`).

### Fixed

- `python -m openswmm_mcp` (and the `openswmm.mcp` script) now honour
  `OPENSWMM_MCP_TRANSPORT` and `OPENSWMM_MCP_HTTP_PORT`, and the HTTP/SSE
  transports apply the configured JWT/OAuth verifier (`create_auth` was never
  wired into the server).
