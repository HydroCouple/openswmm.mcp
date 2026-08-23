# Gymnasium Integration Plan

**Date:** 2026-06-11
**Status:** DRAFT — for review before implementation
**Scope:** Integrate `openswmm.gymnasium` into the MCP server so users can orchestrate
RTC / CIP / joint / multi-objective optimization configurations and runs through natural
language. Full orchestration scope; long runs execute as async background jobs;
`openswmm.gymnasium` is an **optional extra** (already added to `pyproject.toml` as
`gym = ["openswmm.gymnasium"]`).
**Authoring guidance:** `CLAUDE.md` §1 (think before coding), §2 (simplicity), §3
(surgical), §4 (verifiable goals), §4.1 (transparent file IO), §5.0 (preconfigured plan).

---

## 1. Goals

1. Expose the `openswmm.gymnasium` surface — env construction (`SwmmRTCEnv`,
   `SwmmCIPEnv`, `SwmmJointCIPRTCEnv`, `SwmmMORTCEnv`), observation builders, reward
   terms, design/runtime action factories, wrappers, and scoring — as MCP tools an LLM
   can drive conversationally.
2. Let the LLM compose a **declarative environment/optimization config** (JSON, validated
   by Pydantic), instantiate it against the real engine, and run it.
3. Run long optimizations (Platypus MOEAs for design search, policy rollouts for RTC) as
   **async background jobs** with start / status / results / cancel tools.
4. Support **LLM-as-controller**: interactive `reset` / `step` tools so the model itself
   can act as the RTC policy on an open environment.
5. Score and compare results with the existing scoring module (hypervolume, normalized
   HV, IGD/IGD+, ε-indicator, spread, R2, Pareto filtering).
6. Bridge results back into the modeling workflow: apply a chosen design vector to a
   model session and write the updated `.inp`.

## 2. Non-Goals

1. No changes to `openswmm.gymnasium` or `openswmm.engine` themselves. Anything missing
   upstream is recorded as a follow-up, not patched around.
2. No new RL training algorithms inside the MCP server. Policy *training* (SB3 etc.)
   stays outside; the server orchestrates env config, rollouts, MOEA design search, and
   scoring.
3. No legacy-backend support. All gym tools require the v6 handle-based engine
   (`require_new_engine()` semantics); the gym package already hard-guards against the
   legacy solver.
4. No refactor of unrelated MCP infrastructure (auth, resources, prompts, existing tool
   domains).

## 3. Design overview

### 3.1 New tool domain: `gym`

Mounted in `server.py` alongside the existing 20 domains:

```python
mcp.mount(gym_mcp, namespace="gym")
```

New files under `src/openswmm_mcp/`:

| File | Responsibility |
|---|---|
| `gym_support/registry.py` | Capability registry: maps string names → gym classes (reward terms, collectors, design/runtime factories, wrappers, env types). |
| `gym_support/config.py` | Pydantic config models (`EnvConfig`, `OptimizationConfig`, …) and `build_env(config) -> gym.Env`. |
| `gym_support/jobs.py` | `JobManager`: background optimization jobs on a thread pool, job records, progress, cancellation. |
| `gym_support/store.py` | `GymStore`: registry of named configs (persisted to JSON on disk), open interactive envs, and finished-run artifacts. |
| `tools/gym_envs.py` | Config CRUD, validation, capability listing, env description. |
| `tools/gym_runs.py` | Episode rollouts, interactive reset/step/close, optimization job lifecycle. |
| `tools/gym_scoring.py` | Pareto filtering, indicators, run comparison, apply-design bridge. |

A package named `gym_support` (not `gymnasium`) avoids any confusion with the installed
`gymnasium` distribution.

### 3.2 Optional-dependency guard

Mirror the engine pattern in `dependencies.py`:

```python
def require_gymnasium() -> None:
    """Raise ToolError(ErrorCode.DEPENDENCY_MISSING) unless
    openswmm_gymnasium is importable. Imported lazily so the server
    starts cleanly without the gym extra."""
```

All gym tool modules import `openswmm_gymnasium` lazily (inside functions or behind
`TYPE_CHECKING`) so `server.py` mounts the domain unconditionally but tools fail with a
clear, actionable error when the extra is absent.

### 3.3 Declarative config schema

The core artifact the LLM manipulates. One Pydantic model tree, serialized as JSON,
stored by name in `GymStore`:

