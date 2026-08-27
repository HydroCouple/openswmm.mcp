---
name: design-storm-check
description: "Use this skill when the user wants a fast pass/fail check of a model against a specific design storm -- e.g. 'run the 10-year storm and tell me if it floods', 'check this against the 100-year event', or any development-review-style question of the form 'does this system handle a design storm of return period X'. Triggers include mentions of 'design storm', 'return period', '<N>-year storm', 'IDF curve', 'SCS Type II', 'Chicago design storm', 'development review check', or 'pass/fail' against a storm event. Do NOT use for a full multi-tier capacity report on the model's existing rainfall (use capacity-assessment), for calibration (use calibrate-model), or for overland/2D hazard mapping (use overland-flood-mapping) -- this skill is specifically about substituting a synthetic design storm and getting a quick verdict."
---

# Design-storm pass/fail check

## Overview

A lightweight, single-storm workflow for development-review-style questions:
build a synthetic design-storm hyetograph, run it against the model, and
report a clear PASS/FAIL against capacity thresholds -- fast, not the full
multi-tier `capacity-assessment` report. No engine tool generates a design
storm; you compute the hyetograph yourself from standard methods and must
show your work.

## Inputs you must confirm before starting

- Path to the `.inp` model file.
- The storm: return period (e.g. 10-, 25-, 100-year), duration (commonly 24 hr
  in North American practice, or match the model's existing storm duration),
  and either a design depth/intensity the user supplies directly, or an IDF
  source (a curve, an equation, or "use NOAA Atlas 14 / local IDF for
  [location]" -- if the user doesn't have IDF data on hand and there's no
  tool to fetch it, ask them for the design depth directly rather than
  inventing one).
- Hyetograph shape/method if not specified: default to **SCS Type II, 24-hr**
  (a common North American default for return-period checks); if the user
  gives a full IDF curve instead of one depth, use the **alternating block
  method** to build the hyetograph. State which method you used.
- Pass/fail thresholds: default to the same conduit/node thresholds as
  `capacity-assessment` (hmax/Hmax ≥ 1.00 = surcharge/fail at that asset;
  any node flood volume = fail at that node) unless the user gives their own
  design criteria.
- Whether to save the storm-substituted model (default: no -- run ephemeral,
  don't persist the substitution unless asked).

If any of these is unknown, ask before running anything.

## Workflow

1. **Open the model.** `lifecycle_open_model`. Note the existing rain gage(s)
   via `query_get_gage_info` (you'll be replacing their data source, not
   deleting them).

2. **Build the design hyetograph.** Compute incremental rainfall (depth per
   timestep, e.g. every 5-15 min) for the chosen duration using the chosen
   method (SCS Type II scaled to the design depth, or alternating-block from
   the supplied IDF curve). Show the total depth and peak intensity you
   computed so the user can sanity-check it against their own IDF source.

3. **Inject it as a time series.** `tables_add_timeseries` with the computed
   `(time_hours, value)` pairs -- the session must still be in an editable
   state (fresh from `lifecycle_open_model`); if `lifecycle_get_simulation_state`
   shows it has already run, close and reopen first. Then
   `editing_configure_gage(gage_id, rain_type=..., data_source="TIMESERIES",
   timeseries_id=...)` to point the gage at it, matching the rain_type/units
   your time series values are in (intensity vs. volume -- check the gage's
   current `rain_type` via `query_get_gage_info` first and match it, or
   convert your series accordingly).
   → verify: `editing_configure_gage` reports the gage now sources from your
   new time series.

4. **Run it.** `lifecycle_run_simulation`. → verify: state reaches `ended`.

5. **Check continuity.** `analysis_get_mass_balance`. If the continuity error
   is large (>5-10%), say the result is unreliable before reporting pass/fail.

6. **Score against thresholds.** `analysis_get_capacity_summary` (per-conduit
   hmax/Hmax) and `analysis_get_flooding_summary` (per-node flood volume).
   Any asset breaching a threshold is a FAIL for that asset; the model as a
   whole is PASS only if nothing fails.

7. **Optionally persist.** Only if the user asks: `building_write_model` to a
   **new** path (never overwrite the original) so the design-storm variant is
   saved for reuse.

## Reporting requirements

- The storm actually used: return period, duration, method, total depth, peak
  intensity -- reproducible, not just "ran a 100-year storm."
- Overall PASS/FAIL verdict, plus the list of failing assets (asset, metric,
  value, threshold) if FAIL.
- The continuity error, and an explicit reliability caveat if it's high.
- Which gage(s) were modified and confirmation the substitution was not saved
  to the original file (unless the user asked to persist it).

## Guardrails

- Never invent a design-storm depth/intensity -- get it from the user or a
  source they name. If they only give a return period with no depth/IDF and
  can't supply one, say you can't proceed without it rather than guessing.
- State the hyetograph method used; a Type-II-vs-alternating-block choice
  materially changes peak intensity and therefore the pass/fail result.
- Don't silently overwrite the user's original `.inp` -- the design-storm
  substitution is ephemeral by default.
- A FAIL on hmax/Hmax ≥ 1.0 means surcharge (full pipe), not necessarily
  surface flooding -- keep that distinction in the verdict language, same as
  `capacity-assessment`.
