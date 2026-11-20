# Disposition of the alpha-RTC agent feedback (10 issues)

Source: `pystorms/.../alpha_openswmm_findings_and_rerun.md` — an agent's report
after driving the gym MCP tools to optimize reactive RTC on the *alpha* network.
This records, per issue: whether the root-cause was **accurate**, what was
**implemented**, and what was **deferred** (with rationale). Validated and
tested in the `openswmm` conda env.

## Summary

| # | Issue | Disposition |
|---|---|---|
| 1 | Complex params rejected as JSON strings | **Fixed** (defensive coercion); root-cause corrected |
| 2 | EnvConfig/ObservationSpec not discoverable | **Fixed** (schema + features + examples + better errors) |
| 3 | No first-class single/baseline policy eval | Deferred (documented workaround) |
| 4 | Baseline corner can be missing from front | **Partly** (documented + guidance in capabilities); seeding deferred |
| 5 | Monotonic projection undocumented | **Fixed** (capability note; decode already returns projected) |
| 6 | "CSO" reward semantics ambiguous | **Fixed** (capability note; tag-resolver deferred) |
| 7 | Results payload bloat | Deferred (documented: read `evaluations.jsonl`) |
| 8 | Path/concurrency under-documented | Deferred (doc-only) |
| 9 | Silent algorithm/budget fallback | **Fixed** (resolved settings echoed everywhere; #1 removes the trigger) |
| 10 | Dashboards not discoverable/offline | Deferred (skills/dashboard layer, out of MCP-tool scope) |

---

## Implemented

### #1 — JSON-string object/array params (critical)

**Root-cause (corrected twice).** The report blamed a missing schema type; on
FastMCP 3.2.4 the schema is in fact typed (`optimization` is
`{"anyOf":[{"type":"object",...},{"type":"null"}],"default":null}`). The real
cause is one layer deeper: FastMCP validates each tool argument against the
function signature with **Pydantic, at bind time**, so a stringified argument is
rejected with `Input should be a valid dictionary [type=dict_type, ...,
input_type=str]` **before the tool body runs**. A first attempt that decoded the
string *inside* the function body therefore never executed for the failing path
— the string was rejected during binding. (Confirmed by driving
`mcp.call_tool("gym_start_optimization", {"optimization": "{...}"})`.)

**Fix (the correct layer).** A Pydantic `BeforeValidator`,
`decode_json_if_str`, that runs **during binding**: a string argument is
`json.loads`-decoded there, so Pydantic then validates the resulting
object/array; non-strings pass through; bad JSON yields a clear validation
error. Exposed as reusable annotated parameter types in `gym_support/config.py`
— `JsonObject`, `JsonObjectRequired`, `JsonFloatMatrix`, `JsonFloatVector`,
`JsonStrListRequired` — and applied to every complex `gym_*` param:
`optimization`/`config`/`policy`/`action` (gym_runs),
`config` (gym_envs create/validate), and
`front`/`reference_point`/`ideal_point`/`reference_front`/`weights`/`indicators`/`job_ids`
(gym_scoring). So `optimization='{"algorithm":"nsga2","budget":600}'` now binds
and runs NSGA-II — it can no longer be lost to a bind failure.

Crucially, the **client-facing schema is unchanged** (still `object|null` /
`array|null`): the validator decodes strings transparently, so clients that send
proper objects are unaffected and clients that stringify now also work. The
function-body `coerce_json_param` helper is retained as defence for direct/
internal callers (e.g. `_parse_config`), but the binding-layer validator is what
fixes the FastMCP path.

Tests: `test_gym_scoring.py::test_pareto_filter_accepts_json_string_front_via_binding_layer`
(through the real `mcp.call_tool` binding layer),
`test_gym_config.py::test_decode_json_if_str_validator_runs_inside_pydantic_binding`
and `::test_coerce_json_param_*`,
`test_gym_envs.py::test_json_string_config_is_accepted`,
`test_gym_jobs.py::test_start_optimization_accepts_json_string_and_echoes_resolved`.

### #9 — Silent algorithm/budget fallback / verifiability

`OptimizationConfig.resolved()` returns the applied settings (algorithm, budget,
seed, plus `population_size` for MOEAs / `grid_levels` for grid). It is now
echoed in the **start response and every `gym_get_job` snapshot** (`optimization`
block) and in **`result.json` / `gym_get_job_results`** — previously these lived
only in `job.json`. Combined with #1 (the optimization arg now binds, instead of
being dropped → defaulting to `random_search`/50), the silent-downgrade path is
closed: a malformed `optimization` raises rather than falling back.