```python
class ObservationSpec(BaseModel):
    node_depths: list[str] = []
    node_heads: list[str] = []
    node_inflows: list[str] = []
    node_overflows: list[str] = []
    link_flows: list[str] = []
    link_depths: list[str] = []
    link_velocities: list[str] = []
    link_capacities: list[str] = []
    link_settings: list[str] = []
    subcatch_runoff: list[str] = []
    rainfall_gages: list[str] = []
    include_clock: bool = False

class RewardTermSpec(BaseModel):
    kind: str               # registry name: "flooding_volume", "cso_volume",
                            # "peak_outflow", "reliability_margin", "setpoint_smoothness"
    params: dict[str, Any] = {}

class ActionFactorySpec(BaseModel):
    kind: str               # "orifice_setting", "node_lateral_inflow" (runtime);
                            # "link_diameter", "link_roughness", "link_length",
                            # "node_max_depth" (design)
    params: dict[str, Any] = {}

class WrapperSpec(BaseModel):
    kind: str               # "record_trajectory", "rescale_box_actions",
                            # "linear_scalarize", "tchebycheff_scalarize",
                            # "forecast_observation", "mask_runtime", "mask_design"
    params: dict[str, Any] = {}

class EnvConfig(BaseModel):
    env_type: Literal["rtc", "cip", "joint", "mo_rtc"]
    inp_path: str
    runtime_factories: list[ActionFactorySpec] = []
    design_factories: list[ActionFactorySpec] = []
    observations: ObservationSpec
    reward_terms: list[RewardTermSpec] = []
    control_interval_steps: int = 1
    max_episode_steps: int | None = None
    wrappers: list[WrapperSpec] = []
    rpt_path: str | None = None
    out_path: str | None = None
```

`registry.py` owns the `kind` → class mapping and per-kind param validation, so
`gym_list_capabilities` and `build_env` can never drift apart (one source of truth, the
same pattern as `RewardRegistry` upstream).

### 3.4 Execution model

* **Episode rollout / MOEA jobs** run on a `ThreadPoolExecutor` owned by `JobManager`
  (env stepping is synchronous, CPU-bound C engine work; the v6 handle-based Solver is
  thread-safe with one Solver handle per job). Tool handlers stay `async` and never
  block the event loop.
* **Interactive envs** (LLM-as-controller) are owned by `GymStore` keyed by `env_id`,
  with explicit `gym_env_close` and idle-timeout cleanup, mirroring `SessionManager`
  for model sessions.
* **Job records** carry: `job_id`, config name, algorithm, budget, state
  (`pending|running|done|failed|cancelled`), progress counters (evaluations done /
  budget, current generation), and on completion a result payload (Pareto set:
  objective vectors + decision vectors) plus paths of any files written.
* **Transparent file IO (CLAUDE.md §4.1):** every job writes its artifacts (trajectory
  CSV/JSON, Pareto front JSON, `.rpt`/`.out`) under a user-visible directory —
  default `<inp_dir>/gym_runs/<job_id>/`, overridable per job — never to temp dirs.

### 3.5 Optimization algorithms (initial set)

| Algorithm | Use | Backing |
|---|---|---|
| `random_search` | Baseline for any env type | numpy sampling of the design/runtime space |
| `grid_search` | Small CIP design spaces | itertools over per-dimension levels |
| `nsga2` (and other Platypus MOEAs by name) | CIP / joint multi-objective design search | `platypus-opt` extra (already the sole supported MOO adapter upstream) |
| `constant_policy` / `replay_policy` | RTC rollout evaluation | direct env stepping |

Platypus availability is checked at job start with the same lazy-import + clear-error
pattern as §3.2.

## 4. Tool surface

Namespaced `gym_*` once mounted. Response models are Pydantic v2
(`from_attributes=True`), matching existing domains.

### 4.1 Capability & config tools (`tools/gym_envs.py`)

| Tool | Purpose |
|---|---|
| `gym_list_capabilities` | Enumerate registered env types, reward terms, observation collectors, action factories, wrappers, scoring indicators, algorithms — with param schemas, so the LLM can self-discover the vocabulary. |
| `gym_create_env_config` | Validate and store a named `EnvConfig`. |
| `gym_get_env_config` / `gym_list_env_configs` / `gym_delete_env_config` | Config CRUD. |
| `gym_validate_env_config` | Instantiate the env against the real Solver, then close it; return observation-space size, action-space bounds per factory, and reward-term wiring. Catches bad element IDs before any long run. |
| `gym_describe_benchmark` | List/describe registered benchmark envs (`OpenSWMM/Minimal-RTC-v0`, …, `b01_twin_tank`) so users can start from a known scenario. |

