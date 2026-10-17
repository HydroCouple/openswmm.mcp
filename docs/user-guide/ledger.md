# Ledger: verifiable run facts

A tally is a split stick: when a run finishes, the server records every fact the
engine produced in a salted hash tree and keeps the stick's public half to share,
while the private half stays with the session. A narrative that cites a fact is
checked against the committed record, so a reviewer can trust the numbers without
trusting the author or the model.

## Enabling

The ledger is an optional toolset. Start the server with:

```bash
OPENSWMM_MCP_TOOLSETS=core,ledger
```

It uses only the Python standard library, so there is nothing to install beyond
the server itself, and it makes no network calls.

## Public and private halves

Each commit produces two things:

| Half | Contents | Share it? |
|---|---|---|
| Public | `tally_id`, `root`, `fact_count`, `meta`, `schema` | Yes. Safe to send to a reviewer. |
| Private | The `.tally.json` file under the session folder, which holds the salt, every fact and its leaf hash | Never. The salt lets anyone rebuild and test every fact. |

`tally(action="root")` and `tally(action="list")` return only the public half.

## Facts and citations

Each fact has an id of the form `f:kind:entity:attr`, for example
`f:link:C2:max_flow`. Cite it in the narrative immediately after the number it
supports:

> Conduit C2 peaks at 14.2 CFS [f:link:C2:max_flow].

`tally(action="facts", tally_id=...)` lists the facts of a committed tally, and
`kind=` or `ids=` narrows the list. Values are binned before they are committed:
most quantities to 3 significant figures, and continuity errors to 0.01 %. A
claim matches when it bins to the same value, so write the number as the fact
reports it.

## Verifying claims

`tally(action="verify", tally_id=..., claims=[...], narrative=...)` checks each
claim `{"fact_id": ..., "value": ...}` and, when a narrative is given, the bare
numbers in it that no claim covers.

Each claim gets one verdict:

| Verdict | Meaning |
|---|---|
| `matched` | The fact exists and the value agrees with it under the fact's binning. |
| `altered` | The fact exists but the value (or a stated unit) does not agree. |
| `unsupported` | No fact has that id. The result lists close matches. |

**Material coverage** is the share of material facts that the narrative cites. A
material fact is every diagnostic fact (a fact flagged as a diagnostic in the
run, such as flooded volume or a convergence count) whose value is not zero, plus
every continuity error whatever its value. Uncited material facts are returned
with their values so you can cite or dismiss them.

**Bare numerics** are numbers in the narrative that no citation covers. The check
is advisory: it lists them with their surrounding text and never fails the run.

The verify result carries one of three statuses:

| Status | When |
|---|---|
| `verified` | No altered or unsupported claims, full material coverage, and no bare numerics. |
| `verified_with_gaps` | No altered or unsupported claims, but some material facts are uncited or some numbers are bare. |
| `failed` | At least one claim is altered or unsupported. |

## Worked example

This example uses `tests/unit/data/site_drainage_model.inp`, opened and run to its
end as session `demo`. The numbers are real output from a run on the reference
model.

**1. Commit the run.** `tally(action="commit", session_id="demo")` returns:

```json
{"tally_id": "demo/site_drainage_model",
 "root": "4584b807862002e58613336593bfdd1f218c91a2c996c2737fdb2d8bc34e605c",
 "fact_count": 135}
```

The root is random for each commit (a fresh salt each time), so your root will
differ from this one. Your `tally_id` and `fact_count` follow the same pattern.

**2. Read the material diagnostics.** This run has three material facts, all
continuity errors:

| Fact | Value |
|---|---|
| `f:pollutant:TSS:continuity_error_pct` | -1.92 % |
| `f:system:system:routing_continuity_error_pct` | 0.03 % |
| `f:system:system:runoff_continuity_error_pct` | -0.11 % |

**3. Verify a narrative that cites everything.** Every number cites its fact, and
the three continuity errors are cited. The result is:

```text
matched 4, altered 0, unsupported 0, coverage 1.0, bare 0 → verified
```

**4. Change one value.** The narrative now says C2 peaks at 15.9 CFS, but the fact
is 14.2 CFS. The claim for `f:link:C2:max_flow` is `altered`:

```json
{"fact_id": "f:link:C2:max_flow", "value": 15.9, "expected": 14.2,
 "policy": {"type": "sigfig", "n": 3}, "delta": 1.7, "verdict": "altered"}
```

The result is `matched 3, altered 1, unsupported 0, coverage 1.0, bare 0 → failed`.
Fix the narrative to 14.2 and verify again.

**5. Drop one continuity error.** The narrative no longer cites
`f:pollutant:TSS:continuity_error_pct`. The claims all match, but one material
fact is uncited:

```text
matched 3, altered 0, unsupported 0, coverage 0.67, bare 0 → verified_with_gaps
```

The uncited fact is returned as `f:pollutant:TSS:continuity_error_pct`, value -1.92
%. Cite it, or say in the text that you set it aside and why, and the status
returns to `verified`.

## Offline proof check

A reviewer does not need the session or the model. `tally(action="prove", ...)`
discloses chosen facts to a file for each id:

```text
tally(action="prove", tally_id="demo/site_drainage_model", fact_ids=["f:link:C2:max_flow"])
```

It returns the `root` and `fact_count`, the disclosure files it wrote under the
session folder, and a `commands` entry for each one. Run the command on the
reviewer's machine:

```bash
python -m openswmm_mcp.ledger verify-proof --root 4584b807862002e58613336593bfdd1f218c91a2c996c2737fdb2d8bc34e605c --count 135 --proof <working_dir>/demo/site_drainage_model.proof.1.json
```

Its output is:

```text
OK f:link:C2:max_flow = 14.2 CFS
```

The reviewer needs only the root and the fact count, which you send from the
public half, and the one proof file. The check recomputes that fact's leaf from
its disclosed line and key and folds the path to the root. A disclosure reveals
that one fact only: the other facts stay hidden behind their own keys, and the
salt never leaves the private file.

## Provenance ledger (coming in Phase 2)

A later release will add a provenance ledger that records the source files and model
edits behind each committed run.