Tests: `test_gym_jobs.py::test_optimization_config_resolved_includes_only_relevant_fields`
and `…_accepts_json_string_and_echoes_resolved`.

### #2 — EnvConfig / ObservationSpec discoverability

`gym_list_capabilities` now also returns: `env_config_schema`
(`EnvConfig.model_json_schema()`), `observation_spec_schema`,
`observation_features` (the 15 valid `observations` keys, from the single source
of truth `OBSERVATION_FEATURES`), and one worked `examples` config per common
`env_type` (cip/rtc/control_curve/schedule). The config-validation error now,
when the failure mentions observations, enumerates the valid feature keys and
states they map *element-ID lists*, not IDs directly — fixing the exact
three-try loop the agent hit (`observations.J1 Extra inputs are not permitted`).

Tests: `test_gym_envs.py::test_list_capabilities_surfaces_env_and_observation_schema`,
`…::test_observations_validation_error_enumerates_feature_keys`,
`test_gym_config.py::test_observation_features_are_real_observation_spec_fields`.

### #5 / #6 — Projection + CSO semantics (docs)

Added to `gym_list_capabilities` notes: (#5) the exact monotonic projection —
`nondecreasing` = left-to-right cumulative max, `nonincreasing` = cumulative min,
applied at decode time; `gym_decode_policy` already returns the projected
`y_values` that ran. (#6) `cso_volume` sums **node overflow**; weir/relief spill
is a **link flow** → use `uncontrolled_discharge` with `link_ids`; never
`cso_volume` for weir CSO. Also added the #4 baseline guidance (below) as a note.

---

## Deferred (with rationale)

### #3 — First-class single/baseline policy evaluation

Real gap: `gym_run_episode` can't run a chosen decision vector for a Box-action
policy env (control_curve/schedule/market), so measuring the all-open baseline
needs the degenerate `y_low=y_high=1.0` trick. A clean fix is a new
`gym_evaluate_policy(name|config, decision_vector)` tool (or a typed `PolicySpec`
for `gym_run_episode`). Deferred as a new tool surface; the documented workaround
(pin `y_low=y_high`) works today and is now described in the capabilities notes.

### #4 — Baseline (dominating corner) missing from the front

Real correctness gap for `control_curve`/design search: random/MOEA sampling may
never hit the exact all-`y_high` baseline, so the reported Pareto front can omit
the true CSO-optimal corner. **Implemented now:** a capabilities note instructing
the agent to measure the baseline separately and union it in.
**Deferred:** seeding the initial population with the `y_init`/extreme corners
inside the search runners (`jobs.py::_run_*`) — a search-loop change wanted its
own tests; the union-the-baseline guidance covers correctness in the meantime.

### #7 — Results payload size

`gym_get_job_results` inlines every Pareto point's full decision dict. A compact
objectives-only mode + pagination is worthwhile but additive API surface;
deferred. The canonical cheap source is documented: each job's
`output_dir/evaluations.jsonl` and `result.json`.

### #8 — Path/concurrency docs

Doc-only: `inp_path`/`config_dir` must be readable by the server process; jobs
run concurrently and share CPU (submit sequentially for best wall-clock).
Deferred to the user-guide.

### #10 — Dashboard discoverability / offline rendering

Concerns the **skills/dashboard** layer (templates, `viz.figures`, CDN-vs-inline
Plotly, no-WebGL desktop viewer), not the gym MCP tools. Valid, but a separate
surface; deferred to the skills package.

---

## Incidental finding (not in the 10)

`test_gym_jobs.py::test_nsga2_produces_nondominated_front` is **flaky**:
`_run_platypus` seeds only numpy (`np.random.default_rng(opt.seed)` is used by
`random_search`), not Platypus's global `random`, so MOEA runs are
non-deterministic and the `np.allclose`-based Pareto-index reconstruction in
`_assemble_result` can occasionally include a near-duplicate dominated point.
Recommended follow-up: seed Platypus for reproducibility (carefully, given the
global-RNG/thread-safety concern) and/or make the Pareto-index reconstruction
exact. Left unchanged here (pre-existing, outside this feedback's scope).