### 4.2 Execution tools (`tools/gym_runs.py`)

| Tool | Purpose |
|---|---|
| `gym_run_episode` | Single rollout of a config with a named policy (`constant`, `random`, `replay` with an explicit action sequence). Wraps with `RecordTrajectory`; returns reward total, per-term breakdown, episode length, artifact paths. Blocking but bounded by `max_episode_steps`. |
| `gym_env_open` / `gym_env_reset` / `gym_env_step` / `gym_env_close` | Interactive LLM-as-controller loop. `step` takes the runtime action dict, returns observation (labeled by feature name), reward, terminated/truncated, info. |
| `gym_start_optimization` | Start a background job: config name + `OptimizationConfig` (algorithm, budget/generations, population size, seed, output dir). Returns `job_id` immediately. |
| `gym_get_job` / `gym_list_jobs` | Status + progress polling. |
| `gym_cancel_job` | Cooperative cancellation between evaluations. |
| `gym_get_job_results` | Pareto set (objective + decision vectors, labeled by factory/element), best-by-scalarization pick, artifact paths. |

### 4.3 Scoring & bridge tools (`tools/gym_scoring.py`)

| Tool | Purpose |
|---|---|
| `gym_pareto_filter` | `pareto_front` over supplied or job-referenced objective vectors. |
| `gym_score_front` | Indicators by name: `hypervolume`, `normalized_hypervolume`, `igd`, `igd_plus`, `epsilon_indicator`, `spread`, `r2_indicator`, with reference point/set inputs. |
| `gym_compare_runs` | Cross-job comparison table of indicators (natural-language scenario comparison). |
| `gym_apply_design` | Apply a chosen decision vector from a job result to an **open MCP model session** (via the same engine setters the design factories use), so the user can then `building_write_model` / rerun analysis with existing tools. Closes the loop from optimization back to the model. |

## 5. Implementation phases

Each phase is independently mergeable. **Definition of done for every item: documented
`.pyi` stub (epytext docstrings — `@param`/`@type`/`@return`/`@rtype`) + unit tests
against the real handle-based Solver (no mocks).**

### Phase 1 — Foundation: registry, config, guard ✅ (implemented 2026-06-11)

1. `dependencies.require_gymnasium()` → verify: unit test with/without import succeeding
   (skip-marked when the extra is absent).
2. `gym_support/registry.py` + `gym_support/config.py` (`EnvConfig` tree, `build_env`)
   → verify: `build_env` constructs all four env types against a real `.inp` fixture;
   bad `kind`/element IDs raise `ToolError` with actionable messages.
3. `gym_support/store.py` with JSON persistence (§7.1) → verify: config CRUD
   round-trips; duplicate names rejected; configs survive a store reload from disk;
   corrupt/unreadable JSON raises an actionable `ToolError` rather than silently
   dropping configs.
4. Stubs: `gym_support/registry.pyi`, `gym_support/config.pyi`, `gym_support/store.pyi`.

### Phase 2 — Capability & config tools ✅ (implemented 2026-06-11)

1. `tools/gym_envs.py`, mount as `namespace="gym"` → verify: names appear in
   `test_tool_registration.py` guard; `gym_validate_env_config` reports correct
   space sizes for a fixture model.
2. `gym_list_capabilities` output is generated from the registry → verify: every
   registered kind appears with a JSON-schema of its params.

### Phase 3 — Episodes & interactive control ✅ (implemented 2026-06-11; episode
runner + `EnvManager` live in `gym_support/envs.py` rather than splitting
`store.py`, keeping disk CRUD and live handles separate)

1. `gym_run_episode` with `constant` / `random` / `replay` policies → verify: real-Solver
   episode on the fixture `.inp` completes; reward breakdown sums to total; trajectory
   artifact written under the user-visible run directory.
2. Interactive `gym_env_open/reset/step/close` → verify: a scripted reset→step→…→close
   sequence matches a direct `SwmmRTCEnv` run step-for-step; idle cleanup closes
   leaked envs.
