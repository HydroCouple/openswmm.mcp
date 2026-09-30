"""Each bundled skill's tool sequence, run end to end against a real model.

The skills (``src/openswmm_mcp/skills/*/SKILL.md``) tell an agent which tools
to call and what each step must verify. These tests follow those steps in
order through an MCP client, so a renamed tool, a changed argument or a
result that no longer carries what a step reads fails here rather than in a
user's session. The static check that skills only name real tools and fields
is ``test_code_and_resources.py``.
"""

from __future__ import annotations

import asyncio
import csv
import json
import shutil
from pathlib import Path

import pytest
from fastmcp import Client

from .conftest import Tools


@pytest.fixture
async def gym_tools(output_dir: Path, monkeypatch):
    """A client to a server with the gym toolset, as operational-optimization needs."""
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    monkeypatch.setenv("OPENSWMM_MCP_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("OPENSWMM_MCP_TOOLSETS", "core,gym")
    from openswmm_mcp.server import build_server

    async with Client(build_server()) as client:
        yield Tools(client)


async def _results(tools, session_id: str) -> None:
    """Skill step 0: reuse ENDED results, otherwise run to the end."""
    state = await tools("session", action="state", session_id=session_id)
    if state["state"] != "ENDED":
        await tools("run", session_id=session_id, until="end")
    state = await tools("session", action="state", session_id=session_id)
    assert state["state"] == "ENDED"


async def test_calibrate_model(tools, inp_path, output_dir):
    original = Path(inp_path).read_bytes()
    # 1. Open and snapshot.
    await tools("open_model", path=inp_path, session_id="cal")
    summary = await tools("report", session_id="cal", name="summary")
    assert summary
    params = await tools(
        "get", session_id="cal", kind="subcatchment", fields=["width", "imperv_pct"]
    )
    baseline_width = params["columns"]["width"][0]
    sub = params["ids"][0]

    # 2. Baseline run and fit against observed data (synthetic: baseline flow +10%).
    await tools("run", session_id="cal")
    series = await tools(
        "timeseries", source="cal", kind="link", ids=["C1"], variable="flow", max_points=100000
    )
    observed = output_dir / "observed.csv"
    with observed.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "C1"])
        for t, v in zip(series["series"]["C1"]["times"], series["series"]["C1"]["values"]):
            writer.writerow([t, 1.1 * v])
    fit = await tools("compare", a=str(observed), b="cal", kind="link", variable="flow")
    baseline = fit["rows"][0]
    assert {"nse", "rmse"} <= set(baseline)
    (output_dir / "metrics.json").write_text(json.dumps({"baseline": baseline}, indent=2))

    # 3-4. Adjust sensitive parameters on a clone, re-run, re-score.
    await tools("session", action="reset", session_id="cal")
    await tools("session", action="clone", session_id="cal", new_session_id="trial")
    changes = [{"id": sub, "field": "width", "value": baseline_width * 1.2}]
    result = await tools("set", session_id="trial", kind="subcatchment", changes=changes)
    assert all(r["ok"] for r in result["results"]), result
    await tools(
        "call",
        session_id="trial",
        target=f"subcatchment:{sub}.infiltration",
        method="set_horton",
        args={"f0": 4.0, "fmin": 0.2, "decay": 6.5, "dry_time": 7.0},
    )
    link = [{"id": "C1", "field": "roughness", "value": 0.014}]
    result = await tools("set", session_id="trial", kind="link", changes=link)
    assert all(r["ok"] for r in result["results"]), result
    await tools(
        "call",
        session_id="trial",
        target="options",
        method="set_item",
        args={"key": "INERTIAL_DAMPING", "value": "PARTIAL"},
    )
    await tools("run", session_id="trial")
    tuned = (await tools("compare", a=str(observed), b="trial", kind="link", variable="flow"))[
        "rows"
    ][0]
    assert tuned["nse"] != baseline["nse"]  # the change set moved the fit

    # 6. Write the calibrated model to a new path; the original is untouched.
    saved = await tools("save", session_id="trial", path="calibrated.inp")
    assert Path(saved["path"]).is_file()
    assert Path(inp_path).read_bytes() == original


