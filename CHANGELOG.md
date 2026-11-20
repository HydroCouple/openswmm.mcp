# Changelog

All notable changes to **openswmm.mcp** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

A clean break from the v1 tool surface. v1 registered 663 tools (about 143k
tokens of definitions), more than MCP clients such as Claude Desktop load.
v2 registers 14 core tools (about 12k characters) that reach every property
and method of the engine's Python API through the engine catalog.
`docs/user-guide/tools.md` maps every v1 namespace to its v2 tool. The package
version is now `0.2.0.dev0`.

### Removed

- **All 663 v1 tools** (`lifecycle_*`, `query_*`, `nodes_*`, `links_*`,
  `forcing_*`, `twod_*`, `hotstart_*`, `analysis_*`, `gym_*`, ...). No aliases
  are kept.
- **The SWMM 5 (legacy) backend.** Every session runs the handle-based
  OpenSWMM 6 engine. `save(profile="SWMM5")` still writes a SWMM 5 compatible
  `.inp`.
- The `engine` optional extra: `openswmm>=6.0.0a4.dev2` is now a required
  dependency, and the server needs an engine catalog of revision 2 or later
  (`openswmm.engine.catalog`).
- `openswmm_mcp.gym_support.config`, `.registry` and `.envs`: the environment
  spec layer moved to `openswmm_gymnasium.spec`, and the `gym` extra now
  installs `openswmm.gymnasium[spec]`.

### Added

- **`platform_model(action="derive")`**: a platform study's evaluation becomes a revision
  that names its base revision, study, evaluation and design. `gym_score(action="apply_design")`
  now writes every design kind through the job's own factories with the values the evaluation
  used (a `link_diameter` rescales the whole section, as it was scored, instead of setting
  `geom1`).
- **Local runs can leave platform-format manifests** (opt-in, `OPENSWMM_MCP_LOCAL_MANIFESTS=true`,
  with the `platform` extra): a `run` that starts from an opened model writes `<session>/local/<run id>/` with the model as it ran
  (edits included) packaged with its revision manifest, and an `attempt/` folder
  (`attempt.json`, `run.json`, `summary.json`, `model.rpt`, `model.out`) whose actor names
  the journal and the call that started the run. Without the platform toolset the journal
  names itself as the session and the operating-system login as the person. The water
  ontology loads both like a platform run.
- **`platform_finding`** (extra `ontology`): records an answer as a water-ontology
  `AgentFinding` over rows built from the cited runs' manifests and this session's journal,
  and returns the verifier's verdict for every number it cites.
- **Tool-call journal**: one JSON line per tool call under `<working_dir>/journal/`, with the
  call's effect, argument and result checksums, times and tool call id; `platform_run` sends
  that id to the platform. **Resource** `swmm://ontology/context`: the water ontology's
  agent context, vendored.
- **Platform toolset** (`OPENSWMM_MCP_TOOLSETS=core,platform`, extra `platform`): seven
  tools over `hydrocouple_platform.client.PlatformClient` that list targets and workflows,
  package and upload a model as a revision, estimate, submit, follow and read runs.
  `platform_results` returns provenance first and reads the run summary by checksum,
  without downloading the report or the binary output. The server signs in with an agent
  session credential and refuses a person's token.
- **`inspect` beside `call`** (15 core tools). A catalogued method runs through `inspect`
  when the engine catalog gives it the effect `read`, and through `call` otherwise; each
  refuses the other's methods and names the right tool. `describe` lists the tool of every
  method. Read calls that used `call` (for example `spatial.node_coords`) move to `inspect`.
- **Tool annotations**: `describe`, `find`, `get`, `inspect`, `timeseries`, `report` and
  `compare` carry the MCP read-only hint; `set`, `call` and `edit` the destructive hint, so a
  client can approve reads without approving changes.
- `inspect` and `call` add `"truncated": true` when an array was cut at 2,000 entries.
- The server requires engine catalog schema 2 (every method carries an effect).
- **Optional ledger toolset** (`OPENSWMM_MCP_TOOLSETS=core,ledger`): one `tally`
  tool commits a finished run's facts to a salted hash tree, lists them for
  citation, verifies claims and narratives claim by claim, and discloses single
  facts to a reviewer. A `verified-narrative` skill and prompt walk a writer through
  citing every number. Reviewers can check a disclosed fact offline with
  `python -m openswmm_mcp.ledger verify-proof`, given only the root and fact count.
  Standard library only; see `docs/user-guide/ledger.md`.
