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

## Provenance ledger

The provenance ledger answers where a model element came from and what is known
about a place in the model. It uses the same toolset (`OPENSWMM_MCP_TOOLSETS=core,ledger`)
and two tools, `sources` and `locate`. It records three things:

- **Sources:** the files a model was built from (`sources`).
- **Lineage:** edges from model elements to source features, made by one of three
  rules: `id_match` (a key field equals the element id), `nearest` (the closest
  feature within a distance) or `manual` (edges you list).
- **Mentions:** element ids that appear in documents.

Rows are never overwritten. A changed file adds a version, and `retire` ends a
source's validity without deleting it. Every row carries two dates:

- `as_of` is "true at the time": the world time a fact or feature was valid.
- `as_known` is "known at the time": the ledger time when the row was recorded.

### Formats

| Format | What is registered |
|---|---|
| GeoJSON | Feature rows, with their geometry, for lineage and spatial search |
| `.md`, `.txt` | Document rows; element ids mentioned in the text are recorded as mentions |
| `.csv` time series | One row. Its first timestamp is `valid_from`. The data's full time span is kept in the source's metadata, and `valid_to` stays open, so an old record is not treated as retired |
| `.inp` | A model row. Its map units are read from `[MAP]` |
| `.gpkg`, `.shp` | File-only rows (sha256 and metadata, no features). A warning says that feature ingest supports GeoJSON only |
| PDF | Needs `pypdf` (`pip install pypdf`). The refusal names it |

### Coordinate systems

The model's CRS is resolved for each `locate` or `link` call. The first match wins:
a `crs` argument to `locate` (or `params.crs` on `link`), a `crs` given when the
model is registered with `role="model"`, the CRS in the model's spatial section, or
`local:<map units>` from `[MAP] Units`.

| Situation | Result |
|---|---|
| Model with no CRS | Works in `local:<map units>`. The reference model is `local:feet`, and the buffer is in those units |
| Declared CRS on the model (`register(role="model", crs=...)` or `locate(crs=...)`) | Declared sources are reprojected into it |
| GeoJSON with no `crs` member | Its CRS is `unknown` until registered with `crs=...`. No default is assumed |
| Source whose CRS cannot be aligned with the model | Left out of `locate` and listed in `excluded.crs_incompatible`. It is never reprojected by guess |
| `link(rule="nearest")` with an incompatible source | Refused |
| Model in degrees with no CRS | Refused. A buffer in degrees is not a distance |

Every result states `buffer`, `buffer_unit` and `crs` in its `provenance` block.

### Without the extra

Buffer search, `nearest` links and reprojection need the `ledger` extra:

```bash
pip install 'openswmm.mcp[ledger]'
```

The extra adds `shapely>=2.0` and `pyproj>=3.4`. Without it:

- `locate` on an element returns the lineage-linked features and the mentions, with
  a warning that the buffer search was skipped. `provenance.spatial` is `false`.
- `locate` with a `point`, and `link(rule="nearest")`, are refused with a message
  that names the extra.

`register`, `list`, `id_match` and `manual` links, `lineage`, `mentions` and
`retire` work without it.

### Tallies and sources

`tally(action="commit")` also records `sources_root`: a second root over the
sources linked to the model. Each leaf is one source's `source_id`, `sha256` and
`role`. Publish it next to the fact root. A reviewer can recompute it from the
listed entries and the `sha256sum` of each file. The Phase 1 fact root is unchanged.

### Worked example

The test run on the reference model used `local:feet`, a buffer of 25 around
link C2, and the `id_match` rule on `FACILITYID`. The result:

- 4 features returned: three at distance 0.0 (`intersects`) and one at 9.18 ft
  (`within_buffer`).
- Excluded: `outside_buffer` 1; `crs_incompatible` 1 (an EPSG:2236 survey layer,
  slug `crs_mismatch`); `retired` 0; `not_yet_valid` 0.
- 9 model elements within the buffer.
- C2 carries 5 fact ids from the latest tally.
- 1 document mention of C2.
- `provenance`: `buffer` 25, `buffer_unit` "ft", `crs` "local:feet",
  `crs_source` "map_units".

The call sequence:

```text
sources(action="register", path="pipes.geojson", role="network", crs="local:feet")
sources(action="register", path="inspection.md", role="document")
sources(action="link", session_id="mp", source_id="pipes",
        rule="id_match", params={"key": "FACILITYID"})
locate(session_id="mp", element_kind="link", element_id="C2", buffer=25)
```

`locate` returns `elements`, `features`, `mentions`, `excluded`, `warnings` and
`provenance`. Read `provenance` before quoting any distance: it states the unit.

For how the model-provenance skill turns these results into an answer, see
`src/openswmm_mcp/skills/model-provenance/SKILL.md`. It requires the ledger
toolset and ends each answer with the `provenance` block.
