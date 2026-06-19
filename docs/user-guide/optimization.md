# Optimization with the `gym_*` tools

The `gym_*` namespace connects the MCP server to
[`openswmm.gymnasium`](https://github.com/HydroCouple/openswmm.gymnasium), so an
LLM can compose, run, score, and apply stormwater optimization studies — real-time
control (RTC), capital-improvement design (CIP), joint design+control, and
multi-objective variants — entirely through conversation.

Install the optional extra alongside the engine:

```bash
pip install 'openswmm.mcp[engine,gym]'
# MOEAs (NSGA-II etc.) additionally need:
pip install 'openswmm.gymnasium[platypus]'
```

Without the extra the server still starts; `gym_*` tools fail with an
actionable `DEPENDENCY_MISSING` error.

## Concepts

An **environment config** is a JSON document naming an `env_type` (`rtc`,
`cip`, `joint`, `mo_rtc`), the model `.inp`, observation features, reward
terms, and the action factories that define what can be controlled (runtime)
or sized (design). Configs are validated against the capability registry,
persisted as reviewable JSON under `<working_dir>/gym_configs/<name>.json`,
and survive server restarts. Run artifacts (trajectories, evaluation logs,
results) always land in user-visible directories, by default
`<inp_dir>/gym_runs/<run_or_job_id>/`.

Reward terms are framed internally as costs-to-minimize; episode rewards
follow the Gymnasium convention (higher is better), while optimization
objectives are reported as direction-adjusted costs.

## Walkthrough: size two conduits to trade off flooding vs. peak outflow

A typical natural-language session ("find pipe roughness and storage depth
settings that minimize flooding and peak outflow, then update my model")
maps onto this tool sequence.

**1. Discover the vocabulary.**
`gym_list_capabilities` returns every registered reward term, action factory,
and wrapper with JSON schemas for their params — no guessing.

**2. Create and validate a config.**

```json
gym_create_env_config name="cip-study" config={
  "env_type": "cip",
  "inp_path": "site_drainage_example.inp",
  "design_factories": [
    {"kind": "link_roughness",
     "params": {"link_ids": ["C1", "C2"], "low": 0.011, "high": 0.025}},
    {"kind": "node_max_depth",
     "params": {"node_ids": ["J1"], "low": 1.0, "high": 3.0}}
  ],
  "observations": {"node_depths": ["J1"]},
  "reward_terms": [
    {"kind": "flooding_volume", "params": {}},
    {"kind": "peak_outflow", "params": {"link_ids": ["C1"]}}
  ]
}
```

`gym_validate_env_config name="cip-study"` instantiates the env against the
real engine, resolves every element ID, and reports the observation size and
action-space bounds before any long run.

**3. Run a baseline (optional).**
`gym_run_episode name="cip-study"` with no policy runs a neutral midpoint
episode and writes `trajectory.jsonl` + `summary.json`. For RTC configs the
LLM can instead drive the simulation itself with `gym_env_open` →
`gym_env_reset` → repeated `gym_env_step` → `gym_env_close`.

**4. Start the search in the background.**

```json
gym_start_optimization name="cip-study" optimization={
  "algorithm": "nsga2", "budget": 200, "population_size": 20, "seed": 7
}
```

Returns a `job_id` immediately. Poll `gym_get_job` (monotone
`evaluations_done` / `budget`), list with `gym_list_jobs`, stop with
`gym_cancel_job` (takes effect between evaluations). Algorithms:
`random_search`, `grid_search`, and the Platypus MOEAs `nsga2`, `nsga3`,
`spea2`, `moead`, `gde3`. Two jobs run concurrently by default.

**5. Inspect and score results.**
`gym_get_job_results job_id=...` returns every evaluation's decision vector
(labeled `link_roughness:C1`, …), the Pareto-optimal subset, and a best pick.
`gym_score_front` computes `hypervolume`, `normalized_hypervolume`, `igd`,
`igd_plus`, `epsilon_indicator`, `spread`, and `r2_indicator`;
`gym_compare_runs` scores several jobs side by side, and `gym_pareto_filter`
non-dominance-filters any inline front.

**6. Apply the chosen design to the model.**

```json
gym_apply_design job_id=... session_id="default" evaluation="best"
```

writes the decision values onto the open session (link roughness/length,
conduit diameter, node max depth — clipped to the design ranges). Follow with
`building_write_model` to persist a new `.inp`, or rerun the simulation with
the `lifecycle_*` / `analysis_*` tools to verify the improvement.

## Tool reference

| Tool | Purpose |
|------|---------|
| `gym_list_capabilities` | Registered kinds + param schemas + env types. |
| `gym_describe_benchmark` | Registered `OpenSWMM/*` benchmark env IDs. |
| `gym_create_env_config` / `gym_get_env_config` / `gym_list_env_configs` / `gym_delete_env_config` | JSON config CRUD. |
| `gym_validate_env_config` | Build + reset against the real engine; report spaces. |
| `gym_run_episode` | One rollout under a `constant` / `random` / `replay` policy. |
| `gym_env_open` / `gym_env_reset` / `gym_env_step` / `gym_env_close` / `gym_list_envs` | Interactive LLM-as-controller loop. |
| `gym_start_optimization` / `gym_get_job` / `gym_list_jobs` / `gym_cancel_job` / `gym_get_job_results` | Background design-search jobs. |
| `gym_pareto_filter` / `gym_score_front` / `gym_compare_runs` | Pareto filtering and quality indicators. |
| `gym_apply_design` | Apply an optimized design vector to an open model session. |

Design details and phase history: see
`docs/developer/GYMNASIUM_INTEGRATION_PLAN.md`.
