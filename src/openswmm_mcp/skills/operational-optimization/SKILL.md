---
name: operational-optimization
description: "Use this skill when the user wants to improve how a SWMM system is OPERATED in real time — a market/agent-based reactive controller where stressed assets trade for conveyance, storage, and treatment capacity, with PID-throttled pumps and gates, to reduce flooding and uncontrolled overflow, use storage well, and cut pumping energy. Triggers include 'operational optimization', 'agent-based control', 'market-based control', 'capacity trading', 'reactive control', 'RTC', 'cost curves', 'PID pump/gate control', 'reduce CSO/overflow', 'use storage better', or 'tune the control strategy'. Do NOT use for resizing pipes/structures (capital design), building or calibrating a model, or pure diagnosis (use capacity-assessment first to find the constraints)."
---

# Operational optimization via agent-based capacity trading

## Overview

The base implementation is a **reactive, agent-based market controller**. Each
asset is an agent that, at every control step, prices its own state and **trades
capacity** with other agents: stressed buyers pay to offload, and agents with
spare capacity sell it. The resulting **cost differential** across each
controllable structure drives a **PID** that throttles the connecting pump or
gate. An optional **NSGA-II** pass tunes the cost-curve and PID parameters.

This is the operational follow-on to `capacity-assessment`, which identifies the
constraints and — key here — the **spare capacity that sits idle during stress
events**. The market exists precisely to move load from stressed agents toward
that idle capacity in real time.

It is **multi-objective**: the goals conflict, so report trade-offs, never a
single "optimal".

## Objectives (multi-objective; all minimized as costs)

1. **Flooding** — total node flood volume / duration.
2. **Uncontrolled discharge** — volume through outfalls **not** tagged
   treatment-connected (tag value `TREATED`; same convention as
   `capacity-assessment`).
3. **Storage under-utilization** — penalize storage left unused before spilling.
4. **Pumping energy/cost** — pump runtime/volume, subject to cycling limits.

## Market design

### Commodities

Three tradable capacities: **conveyance** (spare pipe flow capacity),
**storage** (free volume), and **treatment** (spare throughput at `TREATED`
facilities). Prices are quoted in a single normalized currency so trades across
commodities are directly comparable.

### Agents and roles

An agent can be a buyer or a seller depending on its live state:

- **Sellers** offer capacity: conduits with low hmax/Hmax, storages with free
  volume, treatment-tagged facilities with spare throughput.
- **Buyers** demand capacity: junctions near their rim, surcharged conduits,
  near-full storages, and untreated outfalls about to spill.

### Cost curves (normalized to [0, 1])

Each agent maps a **stress metric** to a price in **$0–$1** via a monotonic
curve. Normalization is mandatory so prices are comparable across asset classes
and units:

| Agent class | Stress metric | Price → $1 as… |
|---|---|---|
| Conduit | hmax/Hmax | approaches surcharge |
| Junction | depth / rim (freeboard fraction) | approaches flooding |
| Storage | fill fraction (volume / max) | approaches full |
| Treatment | throughput / capacity | approaches limit |
| Outfall (untreated) | imminent/active discharge | spill begins |

- **Buyer willingness-to-pay** rises as the agent nears its **undesirable
  state** (flood, surcharge, spill) — it bids high to offload.
- **Seller ask price** rises as its **own spare capacity shrinks** — accepting
  more load costs more.
- Curve shape is parameterized: an **onset threshold** (price stays ~0 until the
  metric crosses it), a **steepness**, and a **ceiling** of 1.0. Piecewise-linear
  or logistic; keep all curves on the same [0,1] scale.

### Matching and the cost differential

Each controllable structure (pump, gate, orifice, weir) is a **trade route**
between a buyer-side region and a seller-side region. Its trade signal is the
**cost differential = buyer_price − seller_price**. A positive differential
means relieving the buyer into the seller's spare capacity is worthwhile; the
larger it is, the stronger the call to act.

### PID actuation

One PID per controllable structure. **Error = cost differential**, **setpoint =
0** (drive prices toward equilibrium — i.e. share capacity until buyer and
seller are equally stressed). PID output is the structure **setting ∈ [0,1]**
(gate/orifice opening fraction or pump speed/target). Clamp to [0,1] and to the
operating constraints below; tune `Kp, Ki, Kd` per structure (or shared) — these
are also NSGA-II decision variables.

## Operating constraints (hard limits, never optimized away)

Self-cleansing minimum velocities, maximum allowable surcharge/HGL, pump minimum
cycle time / starts-per-hour, valid setting ranges, and any required minimum
treatment routing. A better score that violates these is not a valid solution.

## Workflow

### 0. Baseline and opportunity

1. `lifecycle_open_model`; run a baseline (`lifecycle_run_simulation` only if
   results are not already `ended`).
2. Reuse/generate `capacity-assessment` outputs to locate hotspots, untreated-
   outfall volumes, and idle-capacity-during-stress. Record the baseline
   objective vector. → verify: baseline objectives saved to a review file.

### 1. Define the market configuration

Author a reviewable JSON config (written to the output folder) specifying: the
agent set per commodity and their stress metrics; each cost-curve's onset /
steepness / ceiling; the trade routes (controllable structure ↔ buyer/seller
regions); per-structure PID gains; the control interval; and the constraint
limits. Resolve all element IDs against the model with the `query_*` tools.
→ verify: every trade route references a real controllable link and two regions.

