# MCP Server v1 Migration Plan

**Date:** 2026-05-27
**Status:** DRAFT — for review before implementation
**Scope:** Align the OpenSWMM MCP server with the new v1 Pythonic surface of `openswmm.engine` (per `openswmm.engine/docs/PYTHONIC_BINDINGS_DONE.md`).
**Authoring guidance:** `CLAUDE.md` §1 (think before coding), §3 (surgical), §4 (verifiable goals), §5.0 (preconfigured plan).

---

## 1. Goals

1. Make the MCP server import and run cleanly against the new `openswmm.engine` v1 surface.
2. Rewrite MCP tools to call v1 idioms directly (container protocol, property-style wrappers, native datetime / IntEnum / pathlib at the boundary, exceptions instead of return codes).
3. Preserve legacy SWMM-5 parity: every tool the legacy backend currently supports keeps working.
4. Add new MCP tools for v1-only capabilities: `Solver.steps()` / `until()`, `Forcing` (mode + persistence), `ModelEditor` (delete / convert), and `StaleObjectError` handling.

## 2. Non-Goals

1. No engine-internal C++ changes.
2. No expansion of legacy-backend coverage beyond what it already supports.
3. No refactor of unrelated MCP infrastructure (auth, session manager, resources, prompts).
4. No new MCP tool categories beyond what v1 enables.

---

## 3. The fundamental shape change

The v0 surface was indexed-getter style: `nodes.get_depth(idx)`, `nodes.get_stat_max_depth(idx)`. The v1 surface is container + property style: `solver.nodes[id].depth`, `solver.nodes[id].stats.max_depth`.

This is canonical. The translation table in §5 is authoritative — every MCP rewrite refers back to it.

The v1 surface also adds:
- `len(solver.nodes)` replacing `nodes.count()`
- bulk numpy *properties* (`solver.nodes.depths`) replacing bulk *methods* (`nodes.get_depths_bulk()`)
- sub-views (`node.stats`, `node.storage`, `node.outfall`, `link.pump`, `link.weir`, `subcatchment.infiltration`, …) that raise `AttributeError` on wrong-type objects
- generation counter — wrappers minted before topology mutation raise `StaleObjectError`
- collection accessors directly on the solver: `solver.nodes`, `solver.links`, `solver.subcatchments`, `solver.gages`, `solver.pollutants`, `solver.tables`, `solver.patterns`, `solver.inflows`, `solver.controls`, `solver.forcing`, `solver.infrastructure`, `solver.spatial`, `solver.quality`, `solver.statistics`, `solver.mass_balance`, `solver.editor`, `solver.save_schedule`

## 4. Backend strategy

Because tools call `session.nodes.<...>` and both backends sit behind that name, the simplest correct path is: **both backends expose the same v1-shape facade**.

### 4.1 `backends/openswmm.py`

The current implementation caches per-domain factory results. The v1 solver already exposes `solver.nodes`, `solver.links`, etc. as properties. The backend can collapse to a thin pass-through:

```python
def __getattr__(self, name):
    # nodes, links, subcatchments, gages, pollutants, tables, patterns,
    # inflows, controls, forcing, infrastructure, spatial, quality,
    # statistics, mass_balance, editor, save_schedule
    return getattr(self._solver, name)
```

Domain factory dict is removed.

### 4.2 `backends/legacy.py`

Today the legacy backend's `_LegacyNodes`/`_LegacyLinks`/`_LegacySubcatchments` expose v0-style methods (`get_depth(idx)`, etc.). We need to add v1-shape methods *that the rewritten tools will call*. The legacy backend will not implement everything (no sub-views like `.storage`, no Forcing, no Editor, etc.) — `require_new_engine()` already guards against that.

What legacy *must* keep working (per "keep legacy parity"):
- `len(session.nodes)`, `len(session.links)`, `len(session.subcatchments)`, `len(session.gages)`, `len(session.pollutants)`
- `session.nodes[key]` returning a wrapper with `.id`, `.index`, `.type`, `.depth`, `.head`, `.volume`, `.lateral_inflow`, `.overflow`, `.invert_elev`, `.max_depth`
- equivalent for `links[key]` (basic state + topology)
- equivalent for `subcatchments[key]` (basic state)
- iteration over each collection
- `session.forcing.node_lat_inflow(...)` minimal one-shot path (legacy maps to `set_value(LATERAL_INFLOW)`)
- `session.mass_balance.runoff_continuity_error`, `.routing_continuity_error`, `.quality_continuity_error(p)`

