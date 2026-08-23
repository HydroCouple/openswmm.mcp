# Agent Brief: Test the MCP API Gap-Closure (P1–P5, 2026-06-14)

**Audience:** an autonomous coding agent running on Caleb's Mac with this repo
checked out (the engine wheel only builds on macOS, so these tests *cannot*
run in the Linux authoring sandbox — they have never been executed).
**Goal:** run the new and existing unit tests against the **real**
`openswmm.engine`, exercise the 87 newly-added tools, and **report — not
fix** — anything that fails. If a fix is obvious and tiny (a wrong keyword
name), note it precisely so a human can approve it.
**Context:** `docs/PYTHON_MCP_API_GAP_REVIEW_2026-06-14.md` (the gap analysis,
in the sibling `../openswmm.engine/docs/`) and the implementation summary in
this repo's git diff. Sibling repo expected at `../openswmm.engine`.

## What was implemented

87 new MCP tools closing the functional gaps where Python bindings existed but
no MCP tool exposed them. **Engine Cython bindings were already complete and
were NOT touched** — this is MCP-layer-only work plus one tiny `errors.py`
addition. Files changed:

| Area | File | New tools |
|---|---|---|
| P1 Inflows | `tools/inflows.py` | 17 — DWF/external get·remove·set-baseline·set-scale; hydrograph set-rtk/set-ia/remove-entry/remove-group/rename-group/clear-months/set-gage; remove-rdii; rdii-decay set/remove |
| P2 Transects | `tools/infrastructure.py` | 14 — transect station geometry, bank/encroachment stations, modifiers, comments, roughness read, station-count, remove; street get-params |
| P3 Links | `tools/links.py` | 16 — pump shutoff/startup depth, orifice rate, outlet expon/rating-type, tag get/set, xsect read, control/target/ids bulk getters |
| P4 Nodes/Subcatch | `tools/nodes.py`, `tools/subcatchments.py` | 12 — node tag/head-boundary/outfall-readbacks/ids-bulk; subcatch tag/outlet/ids-bulk; aquifer get/set-param |
| P5 Utilities | `tools/datetime_tools.py` (NEW namespace), `tools/geopackage.py`, `tools/pollutants.py`, `tools/model.py`, `tools/twod.py`, `tools/controls.py`, `tools/forcing.py`, `tools/spatial_quality.py`, `tools/analysis.py`, `tools/editing.py` | 28 |
| Infra | `errors.py` (added `NOT_FOUND`/`BAD_PARAM`), `server.py` (mounted `datetime` namespace) | — |

New test files: `tests/unit/test_datetime.py`, `tests/unit/test_pollutants.py`.
Extended test files: `test_inflows.py`, `test_infrastructure.py`, `test_links.py`,
`test_nodes.py`, `test_subcatchments.py`, `test_geopackage_tools.py`,
`test_tables.py`, `test_model_units.py`, `test_twod.py`, `test_controls.py`,
`test_forcing.py`, `test_spatial_quality.py`, `test_analysis.py`, `test_editing.py`.

## Ground rules

1. **Do not modify `src/` or existing tests to make tests pass.** Your job is
   to verify and report. If a *new* tool or *new* test has a bug, capture the
   evidence; only propose a fix in the report (with the exact line), don't apply
   it without flagging.
2. **No mocks, ever.** All tests run against the real handle-based
   `openswmm.engine.Solver` (repo policy).
3. All artifacts you generate (logs, reports) go in `tests/_gap_paces/` —
   user-reviewable, nothing in temp dirs (CLAUDE.md §4.1).
4. Work the stages in order; later stages assume earlier ones passed.
5. Write the final report to `tests/_gap_paces/REPORT.md` (format in §6).

## 1. Environment setup

```bash
cd <repo-root>            # openswmm.mcp
python -m venv .venv-gap && source .venv-gap/bin/activate
pip install -e '.[dev]'
pip install -e '../openswmm.engine'        # builds the macOS wheel; or: pip install openswmm
python -c "import openswmm.engine, openswmm_mcp; print('deps OK')"
pip freeze | grep -i -E 'openswmm|fastmcp|pydantic|pytest' > tests/_gap_paces/versions.txt
```

If the engine import fails, **stop and report** — nothing else can run. Note the
engine wheel must be current enough to include the bindings these tools call
(2026-06 surface). If a test errors with `AttributeError: 'Inflows' object has
no attribute 'get_external'` (or similar), the installed wheel is stale —
rebuild `../openswmm.engine` and record that, don't treat it as a tool bug.

## 2. Stage A — full unit suite (regression guard)

```bash
pytest tests/unit -p no:cacheprovider 2>&1 | tee tests/_gap_paces/stage_a_full.log
```

`asyncio_mode = auto` is set in `pyproject.toml`, so `async def test_*` run
without decorators. Record pass/fail/skip totals. A green baseline here means
the new tools didn't break existing registration or imports.

## 3. Stage B — the new tests, phase by phase

Run each and capture output. These are the definition-of-done for each phase:

```bash
pytest tests/unit/test_inflows.py -q          2>&1 | tee tests/_gap_paces/b_p1_inflows.log
pytest tests/unit/test_infrastructure.py -q   2>&1 | tee tests/_gap_paces/b_p2_transects.log
pytest tests/unit/test_links.py -q            2>&1 | tee tests/_gap_paces/b_p3_links.log
pytest tests/unit/test_nodes.py tests/unit/test_subcatchments.py -q \
                                              2>&1 | tee tests/_gap_paces/b_p4_node_sub.log
pytest tests/unit/test_datetime.py tests/unit/test_pollutants.py tests/unit/test_geopackage_tools.py \
       tests/unit/test_tables.py tests/unit/test_model_units.py tests/unit/test_twod.py \
       tests/unit/test_controls.py tests/unit/test_forcing.py tests/unit/test_spatial_quality.py \
       tests/unit/test_analysis.py tests/unit/test_editing.py -q \
                                              2>&1 | tee tests/_gap_paces/b_p5_utilities.log
```