- **Provenance ledger** (`core,ledger`): `sources` registers the files a
  model was built from (GeoJSON, `.md`/`.txt`, `.csv`, `.inp`, with `.gpkg`/`.shp`
  and PDF as file-only or pypdf-gated), links source features to model elements by
  `id_match`, `nearest` or `manual` rules, reports lineage and document mentions,
  and retires sources. `locate` returns the elements, features, mentions and
  exclusions within a buffer of an element or point, as of and as known at a time.
  Sources in an incompatible CRS are listed and left out, never reprojected by guess.
  `tally` also commits a `sources_root` over the linked sources.
- **`model-provenance` skill** for tracing a model element or location back to its
  sources.
- **`ledger` optional extra** (`pip install 'openswmm.mcp[ledger]'`: `shapely`,
  `pyproj`) for buffer search, `nearest` links and reprojection.
- **14 core tools**: `open_model`, `run`, `session`, `save`, `describe`,
  `find`, `get`, `set`, `call`, `edit`, `timeseries`, `report`, `compare`,
  `export`. `describe`, `find`, `get`, `set` and `call` are driven by the
  engine catalog, so new engine capabilities need no new tool code.
- **5 gym tools** (`gym_describe`, `gym_config`, `gym_env`, `gym_job`,
  `gym_score`), registered with `OPENSWMM_MCP_TOOLSETS=core,gym`. Environment
  specs can observe any numeric engine field (`observations.fields`) or
  per-cell 2D quantity (`observations.cell_fields`) and actuate any writable
  field (`field_setpoint`). Spec errors keep their error code.
- **`builder` target** for `call`, `get` and `set`: author a model from
  scratch in a session-cached `ModelBuilder`, write it, then `open_model` it.
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
  (`test_catalog_contract.py`), calls every catalogued method with the
  catalog's signature (`test_method_contract.py`), walks each bundled skill's
  tool sequence end to end (`test_skill_walkthroughs.py`), launches the server
  over stdio the way Claude Desktop does, and caps the tool count and
  definition size (`test_server_budget.py`).
- Unit labels come from the engine catalog (`openswmm.engine.catalog.unit_label`),
  shared with openswmm.gymnasium.
- **`describe("capabilities")`** reports the engine build
  (`openswmm.engine.capabilities()`). `describe` resolves shared-view names to
  their canonical target (`surface2d.groundwater` is `groundwater2d`), and with
  a `session_id` returns a service's current values: `describe("options")`
  lists every option through the engine's own key enumeration.
- **2D and live results.** `timeseries`, `compare` and `export` read 2D cell and
  edge series (`kind="cell"` / `"edge"`, ids `"12"` / `"12:3"`) and species,
  and `timeseries(live=True)` reads a running session's 1D output.
- **`call` reaches flow tracing (`trace`), the 2D results reader
  (`surface_output`) and heat snapshots.** Methods that take a Python callback
  are refused, naming the method.
- **Optimisation jobs run on `openswmm_gymnasium.optimize`.** `gym_job` evaluates
  each population in parallel on local worker processes
  (`optimization.workers`, default all but one CPU, at most 8). An engine
  crash fails only its candidate (`optimization.on_failure`: `"fail"` or
  `"penalty"`) instead of the server.
- The tool budget test reserves room for the 7 `platform_*` tools of the cloud
  execution platform plan (core + gym + platform ≤ 26 tools, 40k characters).
  The server instructions and `open_model` mark text read from model and result
  files as data, not instructions.

### Fixed

- `report(name="groundwater")` raised `KeyError` on every meshed model: the
  catalog target is now `groundwater2d`.
- `set` on a cross-section changed nothing: its geometry fields are read-only.
  `link.xsect` now takes `[shape, g1, g2, g3, g4]` or a mapping.
- `find` and `get` on an element kind the model has none of (subcatchments in a
  2D-only model) returned an engine error; they now return no rows.
- `get_item`, `set_item` and `delete_item` through `call` name a missing `key`
  or `value` instead of failing inside the engine.
- A refused tool call (a missing argument, a lifecycle phase) is logged as one
  line. FastMCP logged each refusal with a full traceback, about 0.1 s apiece.
- `python -m openswmm_mcp` (and the `openswmm.mcp` script) now honour
  `OPENSWMM_MCP_TRANSPORT` and `OPENSWMM_MCP_HTTP_PORT`, and the HTTP/SSE
  transports apply the configured JWT/OAuth verifier (`create_auth` was never
  wired into the server).
