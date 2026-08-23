---
name: calibrate-model
description: "Use this skill when the user wants to calibrate a SWMM model against observed data, tune model parameters to match measurements, or improve agreement between simulated and observed flows/depths/quality. Triggers include mentions of 'calibrate', 'calibration', 'match observed data', 'tune parameters', 'NSE', 'PBIAS', 'goodness of fit', or comparing simulation results to gauge/monitoring data. Do NOT use for building a model from scratch, running a single simulation, or pure results reporting with no parameter adjustment."
---

# Calibrate a SWMM model against observed data

## Overview

Calibration adjusts uncertain model parameters until simulated results
acceptably match observed measurements (typically flow, depth, or pollutant
concentration). This skill defines a disciplined, tool-driven workflow over the
OpenSWMM MCP tools so the procedure is repeatable and auditable.

Follow the steps in order. Do not skip the baseline run — every later judgement
is relative to it.

## Inputs you must confirm before starting

- Path to the `.inp` model file (use `lifecycle_open_model`).
- Source of observed data (a GeoPackage via `geopackage_import_observed_data`,
  or a time series the user provides).
- Which element(s) and attribute(s) are being calibrated (e.g. outfall flow,
  a specific node depth).
- Acceptance criteria. Default targets: Nash–Sutcliffe Efficiency (NSE) ≥ 0.5
  and |PBIAS| ≤ 25% for flow, unless the user specifies otherwise.

If any of these is unknown, ask before running anything.

## Workflow

1. **Open and snapshot the model.** `lifecycle_open_model`, then
   `query_get_system_summary` to record element counts and units. Capture the
   current values of the parameters you intend to change so the baseline is
   reproducible. → verify: model opens, summary returned.

2. **Establish the baseline.** Run `lifecycle_run_simulation`, then pull the
   simulated series with `analysis_get_time_series` for the calibration target.
   Load observed data and compute initial fit with
   `geopackage_compare_sim_vs_observed`. Record baseline NSE/PBIAS.
   → verify: baseline metrics exist and are written to a review file.

3. **Identify sensitive parameters.** Calibrate the few parameters that matter,
   not everything. Typical order for hydrology/hydraulics:
   - Subcatchment width and % imperviousness (`editing_set_subcatchment_properties`)
   - Infiltration parameters (`subcatchments_set_infil_horton` /
     `_green_ampt` / `_curve_number`)
   - Conduit roughness (Manning's n) via `editing_set_link_properties`
   - Storage/routing options via `model_set_option`

4. **Adjust → re-run → re-score, one change set at a time.** Apply a bounded
   change, re-run, and recompute fit. Keep changes within physically plausible
   ranges. If fit improves, keep it; if not, revert. Use
   `hotstart_save_hotstart` / `hotstart_clone_session` to branch experiments
   cheaply. → verify: each iteration's metrics are logged with the parameter
   delta that produced them.

5. **Stop when acceptance criteria are met** or improvements stall (change in
   NSE < 0.01 over an iteration). Do not over-fit.

6. **Write the calibrated model and a report.** `building_write_model` to a new
   `.inp` (never silently overwrite the original), and produce a calibration
   summary table: parameter, baseline value, calibrated value, and the
   before/after NSE and PBIAS.

## Reporting requirements

All intermediate run outputs and the metrics log must be written to a
user-reviewable location (not a temp directory), per project file-IO policy.
The final deliverable is the calibrated `.inp` plus a markdown or spreadsheet
calibration report.

## Guardrails

- Never change a parameter outside its physically reasonable range to chase a
  metric.
- Always keep the original model file untouched; write calibrated output to a
  new path.
- Report both NSE and PBIAS — a good NSE with large PBIAS hides a volume bias.