async def test_capacity_assessment(tools, inp_path, output_dir):
    # 0. Results exist.
    await tools("open_model", path=inp_path, session_id="cap")
    await _results(tools, "cap")
    counts = (await tools("describe", session_id="cap"))["element_kinds"]
    n_links, n_nodes = counts["link"]["count"], counts["node"]["count"]

    # 1. Macro assessment.
    balance = await tools("report", session_id="cap", name="mass_balance")
    assert balance
    for name in ("storage", "pumps"):
        await tools("report", session_id="cap", name=name)

    # 2. Granular assessment and geometry.
    capacity = await tools("report", session_id="cap", name="capacity", top=n_links)
    assert "max_filling" in json.dumps(capacity)
    flooding = await tools("report", session_id="cap", name="flooding", top=n_nodes)
    assert flooding is not None
    coords = await tools("call", session_id="cap", target="spatial", method="node_coords")
    assert coords["result"]
    spatial = await tools("describe", topic="spatial")
    assert {"link_vertices", "subcatchment_polygon"} <= set(json.dumps(spatial).split('"'))
    await tools(
        "call", session_id="cap", target="spatial", method="link_vertices", args={"link": "C1"}
    )
    await tools(
        "call", session_id="cap", target="spatial", method="subcatchment_polygon", args={"sub": 0}
    )
    xsect = await tools("get", session_id="cap", kind="link", fields=["xsect.shape", "xsect.g1"])
    assert len(xsect["ids"]) == n_links

    # 3. Temporal analysis.
    system = await tools("timeseries", source="cap", kind="system", ids=[], variable="flooding")
    assert system["series"]["system"]["times"]
    await tools("timeseries", source="cap", kind="node", ids=["J1"], variable="depth")
    await tools("timeseries", source="cap", kind="link", ids=["C1"], variable="flow")

    # Uncontrolled discharge: outfalls and their treatment tags.
    outfalls = await tools("find", session_id="cap", kind="node", type="OUTFALL")
    ids = [row["id"] if isinstance(row, dict) else row for row in outfalls["ids"]]
    assert ids
    await tools("get", session_id="cap", kind="node", fields=["tag"])
    detail = await tools(
        "get", session_id="cap", kind="node", ids=ids, fields=["outfall.type", "outfall.route_to"]
    )
    assert detail["ids"] == ids

    # 4. Deliverable tables.
    exported = await tools(
        "export",
        session_id="cap",
        path="links.csv",
        kind="link",
        fields=["stats.max_filling", "stats.max_flow"],
    )
    assert exported["rows"] == n_links


@pytest.mark.integration
async def test_operational_optimization(gym_tools, output_dir):
    pytest.importorskip("platypus")
    from openswmm_gymnasium.benchmarks.b01_twin_tank import SCENARIO_INP

    tools = gym_tools
    inp = output_dir / "twin_tank.inp"
    shutil.copy(SCENARIO_INP, inp)

    # 0. Baseline.
    await tools("open_model", path=str(inp), session_id="base")
    await _results(tools, "base")
    baseline = await tools("report", session_id="base", name="mass_balance")
    (output_dir / "baseline_objectives.json").write_text(json.dumps(baseline, indent=2))

    # 1. Resolve the controllable structures.
    orifices = await tools("find", session_id="base", kind="link", type="ORIFICE")
    assert orifices["ids"]

    # 2. Reactive control loop, one control interval per iteration.
    await tools("open_model", path=str(inp), session_id="rtc")
    finished, steps = False, 0
    while not finished:
        advanced = await tools("run", session_id="rtc", until="+15m")
        finished = advanced["finished"]
        state = await tools("get", session_id="rtc", kind="node", fields=["depth", "volume"])
        await tools("get", session_id="rtc", kind="link", fields=["depth", "flow"])
        if finished:
            break
        t1 = state["columns"]["depth"][state["ids"].index("T1")]
        setting = 1.0 if t1 > 4.0 else 0.25
        changes = [{"id": "ORIF", "field": "target_setting", "value": setting}]
        result = await tools("set", session_id="rtc", kind="link", changes=changes)
        assert all(r["ok"] for r in result["results"]), result
        steps += 1
        assert steps < 100
    assert steps >= 10  # a 4 h event at 15 min intervals

    # 3. Open-loop schedule search, then apply the chosen design.
    described = await tools("gym_describe")
    assert "schedule" in json.dumps(described)
    spec = {
        "env_type": "schedule",
        "inp_path": str(inp),
        "structure_ids": ["ORIF"],
        "n_points": 4,
        "control_interval_seconds": 900,
        "observations": {"node_depths": ["T1", "T2"]},
        "reward_terms": [
            {"kind": "uncontrolled_discharge", "params": {"link_ids": ["OUT"]}},
            {"kind": "storage_underutilization", "params": {"node_ids": ["T1", "T2"]}},
        ],
    }
    await tools("gym_config", action="create", name="twin", spec=spec)
    validated = await tools("gym_config", action="validate", name="twin")
    assert validated["valid"], validated
    job = await tools(
        "gym_job",
        action="start",
        config="twin",
        optimization={"algorithm": "nsga2", "budget": 8, "population_size": 4, "seed": 3},
    )
    for _ in range(600):
        status = await tools("gym_job", action="status", job_id=job["job_id"])
        if status["state"] in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.2)
    assert status["state"] == "done", status
    pareto = await tools("gym_score", action="pareto", job_id=job["job_id"])
    assert pareto["front"]
    front = pareto["front"]
    reference = [max(row[k] for row in front) * 1.1 + 1.0 for k in range(len(front[0]))]
    scored = await tools(
        "gym_score",
        action="score",
        job_id=job["job_id"],
        indicators=["hypervolume"],
        reference_point=reference,
    )
    assert scored
    applied = await tools("gym_score", action="apply_design", job_id=job["job_id"])
    written = json.loads(Path(applied["schedule_path"]).read_text())
    assert len(written["schedule"]["ORIF"]) == 4
