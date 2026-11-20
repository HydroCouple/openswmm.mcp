---
name: verified-narrative
description: "Use this skill when writing any summary, memo, report or narrative about SWMM simulation results that someone else will rely on, and every number in it must be traceable to the engine's output. Triggers include 'verified summary', 'cite the numbers', 'checkable report', 'tally', 'audit the results', or a write-up for a client, regulator or reviewer. Requires the ledger toolset (OPENSWMM_MCP_TOOLSETS must include ledger, e.g. core,ledger). Do NOT use for exploratory chat about results, or when no finished run exists."
---

# Write a narrative whose numbers are verified against the run

## Overview

A narrative is only as trustworthy as its numbers. This skill commits the facts
of a finished run to a tally (a salted hash tree), has you cite a fact id after
every number, and checks the text claim by claim with the `tally` tool. The
result is a narrative a reviewer can trust, plus a root they can check later
without the model.

Requires the ledger toolset: start the server with
`OPENSWMM_MCP_TOOLSETS=core,ledger`. If the `tally` tool is absent, stop and
tell the user to enable it; do not write an unverified narrative in its place.

## Workflow

1. **Run to the end.** `open_model` if needed, then `run` (reuse a session whose
   state is already ENDED). → verify: `session(action="state")` says ENDED.

2. **Commit.** `tally(action="commit", session_id=...)`. Note `tally_id`
   (`<session_id>/<label>`), `root`, and any `dropped` attributes (facts the
   engine could not supply; do not write about those).

3. **List the facts.** `tally(action="facts", tally_id=...)`; narrow with `kind=`
   or `ids=`. Each row has `id`, `entity`, `attr`, `value`, `unit`, `scope`.
   → verify: you can name the fact id for every number you intend to state.

4. **Write the narrative.** Put a citation `[f:kind:entity:attr]` (the fact `id`
   verbatim, e.g. `[f:link:C2:max_flow]`) immediately after every number.
   - Quote the fact's `value` and `unit` exactly. Never convert units, and never
     write "approximately" or round harder instead of citing.
   - Material diagnostics must be cited or dismissed: every diagnostic fact
     (`scope="diagnostic"`) whose value is not 0, and every
     `*continuity_error_pct` fact whatever its value. Cite each, or say in the
     text that you set it aside, naming its id and why.

5. **Build claims.** One entry per citation:
   `{"fact_id": "f:link:C2:max_flow", "value": 14.2}`, with `value` the number
   as written in the text. Add `"unit"` only to assert the unit; a different
   unit is reported as a mismatch, not converted.

6. **Verify.** `tally(action="verify", tally_id=..., claims=[...], narrative=...)`.
   Read `summary.status`, `results[*].verdict`, `coverage.uncited` and `bare`.
   - `verified`: done.
   - `verified_with_gaps`: cite each fact in `coverage.uncited` (or dismiss it
     by id), and cite or remove each number in `bare`.
   - `failed`: fix every `altered` or `unsupported` claim from the closest
     matches returned.
   Fix the narrative, never the tally, and verify again until `verified`.

7. **Close with the footer** built from the last verify result:
   `verified against tally <tally_id>, N claims, coverage X (root <root>)`,
   where N is the number of claims you sent and X is `summary.coverage`.

## Guardrails

- Cite only ids that appear in `facts`; never invent an id.
- Do not recommit to make a failing narrative pass.
- A reviewer holding only the root can check single facts offline:
  `tally(action="prove", tally_id=..., fact_ids=[...])` writes `proof_files`
  and returns the `commands` to run.
