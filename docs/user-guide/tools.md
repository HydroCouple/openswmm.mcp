# Tools

The server registers **14 core tools**, **5 optional gym tools** and one opt-in
`run_python` tool. Together their definitions are about 17k characters (under
5k tokens), so every MCP client, including Claude Desktop, can load them.

The engine exposes over a thousand functions. Instead of one tool per function,
five generic tools (`describe`, `find`, `get`, `set`, `call`) read the engine's
machine-readable catalog (`openswmm.engine.catalog`) and reach every property
and method it lists. When the engine gains a capability, the server reaches it
without new tool code.

## Core tools

| Tool | What it does |
|---|---|
| `open_model(path?, session_id, lenient)` | Open a `.inp` model into a session, or start an empty model when `path` is omitted. Returns counts, key options, the time window, file paths and parse warnings. |
| `run(session_id, until, max_steps)` | Advance the simulation: `until="end"` (and write the report), an ISO datetime, or `"+<n>[s\|m\|h\|d]"`; `max_steps` for step-wise control. Reports progress; returns continuity errors when finished. |
| `session(action, session_id, new_session_id)` | `list`, `state`, `close`, `clone` (hot-start state into a new session) or `reset` (reopen at the start). |
| `save(session_id, path, format, profile)` | Write the model (`inp`, `gpkg`, `hdf5`), a hot-start file, or the report. `profile="SWMM5"` writes a SWMM 5 compatible `.inp`. |
| `describe(topic, session_id)` | Discover the engine: element kinds, services, fields (type, units, read/write, lifecycle phases) and method signatures; `enum:<Name>`; `output` for time-series variables. |
| `find(session_id, kind, pattern, type, where)` | List element IDs filtered by regex, subtype (`"STORAGE"`) and a field predicate (`"stats.max_depth > 2"`). |
| `get(session_id, kind, fields, ids)` | Read fields as a table. All elements use the engine's bulk arrays where they exist. Works for services too (`kind="mass_balance"`). |
| `set(session_id, kind, changes)` | Write fields: `[{"id": "C1", "field": "roughness", "value": 0.015}]`. Each change is validated and reported individually. |
| `call(session_id, target, method, args)` | Run any catalogued method on a service (`"forcing"`), an element (`"node:OUT1.outfall"`), a standalone target (`"xsect"`, `"output"`, `"geopackage"`, `"hotstart"`, `"builder"`) or a function module (`"datetime"`). |
| `edit(session_id, action, kind, ids, ...)` | Structural edits: `add`, `delete`, `preview_delete`, `rename`, `convert`. |
| `timeseries(source, kind, ids, variable)` | Result series from a finished session or an `.out` file, peak-preserving downsampling; `variable="quality:TSS"` for pollutants. |
| `report(session_id, name)` | `summary`, `mass_balance`, `flooding`, `capacity`, `storage`, `pumps`, `subcatchments`, `statistics`, `quality`, `2d`, `groundwater`. |
| `compare(a, b, kind, variable)` | Two sessions, `.out` files or an observed CSV: per-element peak, volume, RMSE, NSE and bias. |
| `export(session_id, path, kind, fields \| variable)` | Write a field table or a result series to CSV or JSON. |

### Addressing

- **Element kinds** are `node`, `link`, `subcatchment`, `gage`, `pollutant`,
  `landuse`, `pattern`, `table`, and the reaction `species`, `coefficient` and
  `term` collections. Sub-view fields use a dotted path: `stats.max_depth`,
  `storage.functional`, `pump.startup_depth`, `xsect.g1`.
- **Services** are everything else reachable from a model: `forcing`,
  `options`, `climate`, `controls`, `inflows`, `infrastructure.lids`,
  `surface2d.groundwater.transport`, and so on. `describe()` lists them.
- **Elements** in `call` are `<kind>:<id>[.<subview>]`; IDs may contain dots
  (`node:MH.1.outfall`).
- **Standalone targets** take their constructor arguments in `args`: `xsect`
  takes `shape`, `geom1`..`geom4` and `units` (or a `from` spec such as
  `{"method": "from_street", ...}`); `geopackage` and `hotstart` take `path`;
  `output` reads the session's results unless given a `path`. The session
  opens and closes these readers itself.
- **Enums** are passed and returned by name (`"JUNCTION"`, `"PERSIST"`).
- **Units** are the model's own; `describe(..., session_id=...)` and every
  `get` response name them (`ft`, `CFS`, `ft3`, ...).
- **Files** in arguments resolve against `OPENSWMM_MCP_WORKING_DIR`.

### Authoring a model with `builder`

`builder` is a `ModelBuilder` kept by the session, for writing a model from
scratch without running it. Its methods take positional indices: elements are
numbered in the order they are added, and `add_node` / `add_link` return an
error code (`0` on success), not the index. Type and shape arguments are the
integer codes of `NodeType`, `LinkType` and `XSectShape` (`describe("enum:NodeType")`).