Anything else: `require_new_engine` guard at the tool entry-point continues to surface `NOT_SUPPORTED`.

### 4.3 Decision

Make `backends/openswmm.py` a thin pass-through. Extend `backends/legacy.py` with v1-shape wrappers (Container + minimal Node/Link/Subcatchment wrappers). Tools are written against the v1 shape only — they don't branch on backend kind.

## 5. v0 → v1 translation reference (authoritative)

This table is the source of truth used by every per-module rewrite. Items the legacy backend will *not* support are marked `[NEW-ENGINE]`.

### Nodes

| v0 (current MCP call) | v1 equivalent | Legacy support |
|---|---|---|
| `nodes.count()` | `len(solver.nodes)` | yes |
| `nodes.get_id(i)` | `solver.nodes.get_id(i)` (also `solver.nodes[i].id`) | yes |
| `nodes.get_index(s)` | `solver.nodes.get_index(s)` (also `s in solver.nodes`) | yes |
| `nodes.get_depth(i)` | `solver.nodes[i].depth` | yes |
| `nodes.get_depths_bulk()` | `solver.nodes.depths` (np.ndarray) | no |
| `nodes.get_heads_bulk()` | `solver.nodes.heads` | no |
| `nodes.get_inflows_bulk()` | `solver.nodes.inflows` | no |
| `nodes.get_overflows_bulk()` | `solver.nodes.overflows` | no |
| `nodes.set_depths_bulk(arr)` | `solver.nodes.depths = arr` | no |
| `nodes.set_lat_inflows_bulk(arr)` | `solver.nodes.set_lateral_inflows(arr)` | no |
| `nodes.get_quality_bulk(p)` | `solver.nodes.qualities(p)` | no |
| `nodes.get_stat_max_depth(i)` | `solver.nodes[i].stats.max_depth` | no |
| `nodes.get_stat_max_overflow(i)` | `solver.nodes[i].stats.max_overflow` | no |
| `nodes.get_stat_vol_flooded(i)` | `solver.nodes[i].stats.vol_flooded` | no |
| `nodes.get_stat_time_flooded(i)` | `solver.nodes[i].stats.time_flooded` | no |
| `nodes.get_storage_curve(i)` | `solver.nodes[i].storage.curve` | no |
| `nodes.set_storage_curve(i, c)` | `solver.nodes[i].storage.curve = c` | no |
| `nodes.get_storage_functional(i)` | `solver.nodes[i].storage.functional` | no |
| `nodes.set_storage_functional(i, a, b, c)` | `solver.nodes[i].storage.functional = (a, b, c)` | no |
| `nodes.get_storage_seep_rate(i)` | `solver.nodes[i].storage.seep_rate` | no |
| `nodes.set_storage_seep_rate(i, r)` | `solver.nodes[i].storage.seep_rate = r` | no |
| `nodes.get_exfil_params(i)` | `solver.nodes[i].storage.exfil_params` | no |
| `nodes.set_exfil_params(i, s, k, im)` | `solver.nodes[i].storage.exfil_params = (s, k, im)` | no |
| `nodes.get_outfall_type(i)` | `solver.nodes[i].outfall.type` | no |
| `nodes.set_outfall_type(i, t)` | `solver.nodes[i].outfall.type = t` | no |
| `nodes.get_outfall_param(i)` | `solver.nodes[i].outfall.param` | no |
| `nodes.set_outfall_stage(i, s)` | `solver.nodes[i].outfall.set_stage(s)` | no |
| `nodes.set_outfall_tidal(i, c)` | `solver.nodes[i].outfall.set_tidal_curve(c)` | no |
| `nodes.set_outfall_timeseries(i, ts)` | `solver.nodes[i].outfall.set_timeseries(ts)` | no |
| `nodes.get_outfall_flap_gate(i)` | `solver.nodes[i].outfall.flap_gate` | no |
| `nodes.set_outfall_flap_gate(i, b)` | `solver.nodes[i].outfall.flap_gate = b` | no |
| `nodes.get_outfall_route_to(i)` | `solver.nodes[i].outfall.route_to` | no |
| `nodes.set_outfall_route_to(i, sub)` | `solver.nodes[i].outfall.route_to = sub` | no |
| `nodes.get_divider_type(i)` | `solver.nodes[i].divider.type` | no |
| `nodes.set_divider_type(i, t)` | `solver.nodes[i].divider.type = t` | no |
| `nodes.get_quality(i, p)` | `solver.nodes[i].quality(p)` | no |
| `nodes.set_quality_mass_flux(i, p, mf)` | `solver.nodes[i].set_quality_mass_flux(p, mf)` | no |
| `nodes.get_depth_from_volume(i, v)` | `solver.nodes[i].depth_from_volume(v)` | no |