### 2. Run the reactive control loop

Drive the simulation stepwise, acting each control interval:

1. Initialize and step: `lifecycle_step_simulation` / `lifecycle_stride`
   (auto-starts the solver from `initialized` → `running`).
2. Read live state in bulk: `nodes_get_depths_bulk`, `nodes_get_overflows_bulk`,
   `links_get_depths_bulk`, `links_get_flows_bulk`; derive storage volumes from
   depth + the storage curve.
3. Compute each agent's normalized price from its cost curve.
4. For each trade route, compute the cost differential and update its PID.
5. Apply settings: `links_set_target_setting` (gates/orifices/weirs; requires
   `running`), pump setpoints via `links_set_pump_startup_depth` /
   `_shutoff_depth` / `links_set_target_setting`, or `forcing_set_link_control`
   for rule-style overrides.
6. Advance one control interval and repeat until
   `lifecycle_get_simulation_state` reports completion.

Record per-step time series of prices, differentials, settings, and the running
objectives. → verify: loop completes and constraint compliance is logged.

### 3. Optional — NSGA-II tuning of the market

Treat the **cost-curve parameters (onset/steepness/ceiling) and PID gains** as
the decision vector and search them against the four objectives:

- Inspect support with `gym_list_capabilities`. If the gym env can host the
  market policy (env_type `joint`), express the parameters as design factories
  and run `gym_create_env_config` → `gym_validate_env_config` →
  `gym_start_optimization` (`algorithm: nsga2`, set `budget`/`population_size`)
  → `gym_pareto_filter` / `gym_score_front` → `gym_apply_design`.
- Otherwise run an **outer NSGA-II loop**: per candidate parameter vector, run
  the Tier-2 reactive loop to completion and score the four objectives with the
  `analysis_*` tools; keep the non-dominated set.

Either way the output is a **Pareto set** of market configurations, not one
answer. → verify: the applied configuration reproduces its reported objectives.

### 4. Deliverable

A **single self-contained HTML report** (Plotly figures embedded inline; Plotly
only) in the output folder:

1. Executive summary — baseline vs recommended market config across all four
   objectives, trade-offs stated plainly.
2. Opportunity — the idle-capacity/hotspot findings that motivated the market.
3. Market design — agents, cost curves (plotted), trade routes, PID gains.
4. Reactive run — price/differential/setting time series and trade activity.
5. Tuning (if run) — the interactive Pareto front and the selected config.
6. Methods — objective weights, constraints, and all parameters used this run.

Deliver the **results as the multi-tab interactive dashboard** (see Reference
files): the Pareto front is clickable, and selecting a configuration updates the
objective costs, cost-savings and reduction KPIs, and the linked
baseline-vs-optimized macro and granular time series together. Additional tabs:

7. Cost-benefit / ROI — capital + O&M vs damages/CSO-penalty/energy avoided,
   with payback and NPV.
8. Multi-objective trade-offs — parallel-coordinates across all four objectives;
   selecting a Pareto point isolates its line.
9. Control explainability — actuator-setting and buyer/seller price-differential
   timelines, plus an asset×time capacity-price heatmap (which assets traded
   capacity, and when each gate/pump acted).
10. Robustness — the selected configuration re-evaluated across a storm ensemble,
    with a reliability summary, so a one-storm winner is not mistaken for a
    recommendation.

Persist the market config and tuned parameters as JSON, export the iteration/
Pareto evaluations as CSV, and write the controlled model to a **new** `.inp`
(`building_write_model`) — never overwrite the original.

## Reference files

Start from these bundled templates instead of improvising the config each run
(in this skill's `references/` folder):

- `references/market_config.schema.json` — JSON Schema for the market config.
  Validate every config against it before a run.
- `references/market_config.example.json` — a small worked config (buyers,
  sellers, two trade routes) to copy and adapt.
- `references/control_loop.py` — reference implementation of the cost curves,
  the direct-acting PID (with anti-windup), buyer/seller matching, and the
  reactive loop. The pure logic is complete; its `Adapter` methods mark exactly
  where the MCP tool calls plug in (bulk state read / `links_set_target_setting`
  / `lifecycle_stride`). Reuse this logic for the gym env or the outer NSGA-II
  loop so tuning scores the same controller it ships.
- `references/optimization_dashboard.template.html` — the interactive results
  dashboard (clickable Pareto front → objective/cost-savings panels, reduction
  KPIs, and linked baseline-vs-optimized macro/granular time series). Build it by
  injecting `__PLOTLY_JS__` and `__DATA_JSON__` into the template's placeholders.
- `DASHBOARD.md` — the `DATA` contract the dashboard expects (baseline +
  per-config objectives, costs, savings, macro/granular series, and event
  windows).

## Guardrails

- Keep every cost curve normalized to [0,1]; un-normalized prices break
  cross-commodity comparability and the PID error scale.
- Multi-objective means trade-offs — never present one config as universally
  optimal; show what each objective costs the others.
- Enforce operating constraints as hard limits and clamp PID outputs to valid
  ranges; a constraint-violating result is invalid regardless of score.
- Validate the market config (element IDs, trade routes) before long runs.
- Don't re-run a model that already has `ended` results unless asked.
- Keep the original model untouched; all configs, logs, figures, and the report
  go to the user-reviewable output folder — never temp directories.
