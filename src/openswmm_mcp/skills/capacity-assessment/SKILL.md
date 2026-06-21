---
name: capacity-assessment
description: "Use this skill when the user wants to assess the hydraulic capacity of a SWMM drainage/sewer network: where it floods, where pipes are full or surcharged, how much storage is being used, and where spare capacity exists. Triggers include 'capacity assessment', 'is the system undersized', 'where does it surcharge/flood', 'hmax/Hmax', 'd/D ratio', 'conveyance capacity', 'storage utilization', 'bottlenecks', 'CSO / uncontrolled overflow', or any request for a flooding/surcharge map or capacity report. Do NOT use for building a model, calibrating it, or designing/optimizing controls (use operational-optimization for control tuning)."
---

# Hydraulic capacity assessment of a SWMM network

## Overview

This skill produces a three-tier capacity assessment of a drainage or
collection system: a **macro** system-wide picture, a **granular** per-asset
diagnosis with interactive maps, and a **temporal** analysis that exposes when
the system is stressed and where spare capacity sits idle during those events.
The headline metric for conveyance is the filling ratio **hmax/Hmax**
(max water depth / full depth, a.k.a. d/D), reported by
`analysis_get_capacity_summary` as `max_filling`.

Work the tiers in order — granular and temporal findings are interpreted
relative to the macro baseline. Do not skip the run-state check; every analysis
tool requires completed results.

## Inputs to confirm before starting

- Path to the `.inp` model (open with `lifecycle_open_model`).
- Output folder for the report, figures, and CSVs (must be user-reviewable,
  per project file-IO policy — never a temp dir).
- The constraint thresholds below. Present the defaults and let the engineer
  override any of them for this run before proceeding.
- The outfall **treatment tag** convention (default tag value `TREATED`).
  Outfall nodes carrying this tag are treatment-connected; all other outfalls
  are treated as **uncontrolled** discharges.

If the model path or output folder is unknown, ask before running anything.

## Configurable constraint thresholds (defaults)

These define when an asset is flagged "capacity-constrained". They are
defaults — confirm or override at the start of each run.

| Asset class | Metric | Default: constrained | Default: critical |
|---|---|---|---|
| Conduit | hmax/Hmax (`max_filling`) | ≥ 0.90 | ≥ 1.00 (surcharged) |
| Conduit | surcharge time | > 0 | > 30 min |
| Storage node | peak volume / max volume | > 0.90 | ≥ 1.00 (full) |
| Pump | fraction of time on | > 0.90 | excessive cycling |
| Junction | freeboard to rim | flooded (freeboard ≤ 0) | any flood volume |

Also configurable: `min_flood_volume` (ignore negligible flooding) and
`spare_capacity_filling` (default 0.50) — the hmax/Hmax below which a conduit is
considered to hold *excess* capacity during a stress event.

## Workflow

### 0. Load the model and ensure results exist

1. `lifecycle_open_model` with the `.inp` path.
2. `lifecycle_get_simulation_state` — if the session is not `ended`, run it with
   `lifecycle_run_simulation`. If it is already `ended`, reuse the existing
   results (do not re-run). → verify: state is `ended` before any analysis.

### 1. Macro-level assessment

Establish the system-wide picture from a few aggregate calls:

- `analysis_get_report_snapshot` → flow-routing continuity (total **flooding**
  volume vs. total outflow and inflow, continuity error), the **storage volume
  summary** (per-storage depth/volume), the **link flow summary**, and the
  **pump summary**.
- `analysis_get_mass_balance` → confirm the volume balance closes (flag if
  continuity error is large; results are unreliable above ~5–10%).

Report: total flood volume and % of inflow lost to flooding; aggregate storage
utilization; and the **distribution of hmax/Hmax** across all conduits
(e.g. share of count/length in bands <0.5, 0.5–0.9, 0.9–1.0, ≥1.0). This single
distribution tells the engineer whether the system is broadly undersized or
constrained at isolated bottlenecks. → verify: macro KPIs written to the report.

### 2. Granular per-asset assessment

1. `analysis_get_capacity_summary` (pass `max_filling_threshold` =
   `spare_capacity_filling` to retrieve the broad distribution, then classify in
   code using the threshold table) → per-conduit hmax/Hmax, max flow, max
   velocity, surcharge time.
2. `analysis_get_flooding_summary` → per-node flood volume, max overflow rate,
   time flooded, max depth.
3. Pull storage and pump constraints from the macro snapshot; classify each per
   the threshold table.
4. Build the **constrained-asset register**: every flagged asset with its class,
   the governing metric, its value, and constrained/critical status.

Then map it. Geometry comes from `spatial_get_all_coordinates`,
`spatial_get_all_vertices`, and `spatial_get_all_polygons`
(`spatial_get_model_geometry` for the bundle); cross-section context from
`links_get_xsect`. Produce **interactive Plotly figures** (Plotly only — no
matplotlib/folium):

- Network map with conduits colored by hmax/Hmax (continuous scale, surcharged
  links emphasized) and flooded nodes sized by flood volume.
- Map highlighting the constrained-asset register (bottlenecks, full storages,
  saturated pumps).

→ verify: every flagged asset appears in both the register and a figure.

### 3. Temporal analysis

The goal is to understand *when* the system is constrained and to surface
**excess capacity that sits idle during adverse events** — the core input to
downstream operational optimization.

