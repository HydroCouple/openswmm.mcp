# Agent Brief: Put the `gym_*` Integration Through Its Paces

**Audience:** an autonomous coding agent running on Caleb's Mac with this repo
checked out.
**Goal:** execute the full gym test suite (especially the engine-backed
integration tests, which have **never been run on this machine**), exercise the
tool surface end-to-end, and report — *not fix* — anything that fails.
**Context:** `docs/developer/GYMNASIUM_INTEGRATION_PLAN.md` (all 5 phases
implemented 2026-06-11). Sibling repos expected at
`../openswmm.engine` and `../openswmm.gymnasium`.

## Ground rules

1. Do not modify source under `src/` or existing tests. Your job is to verify
   and report. If something fails, capture the evidence and move on.
2. No mocks, ever. All tests run against the real handle-based
   `openswmm.engine.Solver`.
3. All files you generate (logs, reports, scratch models) go in
   `tests/_paces/` — user-reviewable, nothing in temp dirs (CLAUDE.md §4.1).
4. Work through the stages in order; later stages assume earlier ones passed.
5. Write the final report to `tests/_paces/REPORT.md` (format in §7).

## 1. Environment setup

```bash
cd <repo-root>            # openswmm.mcp
python -m venv .venv-paces && source .venv-paces/bin/activate
pip install -e '.[dev]'
pip install -e '../openswmm.engine'        # or: pip install openswmm
pip install -e '../openswmm.gymnasium[platypus]'
python -c "import openswmm.engine, openswmm_gymnasium, platypus; print('deps OK')"
```

Record exact versions (`pip freeze | grep -i -E 'openswmm|gymnasium|platypus|fastmcp|pydantic'`)
in the report. If the engine import fails, stop and report — nothing else can run.

## 2. Stage A — full unit suite (regression guard)

```bash
pytest tests/unit -p no:cacheprovider -q 2>&1 | tee tests/_paces/stage_a_unit.log
```

Expected: **0 failures, 0 errors**; near-zero skips now that the engine and gym
extras are installed. Pay attention to `test_tool_registration.py` — it imports
the whole server graph and asserts all 21 namespaces and every `gym_*` tool name.

## 3. Stage B — gym integration tests (the main event)

```bash
pytest tests/unit -k gym -m integration -p no:cacheprovider -v \
  2>&1 | tee tests/_paces/stage_b_gym_integration.log
```

Expected per file:

| File | What must hold |
|---|---|
| `test_gym_config.py` | All four env types build; RTC episode truncates at `max_episode_steps`; CIP spaces mirror factory bounds; unknown element IDs fail loudly. |
| `test_gym_envs.py` | `gym_validate_env_config` reports `observation_size == 3` for the 2-node+1-link fixture spec. |
| `test_gym_runs.py` | Constant/replay rollouts write `trajectory.jsonl` + `summary.json`; reward components sum to the sign-flipped total; interactive loop matches a directly-built env **step-for-step**; capacity/duplicate/idle-sweep behaviors. |
| `test_gym_jobs.py` | Background random search completes with monotone progress; cancel lands `state == "cancelled"`; grid covers `[1.0, 2.0, 3.0]`; NSGA-II front is mutually non-dominated. |
| `test_gym_scoring.py` | All 7 indicators match direct `openswmm_gymnasium.scoring` calls; `apply_design` mutations read back from a live session within `rel=1e-5`. |

Run the suite **twice**. Engine handle leaks, file-lock contention, or thread
issues often only show on the second pass. Note any timing: a single CIP
evaluation is a full 6 h simulation at 15 s routing steps (~1440 steps), so
`test_gym_jobs.py` is legitimately the slowest file — minutes, not seconds.
Anything stuck > 10 min is a hang: capture `py-spy dump --pid <pid>` if
available, else `SIGQUIT` traceback, then kill and report.

## 4. Stage C — concurrency shake-out (beyond the test suite)

Write throwaway scripts in `tests/_paces/` (do not add to `tests/unit/`):

1. **Parallel jobs:** start two `random_search` jobs (budget 4 each) through
   `JobManager(max_workers=2)` simultaneously; both must finish `done`, and
   their `evaluations.jsonl` files must not interleave or corrupt.
2. **Job + interactive env concurrently:** while a job runs, open an
   interactive env, step it 5 times, close it. Both must succeed (two Solver
   handles coexisting).
3. **Restart persistence:** create a config via `GymStore`, instantiate a fresh
   `GymStore` over the same directory, confirm round-trip — then hand-corrupt
   the JSON and confirm the actionable `VALIDATION_ERROR`.

## 5. Stage D — live MCP server smoke (natural-language path)

Register the server with an MCP client (or drive it with a stdio JSON-RPC
script) and run the §"Walkthrough" sequence from
`docs/user-guide/optimization.md` against `tests/data/site_drainage_example.inp`:

`gym_list_capabilities` → `gym_create_env_config` (the cip-study config) →
`gym_validate_env_config` → `gym_run_episode` (baseline) →
`gym_start_optimization` (`random_search`, budget 4) → poll `gym_get_job` →
`gym_get_job_results` → `gym_score_front` (hypervolume + spread,
`reference_point=[1e9,1e9]`) → `lifecycle_open_model` → `gym_apply_design` →
`query_get_link_info` on C1 to confirm the roughness changed →
`building_write_model` to a new `.inp` in `tests/_paces/`.

Verify the written `.inp` actually contains the optimized roughness values.

## 6. Known constraints (don't misreport these)

- `WET_STEP == ROUTING_STEP` matters for single-step forcing assertions; the
  bundled fixture's runoff clock updates every 4th routing step. Gym tests
  were written with this in mind — don't "fix" cadence-related observations.
- `ForecastObservation` is intentionally absent from `gym_list_capabilities`
  (callable param, not declarative).
- `gym_env_step` on a config without runtime factories legitimately accepts an
  empty runtime dict (midpoint of an empty space).
- Engine wheels are macOS-only; that is expected, not a bug.

## 7. Report format (`tests/_paces/REPORT.md`)

For each stage: pass/fail counts, wall time, and for every failure — test name,
one-paragraph symptom, the relevant log excerpt (≤ 30 lines), and your best
single-sentence hypothesis (engine vs gym package vs MCP layer vs test bug).
End with a ranked list of anything that blocks declaring the gym surface
production-ready, and the `pip freeze` excerpt from §1. Do not attempt fixes;
the follow-up session will triage from your report.