```text
open_model(session_id="b")                                   # a session to hold the builder
call("b", "builder", "add_node", {"node_id": "J1", "node_type": 0})   # JUNCTION, index 0
call("b", "builder", "add_node", {"node_id": "O1", "node_type": 1})   # OUTFALL, index 1
call("b", "builder", "add_link", {"link_id": "C1", "link_type": 0})   # CONDUIT, index 0
call("b", "builder", "set_node_invert", {"idx": 0, "elev": 10.0})
call("b", "builder", "set_node_invert", {"idx": 1, "elev": 9.0})
call("b", "builder", "set_link_nodes", {"idx": 0, "from_node": 0, "to_node": 1})
call("b", "builder", "set_link_xsect", {"idx": 0, "shape": 0, "g1": 1.0})   # CIRCULAR
call("b", "builder", "set_option", {"key": "START_DATE", "value": "01/01/2026"})
call("b", "builder", "write", {"path": "built.inp"})
open_model(path="built.inp", session_id="run")
```

Set `START_DATE` and `END_DATE`: the builder's defaults are not a runnable
simulation window. `to_solver` is refused, because a built model runs only
after it is written and opened.

### A model's structure changes

Adding, deleting or converting elements changes the engine's arrays. The next
`run` therefore writes the edited model to `<name>_edited.inp` in the session
folder and reopens it before initialising, so derived data is rebuilt.

## Gym tools (`OPENSWMM_MCP_TOOLSETS=core,gym`)

| Tool | Actions |
|---|---|
| `gym_describe(topic)` | Capabilities, observation features, reward terms, design factories, runtime actuators; `benchmark[:<id>]`. |
| `gym_config(action, name, spec)` | `create`, `get`, `list`, `validate`, `delete` named environment specs. |
| `gym_env(action, env_id, ...)` | `open`, `reset`, `step`, `close`, `list`, `run_episode`. |
| `gym_job(action, job_id, ...)` | `start` (NSGA-II and other optimisers), `status`, `results`, `cancel`, `list`, `decode_policy`. |
| `gym_score(action, ...)` | `pareto`, `score` (hypervolume, IGD, epsilon, R2, spread), `compare`, `apply_design`. |

Environment specs can observe any numeric engine field through
`observations.fields` (`{"link.stats.max_flow": ["C1"]}`) and actuate any
writable one through the `field_setpoint` runtime factory.

## `run_python` (opt-in)

`OPENSWMM_MCP_ENABLE_PYTHON=true` registers `run_python(session_id, code)` on
the stdio transport only. The code runs with the server's permissions, with
`solver`, `engine`, `catalog` and `np` in scope. Do not enable it on a server
that others can reach.

## Migrating from v1

v1 registered 663 tools. Each maps to one of the tools above:

| v1 namespace | v2 |
|---|---|
| `lifecycle_*` | `open_model`, `run` (step, stride, until), `session`, `save(format="rpt")`; events and runoff-interface files via `call(target="events" \| "solver", ...)` |
| `query_*` | `get`, `find`, `report(name="summary")` |
| `nodes_*`, `links_*`, `subcatchments_*`, `pollutants_*`, `model_*` | `get` / `set` on the element fields; methods via `call(target="<kind>:<id>", ...)` or `call(target="options", ...)` |
| `building_*` | `open_model()` without a path, then `edit(action="add")`, `set`, `save`; or author with `call(target="builder", ...)` (see below) |
| `editing_*` | `edit`; inlet and virtual-junction operations via `call(target="editor", ...)` |
| `forcing_*`, `controls_*`, `inflows_*`, `tables_*`, `climate_*`, `infrastructure_*`, `heat_*`, `reactions_*`, `water_age_*`, `initial_quality_*`, `process_components_*` | `call(target="<service>", ...)`; fields via `get`/`set` |
| `twod_*`, `infil2d_*` | `get(kind="surface2d", ...)`, `call(target="surface2d" \| "surface2d.infiltration", ...)`, `report(name="2d")` |
| `hotstart_*` | `save(format="hotstart")`, `session(action="clone")`, `call(target="hotstart", ...)` |
| `analysis_*` | `report`, `timeseries`, `compare`, `export`; raw output via `call(target="output", ...)` |
| `geopackage_*` | `save(format="gpkg")`, `call(target="geopackage", ...)`, `compare` with an observed CSV |
| `spatial_*` | `call(target="spatial", ...)` |
| `xsect_*` | `call(target="xsect", method=..., args={"shape": ..., "geom1": ..., "units": "US", ...})` |
| `datetime_*` | `call(target="datetime", ...)` |
| `gym_*` (23 tools) | `gym_describe`, `gym_config`, `gym_env`, `gym_job`, `gym_score` |

The SWMM 5 (legacy) backend is gone: every session uses the handle-based
OpenSWMM 6 engine.
