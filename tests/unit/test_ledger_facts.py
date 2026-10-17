"""Fact extraction from a finished run of the reference model."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError
from openswmm.engine import EngineState

from openswmm_mcp.ledger.facts import extract_facts
from openswmm_mcp.ledger.schema import FACT_SCHEMA, bin_value
from openswmm_mcp.session import SimSession
from openswmm_mcp.tools.model import _open_solver, _start

DATA = Path(__file__).parent / "data"
ACTIVE = (EngineState.STARTED, EngineState.RUNNING)


async def _open(session_manager, output_dir: Path) -> SimSession:
    folder = session_manager.session_dir("ledger")
    inp = folder / "site_drainage_model.inp"
    shutil.copy(DATA / "site_drainage_model.inp", inp)
    session = SimSession(
        "ledger",
        folder,
        inp_path=str(inp),
        rpt_path=str(folder / "site_drainage_model.rpt"),
        out_path=str(folder / "site_drainage_model.out"),
    )
    await session_manager.add(session)
    await session.call(_open_solver, session, False)
    return session


def _finish(session: SimSession) -> None:
    solver = session.require_solver()
    _start(solver)
    while int(solver.state) in ACTIVE:
        solver.step()
    solver.end()
    solver.report()


@pytest.fixture
async def finished(session_manager, output_dir):
    session = await _open(session_manager, output_dir)
    await session.call(_finish, session)
    return session


async def test_extraction_before_run_is_refused(session_manager, output_dir):
    session = await _open(session_manager, output_dir)
    with pytest.raises(ToolError, match="INVALID_STATE"):
        await session.call(extract_facts, session)


async def test_facts_cover_the_schema(finished):
    facts, meta = await finished.call(extract_facts, finished)
    present = {f"{f['kind']}.{f['attr']}" for f in facts}
    dropped = {d["attr"] for d in meta["dropped"]}
    missing = set(FACT_SCHEMA) - present
    assert not missing, missing
    assert not dropped
    for fact in facts:
        assert set(fact) == {"id", "scope", "entity", "kind", "attr", "value", "unit", "time"}
        assert fact["time"] is None
        assert fact["id"] == f"f:{fact['kind']}:{fact['entity']}:{fact['attr']}"
        assert fact["scope"] == FACT_SCHEMA[f"{fact['kind']}.{fact['attr']}"]["scope"]


async def test_ids_unique_sorted_and_stable(finished):
    first, _ = await finished.call(extract_facts, finished)
    second, _ = await finished.call(extract_facts, finished)
    ids = [f["id"] for f in first]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert first == second


async def test_values_are_already_binned(finished):
    facts, _ = await finished.call(extract_facts, finished)
    for fact in facts:
        policy = FACT_SCHEMA[f"{fact['kind']}.{fact['attr']}"]["policy"]
        assert bin_value(fact["value"], policy) == fact["value"], fact["id"]


async def test_reference_anchors_and_units(finished):
    facts, meta = await finished.call(extract_facts, finished)
    by_id = {f["id"]: f for f in facts}
    assert by_id["f:link:C2:max_flow"]["value"] == 14.2
    assert by_id["f:link:C2:max_flow"]["unit"] == "CFS"
    assert by_id["f:link:C2:max_filling"]["unit"] == ""
    assert by_id["f:link:C2:max_velocity"]["unit"] == "ft/s"
    assert by_id["f:node:J1:max_depth"]["unit"] == "ft"
    assert by_id["f:system:system:routing_continuity_error_pct"]["value"] == pytest.approx(
        0.03, abs=0.005
    )
    assert by_id["f:system:system:total_outflow"]["unit"] == "ft3"
    assert by_id["f:pollutant:TSS:continuity_error_pct"]["value"] == pytest.approx(-1.92, abs=0.01)
    assert by_id["f:system:system:nodes_flooded"]["value"] == 0
    assert by_id["f:system:system:links_surcharged"]["value"] == 0
    assert meta["unit_system"] == "US"
    assert meta["flow_units"] == "CFS"
    assert meta["routing_model"] == "DYNWAVE"
    assert meta["report_step"] == 300.0
    assert meta["start"].startswith("1998-01-01")
    assert meta["unavailable"] == []
    assert len(meta["inp_sha256"]) == 64


async def test_model_without_subcatchments(session_manager, output_dir):
    folder = session_manager.session_dir("nosub")
    drop = {
        "SUBCATCHMENTS",
        "SUBAREAS",
        "INFILTRATION",
        "POLYGONS",
        "COVERAGES",
        "LOADINGS",
    }
    lines, keep = [], True
    for line in (DATA / "site_drainage_model.inp").read_text().splitlines():
        if line.startswith("["):
            keep = line.strip("[] ").upper() not in drop
        if keep:
            lines.append(line)
    inp = folder / "nosub.inp"
    inp.write_text("\n".join(lines) + "\n")
    session = SimSession(
        "nosub",
        folder,
        inp_path=str(inp),
        rpt_path=str(folder / "nosub.rpt"),
        out_path=str(folder / "nosub.out"),
    )
    await session_manager.add(session)
    await session.call(_open_solver, session, False)
    await session.call(_finish, session)
    facts, meta = await session.call(extract_facts, session)
    kinds = {f["kind"] for f in facts}
    assert "subcatchment" not in kinds
    assert {"node", "link"} <= kinds
    # engine_compat.report_snapshot works around the engine's zero-subcatchment bug,
    # so the snapshot facts are committed rather than dropped.
    ids = {f["id"] for f in facts}
    assert "f:system:system:routing_continuity_error_pct" in ids
    assert "f:pollutant:TSS:continuity_error_pct" in ids
    assert not [d for d in meta["dropped"] if "snapshot unavailable" in d["reason"]]