## 4. Stage C — tool registration / server wiring

The 87 tools must actually register (not just compile). Confirm the server
builds its tool list and the new `datetime` namespace is present:

```bash
pytest tests/unit/test_tool_registration.py -q 2>&1 | tee tests/_gap_paces/c_registration.log
```

Then a direct count check (no engine needed for registration, but importing the
server imports tool modules which import `openswmm.engine`, so keep the venv):

```bash
python - <<'PY' 2>&1 | tee tests/_gap_paces/c_toolcount.log
import asyncio
from openswmm_mcp.server import mcp
tools = asyncio.run(mcp.get_tools())          # FastMCP API; if name differs, inspect mcp
names = sorted(tools)
print("total tools:", len(names))
for ns in ("datetime","inflows","links","infrastructure","nodes","subcatchments",
           "geopackage","tables","pollutants","model","twod","controls","forcing",
           "spatial","analysis","editing"):
    hits = [n for n in names if n.startswith(ns+"_")]
    print(f"  {ns:14} {len(hits)}")
# Spot-check a few new ones exist:
for t in ("datetime_encode_date","inflows_get_dwf","links_get_pump_startup_depth",
          "infrastructure_get_bank_stations","nodes_get_tag","subcatchments_aquifer_get_param",
          "controls_validate_rule","editing_set_gage_scale_factor"):
    print("  present" if t in names else "  MISSING", t)
PY
```

Expected total ≈ 460 (was 373). If `mcp.get_tools()` isn't the right call,
look at how `test_tool_registration.py` enumerates tools and copy that.

## 5. Likely failure points — scrutinize these first

The tools were authored against the engine `.pyi`/`.pyx` stubs without running
them. The most probable failures are **binding-signature mismatches** (wrong
keyword, property-vs-method, enum code), not logic errors. Check these:

- **P3 links** — pump/outlet/orifice values are properties on *sub-views*
  (`link.pump.startup_depth`, `link.outlet.expon`, `link.orifice.open_close_rate`).
  `set_outlet_rating_type` takes an int code 0–3. `get_xsect` returns
  `(shape, g1..g4)`. Bulk getters use the `links.control_settings` /
  `links.target_settings` / `links.ids` numpy properties.
- **P4 aquifer** — `aquifer_get_param`/`set_param` use an `AquiferParam` IntEnum
  (codes 0–11) exposed through a string-token map (`porosity`…`upper_moisture`).
  If a token is wrong, the param round-trip test will fail. The reference model
  has **no `[AQUIFERS]`**, so that round-trip test is expected to **skip** —
  confirm it skips cleanly rather than erroring.
- **P4 nodes** — `set_head_boundary` is a **RUNNING-state** setter; outfall
  read-backs (`get_tidal_curve`/`get_timeseries`) are methods on `OutfallView`.
  `Subcatchment.tag` is in the `.pyx` but absent from the `.pyi` — verify it
  actually works at runtime.
- **P5 geopackage** — `register`/`is_registered` are **module-level** functions
  in `openswmm.engine._geopackage`, not methods; `query_int`/`query_double` take
  a raw SQL string. These need an open `.gpkg`; if no fixture exists they may
  skip — note which.
- **P5 controls** — `validate_rule` returns `(ok, message)` with **no** line
  number (the C `line_out` is discarded by the binding).
- **P5 forcing** — `set_link_quality` is RUNNING-state and mirrors the existing
  `set_forcing` mode/persist conventions.
- **P5 model** — `get/set_report_start` exchange an **ISO-8601 string** for the
  `report_start_datetime` property.
- **P5 datetime** — pure utilities, no session; should always pass if the engine
  imports.
- **errors.py** — `NOT_FOUND`/`BAD_PARAM` were added so geopackage error paths
  resolve. Confirm no other module re-defines `ErrorCode`.

For each failure, classify it as: (a) **stale wheel** (rebuild engine),
(b) **binding mismatch** (tool calls a name/shape the engine doesn't have —
give the exact engine signature from `../openswmm.engine/python/openswmm/engine/_*.pyi`),
(c) **test bug** (assertion wrong, fixture missing), or (d) **real engine bug**.

## 6. Report format → `tests/_gap_paces/REPORT.md`

```
# MCP Gap-Closure Test Report — <date>

## Environment
- engine version / build, fastmcp, pytest (from versions.txt)

## Results matrix
| Stage | Command | Pass | Fail | Skip | Notes |
|-------|---------|-----:|-----:|-----:|-------|
| A full | ... |  |  |  |  |
| B P1..P5 | ... |  |  |  |  |
| C registration | ... |  |  |  |  |

## Tool count
- total registered (expected ~460), per-namespace breakdown, any MISSING

## Failures (one block each)
### <test id>
- classification: stale-wheel | binding-mismatch | test-bug | engine-bug
- evidence: traceback excerpt
- engine signature (if binding-mismatch): <from the .pyi>
- proposed fix (do NOT apply): file:line + one-line change

## Green summary
- which phases are fully verified against the real engine
```

## 7. Out of scope

- The 39 residual audit "gaps" are intentional (count-via-`__len__`, lifecycle,
  C function-pointer callbacks, per-step runoff I/O, and false-positives where
  `get_ids_bulk`/table tools reach data via a `.ids`/`.points` property). Do not
  add tools for these.
- Do not touch the engine repo or its bindings — they are complete (0 unbound
  symbols, verified by `../openswmm.engine/python/tests/test_api_coverage.py`).