### Links

| v0 | v1 | Legacy |
|---|---|---|
| `links.count()` | `len(solver.links)` | yes |
| `links.get_id(i)` / `get_index(s)` | `solver.links.get_id(i)` / `get_index(s)` | yes |
| `links.get_flows_bulk()` | `solver.links.flows` | no |
| `links.get_depths_bulk()` | `solver.links.depths` | no |
| `links.set_flows_bulk(arr)` | `solver.links.flows = arr` | no |
| `links.get_quality_bulk(p)` | `solver.links.qualities(p)` | no |
| `links.get_quality(i, p)` | `solver.links[i].quality(p)` | no |
| `links.get_closed(i)` / `set_closed(i, b)` | `solver.links[i].closed` (read/write) | no |
| `links.get_control_setting(i)` | `solver.links[i].control_setting` | no |
| `links.set_control_setting(i, v)` | `solver.links[i].control_setting = v` | no |
| `links.get_target_setting(i)` | `solver.links[i].target_setting` | no |
| `links.set_target_setting(i, v)` | `solver.links[i].target_setting = v` | no |
| `links.get_loss_coeff(i)` | `solver.links[i].loss_coeff` (tuple) | no |
| `links.set_loss_coeff(i, …)` | `solver.links[i].loss_coeff = (…)` | no |
| `links.get_pump_curve(i)` / `set_pump_curve(i, c)` | `solver.links[i].pump.curve` (read/write) | no |
| `links.get_pump_init_state(i)` / `set_pump_init_state(i, s)` | `solver.links[i].pump.init_state` (read/write) | no |
| `links.get_barrels(i)` / `set_barrels(i, n)` | `solver.links[i].barrels` (read/write) | no |
| `links.get_crest_height(i)` / `set_crest_height` | `solver.links[i].weir.crest_height` (read/write) | no |
| `links.get_discharge_coeff(i)` / `set_discharge_coeff` | `solver.links[i].weir.discharge_coeff` (read/write) | no |
| `links.get_end_contractions(i)` / `set_end_contractions` | `solver.links[i].weir.end_contractions` (read/write) | no |
| `links.get_flap_gate(i)` / `set_flap_gate` | `solver.links[i].flap_gate` (read/write) | no |
| `links.get_culvert_code(i)` / `set_culvert_code` | `solver.links[i].culvert_code` (read/write) | no |
| `links.get_seep_rate(i)` / `set_seep_rate` | `solver.links[i].seep_rate` (read/write) | no |
| `links.hyd_power(i)` | `solver.links[i].hyd_power` | no |
| `links.stat_*` (pump_cycles, pump_on_time, pump_volume, max_flow, max_velocity, max_filling, surcharge_time, vol_flow) | `solver.links[i].stats.*` | no |

### Subcatchments, Gages, Pollutants, Tables, Inflows, Controls, Forcing, Infrastructure, Spatial, Quality, Statistics, MassBalance, HotStart, OutputReader, Model, Editor

Per the `.pyi` stubs (see `/python/openswmm/engine/_*.pyi`). The reference is mechanical: pyi attribute → MCP tool. Built incrementally per phase below.

## 6. Phasing

Each phase ships a coherent, testable slice. Stop-points after each phase: run tests, present diffs, await go-ahead before next phase.

### Phase 0 — Plan approval (this document)
- **Stop-point:** user reviews and approves §3–§5 mapping before any code edit.

