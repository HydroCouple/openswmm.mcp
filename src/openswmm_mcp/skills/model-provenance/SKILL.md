---
name: model-provenance
description: "Use this skill when asked where a model element came from or what is known about a place in the model. Triggers include 'where did this pipe come from', 'what do we know about this location', 'which GIS layer built this model', 'what survey or document mentions this node'. Requires the ledger toolset (OPENSWMM_MCP_TOOLSETS must include ledger, e.g. core,ledger). Do NOT use for questions about simulation results; use verified-narrative for those."
---

# Trace a model element or location back to its sources

## Overview

The ledger records the files a model was built from, the links between source
features and model elements, and the documents that mention them. This skill
answers provenance questions from those records only, and ends with the
`provenance` block so a reviewer can reproduce the answer.

Requires the ledger toolset: start the server with
`OPENSWMM_MCP_TOOLSETS=core,ledger`. If the `sources` and `locate` tools are
absent, stop and tell the user to enable it.

## Workflow

1. **List the sources.** `sources(action="list")`. Note each `source_id`,
   `role`, `crs` and validity. → verify: you know which files exist before you
   search them.

2. **Ask for lineage.** For an element:
   `sources(action="lineage", session_id=..., element_kind=..., element_id=...)`.
   Each edge names the `source_id`, `feature_key` and `rule` that linked it.
   → verify: `count` tells you whether any link exists at all.

3. **Locate.** `locate(session_id=..., element_kind=..., element_id=...)`, or
   `point=[x, y]` for a place. Add `as_of` (true at that time) and `as_known`
   (known at that time) only when the user asks about the past. Read
   `elements`, `features`, `mentions`, `excluded` and `provenance`.

4. **Answer** from steps 2 and 3, then end with the `provenance` block
   verbatim.

## Rules

- The first sentence states the buffer, its unit (`buffer_unit`) and the CRS,
  e.g. "Within 25 ft in local:feet, ...".
- Never widen the buffer silently. If you widen it, say so and show the new
  `excluded` counts.
- When `as_of` and `as_known` differ, say which facts were true at the time
  and which were only known later.
- A source in `excluded.crs_incompatible` was left out, not searched. Say so
  with its `reason`, and ask the user for the model CRS; do not pick one.
- If `provenance.spatial` is false, say the buffer search did not run and the
  answer covers lineage and mentions only (install `openswmm.mcp[ledger]`).
- Never claim a feature is linked to an element unless a lineage edge says so.
  A nearby feature is only nearby.
