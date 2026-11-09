# Architecture

The server is a single [FastMCP 3.x](https://gofastmcp.com/) instance with a
small, fixed tool set. It does not wrap engine functions one by one. Instead,
the generic tools read the engine's machine-readable catalog
(`openswmm.engine.catalog`, generated from the Python stubs, Cython sources and
C headers and checked in CI) and dispatch to the live Python objects it
describes.

```
openswmm.engine (C API -> Cython bindings -> catalog.json)
        |
        v
openswmm_mcp.catalog      addressing, value coercion, JSON serialisation
        |
        +-- tools/access.py    describe / find / get / set / call
        +-- tools/model.py     open_model / run / session / save / edit
        +-- tools/results.py   timeseries / report / compare / export
        +-- tools/gym.py       gym_* (optional tool set)
        +-- tools/code.py      run_python (opt-in, stdio only)
```

## Composition

`server.build_server(settings)` creates the FastMCP instance, registers the
core tools, adds the gym tools when `OPENSWMM_MCP_TOOLSETS` includes `gym`,
adds `run_python` when `OPENSWMM_MCP_ENABLE_PYTHON=true` on stdio, and mounts
the resource, prompt and skill sub-servers (no namespaces, so names stay
short). `server.mcp` is the instance built from the environment at import.

## Sessions

`SessionManager` (in `session.py`) is an async-safe registry of `SimSession`
objects, capped by `OPENSWMM_MCP_MAX_SESSIONS`. Each session owns one
`openswmm.engine.Solver`, its file paths (report and output files live in
`<working_dir>/<session_id>/`), cached output readers and an `asyncio.Lock`.
Every engine call goes through `SimSession.call`, which takes the lock, runs
the call in a worker thread and translates engine exceptions into
`ToolError`s that keep their typed names (`LifecycleError`, `BadParamError`,
...).

The session state is the engine's own `EngineState` (`OPENED`, `INITIALIZED`,
`RUNNING`, `ENDED`, ...), read live rather than tracked separately.
Structural edits (`edit` add/delete/convert, editor methods through `call`)
set `structure_edited`; the next `run` from `OPENED` writes the model to
`<name>_edited.inp` and reopens it so the engine rebuilds derived data.

## Catalog dispatch

- `describe` renders catalog targets and members, resolving unit kinds
  (`length`, `flow`, ...) to the session's units.
- `get`/`set` resolve `kind` plus a field path (`stats.max_depth`) to a
  catalog target and property, obtain the live object with
  `openswmm.engine.catalog.resolve`, and read or write it. Whole-collection
  reads use the engine's bulk arrays when the catalog links one.
- `call` accepts only catalogued methods (and the synthetic `items`,
  `get_item`, `set_item`, `delete_item` for mapping-like targets). Lifecycle
  and callback members of the solver are refused. Arguments are coerced from
  JSON by their catalog types: enum names, datetimes, tuples, arrays, and
  file paths resolved against the working directory.
- Errors for unknown names include close matches from the catalog.

## Tests

`tests/unit/test_catalog_contract.py` reads every catalogued field of every
element kind and service on real models and writes every writable scalar back,
so a catalog entry the server cannot serve fails CI.
`tests/unit/test_server_budget.py` caps the tool count and the size of the
tool definitions. Tests import the compiled engine unconditionally: a missing
engine fails the suite instead of skipping it.

## Authentication

`auth.create_auth(settings)` builds JWT / OAuth verifiers for the HTTP
transports from `OPENSWMM_MCP_JWT_JWKS_URL` and `OPENSWMM_MCP_OAUTH_ISSUER`.
The stdio transport runs without authentication.