### Phase 1 — Backend foundation
- Rewrite `backends/openswmm.py` as a thin pass-through to `solver.<domain>`.
- Extend `backends/legacy.py` with v1-shape `__len__` + indexed `__getitem__` + minimal `Node`/`Link`/`Subcatchment` wrappers exposing the attributes legacy tools actually read.
- Add a generation property to the legacy backend (returns `0` always — no editing on legacy).
- **Verify:** `python -c "from openswmm_mcp.backends import make_backend; b = make_backend('openswmm', …); len(b.nodes); b.nodes[0].depth"` works.

### Phase 2 — High-traffic tool migration (nodes, links, subcatchments, query, lifecycle)
- Migrate `tools/nodes.py`, `tools/links.py`, `tools/subcatchments.py`, `tools/query.py`, `tools/lifecycle.py`.
- These are the modules with the most user-facing tool calls and the most call sites against the changed surface.
- **Verify:** existing tool test suite (or a new minimal smoke test if none) passes for these modules.

### Phase 3 — Configuration / domain tools (model, editing, building, pollutants, tables, controls, inflows, forcing, infrastructure, spatial_quality, quality, hotstart, analysis, geopackage)
- Migrate one module at a time, in the order above (model → editing → … → geopackage).
- **Verify:** module-level smoke per migration.

### Phase 4 — New v1-only MCP tools
- `tools/lifecycle.py`: add `step_iterator_run`, `stride`, `until_datetime`, `until_elapsed` tools backed by `Solver.steps()` / `stride(n)` / `until(target)`.
- `tools/forcing.py`: add persistent-forcing tools (current `forcing.py` is mostly one-shot via `set_value`; expose `ForcingMode.REPLACE / ADD` + `persist=True`).
- `tools/editing.py`: add `delete_object` (already exists, verify against `ModelEditor`), `convert_node`, `convert_link` backed by `ModelEditor`; surface `ImpactEntry` / `ConversionResult` in returns.
- New tool `analyze_impact` from `ModelEditor`.
- Add `StaleObjectError → ToolError(NOT_FOUND/STALE)` mapping in `errors.py`.

### Phase 5 — Verification
- Full smoke test suite against a small `.inp` (e.g., one from `openswmm.engine/python/tests/data/`).
- Run any existing pytest suite under `openswmm.mcp/tests/`.
- Build a single end-to-end integration script in `tests/integration/` that opens a model, runs a few steps, exercises one tool per migrated module, and dumps a JSON report to `tests/integration/report.json` (per `CLAUDE.md` §4.1 — user-reviewable artefact).

## 7. Success criteria (per `CLAUDE.md` §4)

| Goal | Verifiable check |
|---|---|
| MCP imports cleanly against v1 engine | `python -c "from openswmm_mcp import server"` exits 0 |
| All migrated tool modules import cleanly | `python -c "from openswmm_mcp.tools import <each>"` exits 0 for each |
| Each migrated tool returns same shape as before for one representative call | per-module smoke records pre/post output comparison |
| Legacy backend still works for its supported tool subset | run query / lifecycle tools against legacy backend → same output as before migration |
| New v1 tools produce expected output for one synthetic scenario | integration script asserts step-iterator yields >0 steps, forcing.replace persists across steps, editor.delete bumps generation, stale wrapper raises ToolError |

## 8. Open questions to resolve before Phase 1

1. **Legacy backend `Node` / `Link` wrapper return shape:** should attributes that legacy can't read raise `AttributeError` (matching v1's `StaleObjectError` ergonomics) or return `None`? Suggested: raise `AttributeError`, since `require_new_engine` guards already prevent these paths on the tool side.
2. **`engine_kind` exposure:** keep the current `backend.engine_kind` string ("openswmm" / "legacy") for `require_new_engine` checks. No changes.
3. **`session.meta` cache:** currently uses `proxy.count` (without parens) on `SessionMeta` (see `session.py:80`). v1 collections use `len(coll)` — the `count` attribute access fails. Will update `SessionMeta` in Phase 1 to use `len(...)`.

## 9. Out-of-scope items (deferred, NOT done in this plan)

- New high-level analysis tools that combine multiple v1 capabilities (e.g., a single "run-and-report" tool).
- Async-safety review of `asyncio.to_thread` wrapping (current pattern preserved unchanged).
- Migration of MCP resources / prompts beyond what tool-name changes require.
- C-API gaps documented in `engine/docs/C_API_BINDINGS_MCP_IMPROVEMENT_PLAN.md` (separate plan).

## 10. Risk register