1. Identify adverse-event windows from system time series:
   `analysis_get_time_series` for the system **flooding** variable (and
   `analysis_output_system_result`) to find when flooding/overflow is active.
2. For the most stressed assets, pull per-element series with
   `analysis_get_time_series` (node depth/flooding, link flow/depth).
3. **Excess-capacity diagnosis:** during each adverse-event window, find
   conduits and storages whose filling stays below `spare_capacity_filling`.
   Spare capacity *concurrent with* flooding elsewhere indicates spatial
   maldistribution — opportunities for diversion, in-system storage, or control
   changes. Tabulate the idle volume/conveyance available during peak stress.
   Build **system-wide available-capacity time series**, one per commodity, on
   the same time base as the flooding series:
   - **Conveyance** — remaining flow capacity, e.g. Σ over conduits of
     `(1 − hmax/Hmax)` weighted by full-flow capacity (from link depth/flow
     series).
   - **Storage** — total free volume, Σ over storage units of
     `(max_volume − current_volume)` (node depth + storage curve).
   - **Treatment** — spare throughput at `TREATED` facilities,
     `Σ (capacity − current_throughput)`.
4. **Uncontrolled-discharge analysis:** identify outfall nodes lacking the
   treatment tag via `nodes_get_tag` (confirm the outfall set with
   `nodes_get_outfall_type` / `nodes_get_outfall_route_to`). For each untreated
   outfall, get its discharge series and integrate the volume released **during**
   flood/overflow windows. Report total uncontrolled release volume and its
   timing relative to system stress.

Produce Plotly time-series figures. The primary figure is a **linked,
shared-x-axis two-panel chart** (`make_subplots(rows=2, shared_xaxes=True)`) so
the two panels pan/zoom together and are read at the same instant:

- **Top panel** — system flooding/overflow hydrograph with adverse-event
  windows shaded, and untreated-outfall discharge overlaid.
- **Bottom panel** — available **conveyance, storage, and treatment** capacity
  (the three series above), so idle capacity is visible directly beneath the
  flooding it coincides with.

Shade the same adverse-event windows across both panels. Also produce
stressed-asset depth/flow series. → verify: the linked panels share one x-axis,
and the idle-capacity table and uncontrolled-release volume are quantified, not
just described.

### 4. Deliverable

Assemble a **single self-contained HTML report** in the output folder with the
Plotly figures embedded inline (e.g. `plotly.io.to_html(..., full_html=False,
include_plotlyjs=...)` so the file opens standalone). Structure:

1. Executive summary — macro KPIs and the top constraints.
2. Macro assessment — flooding, storage utilization, hmax/Hmax distribution.
3. Granular assessment — constrained-asset register + network maps.
4. Temporal assessment — adverse-event timing, idle-capacity table, and
   uncontrolled-discharge volumes (delivered as the interactive dashboard).
5. Hydraulic grade line — animated HGL profiles along key trunk paths
   (invert/crown/HGL/rim vs distance over the storm).
6. Volume & mass routing — Sankey of inflow → storage / conveyed / treated /
   untreated / flooded / infiltration, with continuity error.
7. Duration & velocity — utilization exceedance/duration curves and
   self-cleansing/scour velocity screening.
8. Risk & level-of-service — pass/fail KPI scorecard and an asset criticality
   ranking (peak utilization × duration × consequence).
9. Infrastructure recommendations — auto-generated improvements (conduit
   upsizing / parallel relief, storage/detention sizing, green-infrastructure
   / LID, and an RTC-vs-capital comparison that links to
   operational-optimization), each traced to the diagnostic that triggered it,
   roughly sized, and re-simulated to quantify benefit where feasible.
10. Methods & thresholds — the exact threshold values used this run.

Also export the constrained-asset register, temporal tables, and the
recommendations register as CSV (`analysis_export_results` and/or written
directly) alongside the HTML.

These sections are delivered as the **multi-tab interactive dashboard** (see
Reference files), not just static figures.

## Reference files

In this skill's `references/` folder:

- `capacity_dashboard.template.html` — a self-contained interactive dashboard:
  a clickable network map (click a conduit/node to chart its utilization
  against the global undesired state), a time slider + play that recolors the
  map at time *t* and moves a linked cursor on the time series, an
  adverse-condition banner, and selected-asset chips. Build the dashboard by
  injecting two things into the template's placeholders: `__PLOTLY_JS__`
  (inline `plotly.min.js` for offline use, or a CDN `<script>` tag) and
  `__DATA_JSON__` (`json.dumps(DATA)`).
- `DASHBOARD.md` — the `DATA` contract the template expects (time base,
  event windows, global-state series, and per-node/per-link `util`/`avail`
  series). Compute these from the analysis tools and the available-capacity
  series defined in the temporal step.

## Guardrails

- Never re-run a model that already has `ended` results unless the user asks.
- Check continuity error first; flag the assessment as unreliable if it is high
  rather than reporting capacity numbers as fact.
- hmax/Hmax ≥ 1.0 means the conduit reached full depth, not that flow exceeded
  some design capacity — describe it as surcharging, and distinguish pressurized
  flow from flooding at nodes.
- State the thresholds used in the report; results are meaningless without them.
- Write all figures, tables, and the report to the user-reviewable output
  folder — never temp directories.
