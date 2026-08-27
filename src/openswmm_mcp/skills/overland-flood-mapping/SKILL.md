---
name: overland-flood-mapping
description: "Use this skill when the user wants to characterize overland/surface flooding from a model's 2D mesh -- flood extent, depth-velocity hazard classification, hot-spot triangles, or inundation mapping. Triggers include mentions of '2D mesh', 'overland flow', 'flood extent', 'inundation map', 'depth x velocity', 'DxV hazard', 'flood hazard classification', 'surface flooding', or asking how far/deep floodwater spreads outside the pipe network. Requires the model to have an active 2D surface ([2D_*] sections) -- do NOT use on a 1D-only model; for 1D node/link flooding and capacity questions instead, use `capacity-assessment`."
---

# Map overland flood extent and hazard from a 2D mesh

## Overview

SWMM's 2D solver tracks depth and velocity per mesh triangle. This skill runs a
2D-coupled simulation, pulls the engine's own cumulative per-triangle
statistics (not a single snapshot in time), and turns them into a flood
extent and depth-velocity (DxV) hazard summary a human can act on --
without hand-rolling depth/velocity extraction from bulk state arrays.

## Inputs you must confirm before starting

- Path to the `.inp` model file (use `lifecycle_open_model`).
- Hazard classification thresholds. Default (unless the user gives their own):
  - **Low**: depth < 0.3 m AND DxV < 0.3 m²/s
  - **Moderate**: depth < 0.6 m AND DxV < 0.6 m²/s (roughly pedestrian/small-vehicle instability)
  - **High**: everything above that
  These are a common depth-velocity hazard convention (similar in spirit to
  Australian ARR / UK Defra guidance), not a specific jurisdiction's adopted
  standard -- say so, and ask if the user has a specific standard to apply
  instead.
- How many hot-spot triangles to report (`top_n`; default 10).

If any of these is unknown, ask before running anything.

## Workflow

1. **Open the model and confirm 2D is active.** `lifecycle_open_model`, then
   `twod_get_mesh_summary`. If `active` is `false`, stop and tell the user
   this model has no 2D mesh -- this skill doesn't apply; suggest
   `capacity-assessment` for 1D-only flooding instead.
   → verify: `active: true`, note `n_vertices` / `n_triangles`.

2. **Run the simulation.** `lifecycle_run_simulation` (runs 1D+2D coupled to
   completion). If the session was already run, close and reopen it first
   (`lifecycle_close_model` → `lifecycle_open_model`) so statistics start
   clean. → verify: simulation completes without error, continuity errors
   reported by the run are small.

3. **Pull cumulative per-triangle hot spots.** `twod_get_stats(top_n=...)` --
   this gives max depth, max velocity magnitude, and max continuity residual
   *over the whole run*, per triangle, already summarized and ranked. This is
   the right tool for hazard mapping; do not reconstruct it by polling
   `twod_get_state`/`twod_get_state_bulk` at individual timesteps, which only
   gives instantaneous values.
   → verify: summary + top-N lists returned for all three variables.

4. **Check global continuity.** `twod_get_mass_balance`. If the continuity
   error fraction is large (>5%, or the user's own tolerance), warn that the
   hazard map below may be unreliable before presenting it as fact.

5. **Get geometry for the hot-spot and flooded triangles.**
   `twod_get_mesh_geometry(offset, limit)` (paginate as needed) to resolve
   each hot-spot triangle's centroid and area -- needed to describe *where*
   the hazard is and to compute flooded area, not just a triangle index.

6. **Classify hazard and compute extent.** For each triangle with max depth
   above a "wet" threshold (default 0.01 m, i.e. any standing water; ask if
   the user wants a higher reporting floor), compute DxV = max_depth ×
   max_velocity and classify into the low/moderate/high bands from the
   Inputs step. Sum triangle areas per band to get flooded area (m²) and
   percentage of total mesh area in each hazard class.

7. **Cross-reference 1D flooding for context.** `analysis_get_flooding_summary`
   -- note which coupled nodes had 1D flood volume, since that's usually
   what's driving the overland spread at those locations.

## Reporting requirements

Write a user-reviewable report (not a temp file) containing:
- Mesh size and whether the run's continuity error was acceptable.
- Flooded area and % of mesh area, broken down by hazard class.
- A hot-spot table: triangle index, centroid (x, y), max depth, max
  velocity, DxV, hazard class -- for at least the requested `top_n`.
- Which hazard thresholds were used (defaults or user-supplied), stated
  explicitly so the classification is reproducible.
- Any coupled 1D nodes flooding at/near the worst 2D hot spots.

## Guardrails

- Never present a hazard classification without stating which thresholds
  produced it -- these are convention, not a universal standard.
- Don't skip the continuity check (step 4) -- a hazard map built on a run
  with a large mass-balance error is misleading, not just imprecise; flag it
  instead of silently reporting numbers.
- `twod_get_stats` reports magnitudes accumulated over the whole simulation,
  not a single instant -- be explicit in the report that "max velocity" means
  "the worst velocity observed at that triangle at any point in the run," not
  a simultaneous depth+velocity snapshot at one timestep. If the user
  specifically needs a simultaneous snapshot (e.g. peak-of-storm state),
  say so and use `twod_get_state_bulk` at a chosen time instead.