| Risk | Mitigation |
|---|---|
| Engine wheel not built in this environment — can't run tests | Use `python/smoke_test.py` from engine repo to verify imports; defer full runtime tests to user's machine |
| Legacy backend v1-shape wrappers drift from openswmm v1 | Same test suite runs against both backends in Phase 5 |
| Generation-counter staleness surprises existing callers | Document `StaleObjectError → ToolError` mapping; treat as additive — old patterns that don't mutate topology see no change |
| Hidden v0 calls in modules not in §5 table | Phase-2 / Phase-3 first step is per-module grep against the §5 table; anything missing is added to the table and re-reviewed |

---

**Awaiting approval. No code edits until §3–§5 are confirmed.**

---

## 11. Status log

| Phase | Status | Notes |
|---|---|---|
| 0 — Plan approval | ✅ done | Approved with full scope (rewrite tools + keep legacy parity + surface new v1 capabilities). |
| 1 — Backend foundation | ✅ done | `openswmm.py` collapsed to pass-through (+ `_OpenSwmmHotstart` wrapper added in Phase 4). `legacy.py` extended with v1-shape wrappers (`_LegacyNode/Link/Subcatchment/Gage/Pollutant`), container protocol on each collection, `_LegacyOptionsView` mapping, v1 datetime / timedelta properties on `_LegacySolverAdapter`, v1 mass-balance property accessors. `SessionMeta` `count` / `len` bug fixed. |
| 2 — High-traffic tools | ✅ done | `tools/nodes.py`, `links.py`, `subcatchments.py`, `query.py`, `lifecycle.py` migrated. Sync-helper pattern collapses N `asyncio.to_thread` submissions into one per query for the heavy aggregators. |
| 3 — Domain tools | ✅ done | All 14 remaining tool modules migrated: `model.py`, `editing.py`, `building.py`, `pollutants.py`, `tables.py`, `controls.py`, `inflows.py`, `forcing.py`, `infrastructure.py`, `spatial_quality.py`, `quality.py`, `hotstart.py`, `analysis.py`, `geopackage.py`. Notable: ModelBuilder `add_node`/`add_link`/`add_subcatchment`/`add_gage` still return error codes (not indices); index lookup via `Nodes(builder).get_index(name)` preserved. v1 collection renames: `pollutants.landuse_*` → `landuses.<…>`, `quality.treatment_set` → `set_treatment`, `inflows.ext_inflow_count` → `external_count`, `spatial.get_subcatch_*` → `subcatchment_*`, etc. |
| 4 — New v1-only tools | ✅ done | `tools/lifecycle.py` gained `stride`, `until_elapsed`, `until_datetime`, `run_for_steps`. `tools/forcing.py` gained `set_persistent_forcing`. `errors.py` gained `STALE_OBJECT` code + `translate_stale_object` / `raise_stale_object_as_tool_error` helpers. `editing.py` impact/conversion response shape verified — no code change needed (Phase 3b already covered it). |
| 5 — Verification | ⚠ written, awaiting local run | `tests/integration/test_v1_smoke.py` (pytest, opt-in via `--run-integration`) and `tests/integration/run_v1_smoke.py` (standalone runner writing a JSON report to `tests/integration/reports/`). Can't run inside the workspace sandbox — needs the local engine wheel. |

### How to run Phase 5 verification locally

From the `openswmm.mcp/` directory:

```bash
# Option 1: pytest (skipped by default; opt in)
pytest --run-integration tests/integration/test_v1_smoke.py -v

# Option 2: standalone runner (writes JSON report)
python tests/integration/run_v1_smoke.py
python tests/integration/run_v1_smoke.py --engine legacy   # legacy adapter check
```

The standalone runner exits 0 on success and writes a timestamped JSON report to `tests/integration/reports/`. Open the report to see one entry per check (one per migrated module + the new Phase 4 surfaces).

### Deferred clean-up (not blocking)

1. Remove the v0 transition shim from `backends/legacy.py` (the `get_*(idx)` methods alongside the v1 container protocol) once all callers are confirmed against the v1 surface.
2. Remove the v0 method aliases from `_LegacyForcing` (`subcatch_rainfall = subcatchment_rainfall`).
3. Audit `tests/unit/` for v0 call sites and migrate (separate effort — those tests pre-date the v1 surface).
