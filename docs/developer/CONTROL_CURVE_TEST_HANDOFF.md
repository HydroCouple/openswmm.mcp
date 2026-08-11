# Handoff: verify & finish the `control_curve` RTC optimization feature

**To:** an agent in an environment where the compiled `openswmm.engine` actually runs.
**From:** the agent that implemented the feature in a sandbox where the engine binary
crashes with `SIGILL` (so **no engine-dependent test could be executed there**).
**Goal:** build/run the full test suites (including the slow engine + MOEA tests),
confirm the spec's acceptance criteria, and fix any residual issues.

The feature adds an **optimizable reactive piecewise-linear (PWL) control policy**
(`control_curve`) that the gym's multi-objective optimizer can tune, tracing an
operational cost curve (e.g. CSO volume vs. flooding volume). Design spec:
`/Users/.../pystorms/pystorms/networks/RTC_control_curve_optimization_SPEC.md`
(also copied as the upload that started this work).

Architectural decision already made and implemented: the policy is wired through the
**existing, vetted `SwmmControlEnv` seam** (the same single-step "policy vector ->
closed-loop episode" path used by `env_type` `market` and `schedule`) and exposed as a
new `env_type: "control_curve"` with a new registry category `policy_factory`. This is
strictly additive — no changes to `SwmmRTCEnv` / `SwmmMORTCEnv`. Do **not** re-architect
this unless a test proves it necessary.

---

## 1. What to build / install first

Two repos are involved (both already on disk):

- `openswmm.gymnasium` — core logic (controller, policy space, PWL math).
- `openswmm.mcp` — registry schema, env-config wiring, optimizer wiring, decode tool.

Set up an environment where the **real engine** imports and runs:

```bash
# Use the LOCAL openswmm.engine build, not a PyPI wheel. In the implementing
# sandbox a mismatched PyPI 'openswmm.engine' was installed, which is the ONLY
# reason the server import and engine enums failed there (see §5). Install the
# engine the way this project normally builds it (editable install of the local
# openswmm.engine repo / its compiled extension).

cd openswmm.gymnasium && pip install -e '.[platypus]'   # platypus-opt for MOEAs
cd ../openswmm.mcp     && pip install -e '.[engine,gym]' # engine + gym extras
pip install pytest pytest-asyncio anyio                  # test deps
```

Sanity-check the engine actually runs before trusting any test result:

```bash
python -c "
from openswmm_gymnasium.benchmarks.b01_twin_tank import SCENARIO_INP
from openswmm_gymnasium._engine import SolverAdapter
a = SolverAdapter(str(SCENARIO_INP), None, None)
a.open(); a.initialize(); a.start()
print('engine OK'); a.close()
"
```

If this prints `engine OK`, the engine tests below are meaningful. If it `SIGILL`s
or core-dumps, you are in the same broken environment as the implementer — stop and
fix the engine build first; nothing engine-related can be validated otherwise.

---

## 2. Files that belong to THIS change

The working tree contains unrelated pre-existing edits. Only the files below are part
of the `control_curve` feature — review/diff just these.

**`openswmm.gymnasium`:**

- `src/openswmm_gymnasium/control/control_curve.py` *(new)* — `ControlCurvePolicy`,
  `CurveAsset`, `ControlCurveController`, `ControlCurveMetricReader`, `pwl_interp`,
  `project_monotonic`.
- `src/openswmm_gymnasium/spaces/control_curve.py` *(new)* — `ControlCurvePolicySpace`
  (validation, labels, bounds, `unflatten`, `decode_curves`).
- `src/openswmm_gymnasium/control/__init__.py`, `spaces/__init__.py` — exports only.
- `tests/unit/test_control_curve.py` *(new)* — pure + engine tests.
- `docs/user-guide/action_spaces.md`, `docs/user-guide/envs.md` — docs.

**`openswmm.mcp`:**

- `src/openswmm_mcp/gym_support/registry.py` — `ControlCurveParams` /
  `ControlCurveAssetParams` models + `policy_factory` category + `control_curve` kind;
  `construct_kind` now refuses `policy_factory` (like wrappers).
- `src/openswmm_mcp/gym_support/config.py` — `PolicyFactorySpec`, `control_curve`
  `env_type`, `policy_factory` field, constraints, `build_env` branch.
- `src/openswmm_mcp/gym_support/jobs.py` — `_control_curve_dimensions`,
  `design_dimensions` branch, `_is_policy_env` includes `control_curve`.
- `src/openswmm_mcp/tools/gym_runs.py` — `gym_decode_policy` tool + relaxed
  `start_optimization` docstring.
- `src/openswmm_mcp/tools/gym_envs.py` — `list_capabilities` env-type list + notes.
- `tests/unit/test_gym_registry.py`, `test_gym_config.py`, `test_gym_jobs.py`,
  `test_tool_registration.py` — tests.
- `docs/user-guide/optimization.md`, `docs/user-guide/tools.md` — docs.

---

## 3. Run the tests

### 3a. Pure tests (already pass in the sandbox — should stay green)

```bash
cd openswmm.gymnasium
python -m pytest tests/unit/test_control_curve.py -q -k "not Engine"
# expect: 33 passed

cd ../openswmm.mcp
python -m pytest tests/unit/test_gym_registry.py tests/unit/test_gym_config.py \
                 tests/unit/test_gym_jobs.py -q -m "not integration"
# expect: all passed (registry/config/jobs control_curve coverage)
```

### 3b. Engine tests (could NOT run in the sandbox — your job to run)

```bash
cd openswmm.gymnasium
# The engine tests in the new file use the b01 twin-tank benchmark (orifice ORIF,
# tank T1). They are the "Engine" TestCase classes.
python -m pytest tests/unit/test_control_curve.py -q -k "Engine" -rA
```

These assert: action space matches the policy; the neutral (all-open) curve is
deterministic and non-trivial; a `nonincreasing` retention curve **reduces**
`uncontrolled_discharge` vs. the open curve; identical params -> identical costs.

### 3c. MCP integration tests (engine + platypus)

```bash
cd openswmm.mcp
python -m pytest tests/unit/test_gym_jobs.py -q -m integration -rA
# Key cases:
#   test_control_curve_nsga2_front_and_decode  -> NSGA-II front + gym_decode_policy roundtrip
#   test_decode_policy_rejects_non_control_curve
#   test_nsga2_produces_nondominated_front     (pre-existing, sanity)
```

### 3d. Full regression (make sure nothing else broke)

```bash
cd openswmm.gymnasium && python -m pytest -q
cd ../openswmm.mcp     && python -m pytest -q
```

---

## 4. Spec acceptance criteria — confirm each (spec §9)

1. `gym_list_capabilities` lists `control_curve` under `policy_factory` with a complete
   `params_schema`. *(pure test covers this; eyeball the tool output too.)*
2. `gym_validate_env_config` on a `control_curve` config reports the decision-space
   **dim and bounds** (via `action_space`). **Known limitation to confirm:** because
   `SwmmControlEnv.reset()` does not open the engine, element-ID resolution
   (`link_id` / `obs_node`) happens at the **first optimization step**, not at validate
   time — same behavior as `market`/`schedule`. Decide with the maintainer whether the
   spec's "validate resolves all asset/obs IDs" needs an eager bind in
   `validate_env_config`; if so, add an optional one-step probe. Don't silently change
   the seam.
3. `gym_start_optimization` accepts an `mo_rtc`-style `control_curve` config and runs
   `nsga2`. *(3c covers this on b01; also try the alpha.inp config in §6.)*
4. `gym_get_job_results` returns a Pareto front (size > 1) whose vectors decode to
   per-asset PWL curves, and the front dominates the neutral (all-open) baseline.
   *(Verify front size > 1 with a real budget; the b01 test only asserts size >= 1.)*
5. Full §7 test suite passes; engine tests confirm neutral ≡ baseline and closed-loop
   reduces CSO.

**Gap to close for criterion 4 + spec §7.6/§7.11/§7.13:** the sandbox tests use the
b01 benchmark and a small budget. On a working engine you should additionally run the
**alpha.inp** worked config (§6) with a realistic budget (NSGA-II budget ~150,
population ~20) and assert: front size > 1, front non-dominated (`gym_pareto_filter`
is a no-op), positive hypervolume, and the front dominates the all-`y=1.0` baseline
point. Add this as an integration test if the maintainer wants alpha.inp coverage
committed (note: `alpha.inp` lives in `pystorms/pystorms/networks/`, not in either
repo's `tests/data/` — copy it into `tests/data/` or point the test at it).

---

## 5. Failures that are ENVIRONMENTAL — do not "fix" these as if they were bugs

In the implementing sandbox these failed for environment reasons, not code reasons.
In a correctly built environment they should pass; if they still fail, fix the
environment, not the feature:

- **`SIGILL` / "Illegal instruction (core dumped)"** on any engine call — the prebuilt
  engine extension used CPU instructions the sandbox lacked. Not a code issue.
- **`test_tool_registration.py::...test_engine_enums_agree`** — asserted
  `ForcingMode.REPLACE == 1` but the sandbox's PyPI engine had `REPLACE = 0`. A
  mismatched engine build. Unrelated to this feature.
- **`ImportError: cannot import name 'BadParamError' from 'openswmm.engine'`** when
  importing `openswmm_mcp.server` — again a mismatched/old engine build. Install the
  correct local engine and this clears.
- **`ModuleNotFoundError: No module named 'plotly'`** collecting `test_viz_figures.py`
  / `test_viz_trajectory.py` — missing optional viz dep; pre-existing, unrelated.

To confirm the tool-registration test now passes with a good engine (it checks
`gym_decode_policy` is registered, which I verified directly):

```bash
cd openswmm.mcp
python -m pytest tests/unit/test_tool_registration.py::TestToolRegistration::test_namespaces_and_new_tools_registered -q
```

---

## 6. Worked alpha.inp `control_curve` config (for a realistic engine run)

Orifices `Or1..Or5` each observe their downstream interceptor node; `nonincreasing`
retention curves; CSO vs. flooding objectives. Decision dim = 5 assets × 4 knots = 20.

```json
{
  "env_type": "control_curve",
  "inp_path": "alpha.inp",
  "control_interval_seconds": 300,
  "policy_factory": {
    "kind": "control_curve",
    "params": {
      "x_normalized": true,
      "assets": [
        {"link_id": "Or1", "obs_node": "JI1",  "x_knots": [0,0.33,0.66,1.0], "monotonic": "nonincreasing"},
        {"link_id": "Or2", "obs_node": "JI2",  "x_knots": [0,0.33,0.66,1.0], "monotonic": "nonincreasing"},
        {"link_id": "Or3", "obs_node": "JI3a", "x_knots": [0,0.33,0.66,1.0], "monotonic": "nonincreasing"},
        {"link_id": "Or4", "obs_node": "JI4",  "x_knots": [0,0.33,0.66,1.0], "monotonic": "nonincreasing"},
        {"link_id": "Or5", "obs_node": "JI5",  "x_knots": [0,0.33,0.66,1.0], "monotonic": "nonincreasing"}
      ]
    }
  },
  "observations": {"node_depths": ["JI1","JI2","JI3a","JI4","JI5"]},
  "reward_terms": [
    {"kind": "cso_volume", "params": {"node_ids": ["JC1b","JC2","JC3a","JC4c","JC5"]}},
    {"kind": "flooding_volume", "params": {}}
  ]
}
```

Flow to exercise end-to-end via the MCP tools:
`gym_create_env_config` -> `gym_validate_env_config` (check dim=20, bounds [0,1]) ->
`gym_start_optimization {algorithm:"nsga2", budget:150, population_size:20, seed:7}` ->
poll `gym_get_job` -> `gym_get_job_results` -> `gym_decode_policy index="best"` and a
few `index="0".."N"` Pareto points.

---

## 7. Residual issues to actively check (and likely fixes)

Work through these on the real engine; each is a place the sandbox couldn't validate.

1. **Neutral curve ≡ uncontrolled baseline (spec §7.6, tolerance ≤0.5%).** The committed
   engine test only checks determinism + "retention < open". Strengthen it: build a
   second baseline run with the orifices left at their `.inp` default (e.g. a `replay`/
   `constant` episode, or an `rtc` env stepped with no control) and assert the neutral
   all-`y_high` curve reproduces `cso_volume`/`flooding_volume` within tolerance.
   - *If they differ:* check the controlled link's **initial/default setting** in the
     model. The "neutral = all open" assumption holds only if the passive structure is
     fully open by default. On b01 confirm `ORIF`'s default setting is 1.0; on alpha.inp
     confirm `Or1..Or5` are effectively fully open. If a structure's default is not 1.0,
     set that asset's `y_init` (and the neutral comparison point) to the actual default,
     or document it.

2. **Observation timing / causality (spec §10).** The convention must be: read state
   **before** applying the new setting within a step. `SwmmControlEnv._apply_control`
   reads `metric_reader.read()` then `controller.compute_settings()` then writes
   `set_target_setting` — confirm this ordering on a live trajectory (dump a few steps).

3. **`x_normalized` divisor.** `ControlCurveMetricReader.bind` normalizes by the observed
   node's `get_max_depth`. Confirm `max_depth > 0` for every `obs_node` on real models
   (there's a guard that falls back to raw if ≤ 0; verify it never silently mis-scales a
   real node). Cross-check that `obs_attr: "depthN"` reading `get_depth` matches the
   model's depth units against the `[0,1]` `x_knots` interpretation.

4. **`control_interval_seconds` vs. routing step.** The controller fires every
   `control_interval_seconds`; ensure that cadence is sane for the model's routing step
   on alpha.inp (300 s is fine for b01). Also confirm the in-controller
   `control_interval_steps` subsampling behaves as intended over a full episode.

5. **Rate limiting (spec §7.7).** The pure test ramps a synthetic observation; confirm on
   a real episode that `|Δsetting| ≤ rate_limit (+eps)` step-over-step when `rate_limit`
   is set.

6. **Decode roundtrip (spec §7.13).** `gym_decode_policy` reconstructs the flat vector
   from the logged `decisions` payload (one component per `control_curve/<link>/y[k]`
   label) and re-applies monotonic projection. Confirm the decoded `(x_knots, y_values)`
   exactly match what was applied during evaluation (projection is deterministic +
   idempotent, so logged-raw -> decode should equal applied).

7. **Pre-existing lint (not introduced here, left untouched per surgical-change rule):**
   `openswmm.mcp/src/openswmm_mcp/gym_support/jobs.py` has a 103-char `E501` line in
   `_schedule_dimensions` (the pre-existing schedule branch, present in `HEAD`). If the
   maintainer wants the file lint-clean, wrap that one line; otherwise leave it.

8. **Lint the new code** (should already pass):
   ```bash
   cd openswmm.gymnasium && ruff check src/openswmm_gymnasium/control/control_curve.py \
       src/openswmm_gymnasium/spaces/control_curve.py tests/unit/test_control_curve.py
   cd ../openswmm.mcp && ruff check src/openswmm_mcp/gym_support/registry.py \
       src/openswmm_mcp/gym_support/config.py src/openswmm_mcp/gym_support/jobs.py \
       src/openswmm_mcp/tools/gym_runs.py src/openswmm_mcp/tools/gym_envs.py
   ```

---

## 8. Definition of done

- §3a–3d all green on a working engine (no `SIGILL`, no skips for the engine/integration
  marks you intend to cover).
- §4 acceptance criteria each confirmed, including the alpha.inp run dominating the
  neutral baseline with a Pareto front of size > 1.
- §7 residual checks resolved or explicitly documented as accepted limitations.
- New files lint-clean; no regressions in the rest of either suite beyond the
  environmental items in §5.

If you change behavior to fix a residual issue, add/adjust the matching test in
`tests/unit/test_control_curve.py` (gym) or `tests/unit/test_gym_jobs.py` (mcp) so the
fix is locked in.