3. Stubs + tests as above. Note the runoff/routing cadence pitfall: fixtures that assert
   per-step forcing effects need `WET_STEP == ROUTING_STEP` in the `.inp`.

### Phase 4 — Background optimization jobs ✅ (implemented 2026-06-11; the
`constant_policy`/`replay_policy` rows of §3.5 are served by `gym_run_episode`
rather than the job manager — jobs cover design search only)

1. `gym_support/jobs.py` (`JobManager`, thread pool, cooperative cancel) → verify:
   `random_search` job on the fixture CIP env runs to completion in the background while
   `gym_get_job` reports monotonically increasing progress; cancel stops within one
   evaluation.
2. `nsga2` via Platypus (optional extra) → verify: skip-marked test produces a
   non-dominated front on the twin-tank benchmark; missing-platypus error is actionable.
3. `gym_get_job_results` labels decision vectors by factory + element ID → verify
   against the factories' declared spaces.

### Phase 5 — Scoring & model bridge ✅ (implemented 2026-06-11; all five plan
phases complete)

1. `tools/gym_scoring.py` indicator tools → verify: indicator values match direct calls
   into `openswmm_gymnasium.scoring` on a fixed front.
2. `gym_apply_design` → verify: applying a decision vector to an open session changes the
   targeted link/node properties (read back via `query_get_link_info` etc.), and
   `building_write_model` persists them.
3. Docs: user-guide page (`docs/user-guide/optimization.md`) with an end-to-end
   natural-language walkthrough (configure → validate → optimize → score → apply).

## 6. Testing strategy

* All tests follow the repo conventions: pytest + the existing `conftest` fixtures
  (`inp_path`, `fake_ctx`, `session_manager`), **real engine, no mocks**, `integration`
  marker for engine-dependent tests.
* New fixture: a small RTC-capable `.inp` (one storage node, one orifice, one outfall,
  short event) checked into `tests/fixtures/`, with `WET_STEP == ROUTING_STEP` so
  single-step assertions are deterministic. The twin-tank benchmark from
  `openswmm_gymnasium.benchmarks.b01_twin_tank` is reused where richer topology helps.
* All test outputs (trajectories, fronts, `.rpt`/`.out`) go to a reviewable
  `tests/_output/` directory, not temp files (CLAUDE.md §4.1).
* Sandbox/CI caveat: the engine ships macOS-only wheels at present; engine-dependent
  tests run on macOS dev machines and are skip-marked elsewhere via the existing
  `integration` marker.

## 7. Resolved design decisions (confirmed 2026-06-11)

1. **Config persistence — JSON on disk.** `GymStore` persists each named `EnvConfig`
   (and `OptimizationConfig`) as a JSON file under a user-visible directory (CLAUDE.md
   §4.1). *Amended during Phase 2:* the default root is the single, discoverable
   `<working_dir>/gym_configs/<name>.json` (rather than per-`inp` directories, which
   would scatter configs and break `gym_list_env_configs`); every tool accepts a
   `config_dir` override for callers who want configs beside a model. The disk is the
   store, so configs survive restarts; tool responses report the file path so users can
   review/edit/version them.
2. **MO rollouts — as proposed.** `gym_run_episode` on `SwmmMORTCEnv` reports the
   reward vector, plus the scalarized total when a scalarize wrapper is configured.
3. **Job concurrency — `max_workers=2` confirmed** as the job-pool default, tunable
   via `config.py` settings.

## 8. File-change summary

| Change | Path |
|---|---|
| Edit (done) | `pyproject.toml` — `gym = ["openswmm.gymnasium"]` optional extra |
| Edit | `src/openswmm_mcp/server.py` — mount `gym_mcp` |
| Edit | `src/openswmm_mcp/dependencies.py` — `require_gymnasium()` |
| New | `src/openswmm_mcp/gym_support/{registry,config,jobs,store}.py` + `.pyi` stubs |
| New | `src/openswmm_mcp/tools/{gym_envs,gym_runs,gym_scoring}.py` |
| New | `tests/test_gym_{registry,config,envs,runs,jobs,scoring,apply_design}.py` |
| New | `tests/fixtures/<rtc_fixture>.inp` |
| Edit | `tests/test_tool_registration.py` — add `gym_*` names |
| New | `docs/user-guide/optimization.md` |
