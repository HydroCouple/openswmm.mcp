---
name: model-qa-check
description: "Use this skill when the user wants to sanity-check a model for authoring errors before trusting any other analysis -- 'check this model for errors', 'is this model set up correctly', 'sanity check this .inp', 'QA this model', or when you (the assistant) are about to run a substantive workflow (capacity-assessment, calibrate-model, design-storm-check) on a model you haven't seen before and want to catch broken input before spending a simulation run on it. Triggers include 'sanity check', 'QA', 'validate this model', 'check for errors', 'disconnected nodes', 'is this model broken', or 'why won't this model run'. Do NOT use for interpreting simulation RESULTS (flooding, capacity, hazard) -- this only checks the model's own consistency, before or without running it."
---

# Model QA / sanity check

## Overview

Catches common authoring errors in a SWMM model before the user (or another
skill) spends time trusting results built on a broken input: disconnected
nodes, non-physical link geometry, missing outfalls, and implausible
parameter values. This is a diagnostic pass over the model definition, not
an analysis of simulation results -- it can run with or without a completed
simulation.

## Inputs you must confirm before starting

- Path to the `.inp` model file.
- Whether to open leniently (recommended default: `lenient_open=True`) so a
  model with parse-time problems can still be inspected instead of failing
  outright. Ask only if the user explicitly wants strict-open behavior.

## Workflow

1. **Open leniently and check parse diagnostics first.**
   `lifecycle_open_model(..., lenient_open=True)`, then
   `lifecycle_get_open_diagnostics` for engine-recorded open-time errors and
   warnings. Report these before anything else -- they're the engine's own
   assessment and take priority over anything found below.
   → verify: errors/warnings lists retrieved (empty is good news, not a
   skipped check).

2. **Run the engine's built-in validator.** `building_validate_model`. Report
   any messages returned; a model with none is "engine-valid" but can still
   have the modeling-judgment issues below.

3. **Get the system inventory.** `query_get_system_summary` for element
   counts. Flag immediately if `outfall_count` (or equivalent) is zero -- a
   model with no outfall cannot route flow out and will not simulate
   meaningfully.

4. **Check for disconnected nodes.** `query_get_node_info` with no `node_id`
   (all nodes) and inspect `degree` per node -- `degree == 0` means the node
   has no connecting links, i.e. it's isolated and can never receive or pass
   flow. List every such node.

5. **Check link geometry for non-physical values.** `query_get_link_info`
   (all links) and flag, per link:
   - `slope <= 0` for conduits (zero or reverse-grade -- often a genuine
     design case for backwater-controlled pipes, but always worth surfacing,
     not silently ignoring).
   - `length <= 0`.
   - Crown below invert (if both `crown_elev`/`offset` fields available via
     `query_get_node_info` at each end) -- a physically impossible cross
     section placement.

6. **Check parameter plausibility.** Spot-check against typical engineering
   ranges and flag outliers rather than hard-failing on them (these are
   judgment calls, not hard errors):
   - Manning's roughness outside roughly 0.008-0.15 (pipe material dependent)
     via `links_get_xsect` / conduit roughness in `query_get_link_info`.
   - Subcatchment % imperviousness outside [0, 100] or area ≤ 0 via
     `query_get_subcatchment_info`.
   - Node `invert_elev` above `crown_elev`, or `max_depth` ≤ 0.

7. **Cross-check rain gages have a real data source.** `query_get_gage_info`
   for each gage -- flag any gage with no timeseries/file/station configured,
   since subcatchments draining to it will see zero rainfall silently.

## Reporting requirements

A findings table: category (parse error/warning, validator message,
disconnected node, geometry issue, parameter outlier, gage misconfiguration),
element ID, the specific value/message, and a severity (error = will break or
invalidate a run; warning = worth the user's attention but not necessarily
wrong). End with a one-line overall verdict: clean / warnings only / errors
found. If errors were found, say plainly that downstream analysis on this
model isn't trustworthy until they're addressed.

## Guardrails

- Don't silently "fix" anything found here -- this skill reports, it doesn't
  edit. If the user wants a finding fixed, confirm the specific change before
  using an `editing_*`/`building_*` tool.
- Distinguish engine-level errors (from steps 1-2, authoritative) from your
  own judgment-based flags (steps 5-7, worth surfacing but not necessarily
  wrong) -- don't present a plausibility flag with the same certainty as a
  parser error.
- A zero/negative conduit slope is not automatically wrong (backwater-
  controlled and tidal-influenced pipes are legitimately flat or reverse-
  graded) -- report it, don't call it an error outright.
