---
name: calibrate-model
description: "Use this skill when the user wants to calibrate a SWMM model against observed data, tune model parameters to match measurements, or improve agreement between simulated and observed flows/depths/quality. Triggers include mentions of 'calibrate', 'calibration', 'match observed data', 'tune parameters', 'NSE', 'KGE', 'PBIAS', 'goodness of fit', 'uncertainty bands', or comparing simulation results to gauge/monitoring data. Do NOT use for building a model from scratch, running a single simulation, or pure results reporting with no parameter adjustment."
---

# Calibrate a SWMM model against observed data

## Overview

Calibration finds the parameter values for which the model reproduces what was
measured, and says how uncertain they are. This skill runs it as a **study**: a
sampling design scores many parameter sets against the observations, an
acceptance rule keeps the behavioral ones (generalized likelihood uncertainty
estimation), and their runs give prediction bands. The answer is a set of
parameter sets and the spread of what they predict, never one tuned value.

Every step writes to the job's output folder, and the study's manifest lets it be
run again, by seed, to the same evaluations.

Follow the steps in order. Do not skip the baseline: every later judgment is
relative to it.

## Inputs you must confirm before starting

- Path to the `.inp` model file (use `open_model`).
- The observed data: a CSV with ISO datetimes in the first column and one column
  per element id. Keep it **in the model's folder** (for example
  `data/observed.csv`): a study names it relative to the model, and a platform
  revision carries it as a sidecar file.
- Which element and variable are calibrated (an outfall flow, a node depth).
- The parameters to calibrate, each with a physically plausible range.
- Acceptance criteria. Defaults for flow: NSE ≥ 0.5 and KGE ≥ 0.5, with the
  volume bias (PBIAS) within 25 % on the chosen member, unless the user says
  otherwise.

If any of these is unknown, ask before running anything.

## Workflow

1. **Open and snapshot the model.** `open_model`, then
   `report(name="summary")`; record the current values of the parameters you
   will calibrate (`get(kind="subcatchment", fields=["width", "imperv_pct"])`).
   → verify: model opens, summary and values saved to a review file.

2. **Baseline fit.** `run`, then
   `compare(a=<observed.csv>, b=<session>, kind=..., variable=...)`: NSE, KGE,
   RMSE, bias, and peak and volume for both series. PBIAS is
   `100 * (volume_b - volume_a) / volume_a`. → verify: baseline metrics written
   to a review file.

3. **Choose the parameters.** Calibrate the few that matter, as design
   factories (`gym_describe` lists them): `field_design` for any writable
   numeric field by catalog path (`subcatchment.width`, `subcatchment.imperv_pct`,
   `link.roughness`) with `low`/`high`; `field_choice` for a discrete one
   (`choices`). Ranges must be physically plausible.

4. **Declare the study.** An env config (`env_type: "cip"`) whose reward terms
   are `fit_to_observed` terms (`path`, `element_id`, `observed` relative to the
   model, `metric`: `nse`, `kge`, `rmse`, `abs_bias`, `peak_error` or
   `volume_error`). Name the objective(s) in `objectives`; every other term is
   still recorded as a metric of each evaluation. Create and check it with
   `gym_config(action="create")` and `gym_config(action="validate")`.

5. **Sample.** `gym_job(action="start", optimization={"algorithm": "lhs" (or
   "sobol"), "budget": ..., "seed": ..., "acceptance": {"thresholds": [{"metric":
   "nse_<id>", "at_least": 0.5}, {"metric": "kge_<id>", "at_least": 0.5}]}})`.
   The acceptance rule is part of the study, so it travels in its manifest. For
   a multi-objective calibration (for example NSE against volume error) use
   `"nsga2"` with two objectives instead. Poll `gym_job(action="status")`.
   → verify: the job is `done` and every evaluation has its metrics
   (`gym_job(action="results")`).

6. **Behavioral set.** `gym_score(action="behavioural", job_id=...)` applies
   the study's rule (or `rule` to try another) and writes
   `behavioural-set.json`. If no member passes, report it: do not loosen the
   rule silently.

7. **Prediction bands.** `gym_score(action="bands", job_id=...,
   series=["link.flow:<id>"])` runs every member and writes the 5, 50 and 95 %
   quantiles at each report time (and each member's values) to `bands/`. Check
   how much of the observed series falls inside the 5 to 95 % band.
   → verify: one CSV per series exists and its quantiles are ordered.

8. **Write the chosen parameter set.** Pick a member (the best by the
   objective, or one the user prefers) and
   `gym_score(action="apply_design", job_id=..., evaluation=<n>,
   session_id=...)`, then `save` to a **new** `.inp`. When the study ran on the
   platform, `platform_model(action="derive", ...)` makes the calibrated model a
   revision derived from the one the study evaluated, with the study and
   evaluation recorded. → verify: `compare` on the saved model gives the
   member's metrics.

## Reporting requirements

All outputs (the job folder with `evaluations.jsonl`, `study-manifest.json`,
`behavioural-set.json` and `bands/`, plus the calibrated `.inp`) go to a
user-reviewable location, never a temp directory. The report gives: the
parameter ranges searched and the ranges the behavioral set spans; baseline
and chosen-member NSE, KGE and PBIAS; the bands against the observations; and
the acceptance rule used.

## Guardrails

- Never widen a parameter range past what is physically reasonable to reach a
  metric.
- Keep the original model untouched; write the calibrated model to a new path.
- Report a set and its spread, not a single best fit; equifinal parameter sets
  are the expected result.
- Report NSE, KGE and PBIAS together: a good NSE with a large volume bias hides
  an error.
