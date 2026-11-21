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
    pytest.importorskip("openswmm_gymnasium")
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    monkeypatch.setenv("OPENSWMM_MCP_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("OPENSWMM_MCP_TOOLSETS", "core,gym")
    from openswmm_mcp.server import build_server

    async with Client(build_server()) as client:
        yield Tools(client)


@pytest.fixture
async def ledger_tools(output_dir: Path, monkeypatch):
    """A client to a server with the ledger toolset, as verified-narrative needs."""
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    monkeypatch.setenv("OPENSWMM_MCP_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("OPENSWMM_MCP_TOOLSETS", "core,ledger")
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


async def _finished(tools, job_id: str) -> dict:
    """Poll a gym job until it ends."""
    for _ in range(1200):
        status = await tools("gym_job", action="status", job_id=job_id)
        if status["state"] in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.2)
    assert status["state"] == "done", status
    return status


def _log(status: dict) -> list[dict]:
    path = Path(status["output_dir"]) / "evaluations.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


async def test_calibrate_model(gym_tools, inp_path, output_dir):
    pytest.importorskip("scipy.stats")  # Latin hypercube: openswmm.gymnasium[sampling]
    tools = gym_tools
    model = Path(inp_path)
    original = model.read_bytes()
    # 1. Open and snapshot.
    await tools("open_model", path=inp_path, session_id="cal")
    summary = await tools("report", session_id="cal", name="summary")
    assert summary
    params = await tools(
        "get", session_id="cal", kind="subcatchment", fields=["width", "imperv_pct"]
    )
    baseline_width = params["columns"]["width"][0]
    sub = params["ids"][0]

    # 2. Baseline fit against observations (synthetic: baseline flow +10%) kept in the
    # model's folder, where a study names them and a revision carries them.
    await tools("run", session_id="cal")
    series = await tools(
        "timeseries", source="cal", kind="link", ids=["C1"], variable="flow", max_points=100000
    )
    observed = model.parent / "data" / "observed.csv"
    observed.parent.mkdir()
    with observed.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "C1"])
        for t, v in zip(series["series"]["C1"]["times"], series["series"]["C1"]["values"]):
            writer.writerow([t, 1.1 * v])
    fit = await tools("compare", a=str(observed), b="cal", kind="link", variable="flow")
    baseline = fit["rows"][0]
    assert {"nse", "kge", "rmse", "volume_a", "volume_b"} <= set(baseline)
    (output_dir / "metrics.json").write_text(json.dumps({"baseline": baseline}, indent=2))

    # 3-4. The parameters as design factories; fit terms as the objective and metrics.
    fit_term = {"path": "link.flow", "element_id": "C1", "observed": "data/observed.csv"}
    spec = {
        "env_type": "cip",
        "inp_path": inp_path,
        "observations": {"node_depths": ["J1"]},
        "design_factories": [
            {
                "kind": "field_design",
                "params": {
                    "path": "subcatchment.width",
                    "ids": [sub],
                    "low": 0.5 * baseline_width,
                    "high": 2.0 * baseline_width,
                },
            }
        ],
        "reward_terms": [
            {"kind": "fit_to_observed", "params": fit_term | {"metric": m}}
            for m in ("nse", "kge", "rmse")
        ],
        "objectives": ["nse_C1"],
    }
    await tools("gym_config", action="create", name="calibration", spec=spec)
    validated = await tools("gym_config", action="validate", name="calibration")
    assert validated["valid"], validated

    # 5. Sample, with the acceptance rule in the study.
    rule = {
        "thresholds": [{"metric": "nse_C1", "at_least": 0.5}, {"metric": "kge_C1", "at_least": 0.5}]
    }
    job = await tools(
        "gym_job",
        action="start",
        config="calibration",
        optimization={"algorithm": "lhs", "budget": 8, "seed": 2, "acceptance": rule},
    )
    status = await _finished(tools, job["job_id"])
    assert all(set(r["metrics"]) == {"nse_C1", "kge_C1", "rmse_C1"} for r in _log(status))

    # 6. Behavioral set.
    accepted = await tools("gym_score", action="behavioural", job_id=job["job_id"])
    assert accepted["members"] and Path(accepted["path"]).is_file()
    assert all(m["metrics"]["nse_C1"] >= 0.5 for m in accepted["members"])

    # 7. Prediction bands.
    bands = await tools("gym_score", action="bands", job_id=job["job_id"], series=["link.flow:C1"])
    with Path(bands["files"]["link.flow:C1"]).open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == bands["report_times"] > 0
    assert all(float(r["q0.05"]) <= float(r["q0.5"]) <= float(r["q0.95"]) for r in rows)

    # 8. Write the chosen member to a new model; its fit is the member's.
    chosen = max(accepted["members"], key=lambda m: m["metrics"]["nse_C1"])
    await tools("open_model", path=inp_path, session_id="chosen")
    await tools(
        "gym_score",
        action="apply_design",
        job_id=job["job_id"],
        evaluation=chosen["evaluation"],
        session_id="chosen",
    )
    saved = await tools("save", session_id="chosen", path="calibrated.inp")
    await tools("open_model", path=saved["path"], session_id="check")
    await tools("run", session_id="check")
    again = await tools("compare", a=str(observed), b="check", kind="link", variable="flow")
    assert again["rows"][0]["nse"] == pytest.approx(chosen["metrics"]["nse_C1"], rel=1e-3)
    assert model.read_bytes() == original


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
    coords = await tools("inspect", session_id="cap", target="spatial", method="node_coords")
    assert coords["result"]
    spatial = await tools("describe", topic="spatial")
    assert {"link_vertices", "subcatchment_polygon"} <= set(json.dumps(spatial).split('"'))
    await tools(
        "inspect", session_id="cap", target="spatial", method="link_vertices", args={"link": "C1"}
    )
    await tools(
        "inspect",
        session_id="cap",
        target="spatial",
        method="subcatchment_polygon",
        args={"sub": 0},
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
def _storms(folder: Path) -> list[dict]:
    """Three storms on the twin-tank gage, written beside the model."""
    (folder / "rain").mkdir()
    storms = []
    for day, (name, peak) in enumerate((("short", 6.0), ("long", 3.0), ("burst", 12.0)), start=2):
        start = f"2024-01-{day:02d}T00:00:00"
        rows = "".join(
            f"2024-01-{day:02d}T00:{10 * (i + 1):02d}:00,{peak * f}\n"
            for i, f in enumerate((0.3, 1.0, 0.6, 0.2))
        )
        (folder / "rain" / f"{name}.csv").write_text("time,rain\n" + rows)
        storms.append(
            {
                "name": name,
                "series": f"rain/{name}.csv",
                "gage": "RG",
                "start": start,
                "end": f"2024-01-{day:02d}T04:00:00",
            }
        )
    return storms


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
        # Three storms in the model's folder, each run in its own window; the worst counts.
        "events": _storms(inp.parent),
        "event_aggregation": "max",
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
    status = await _finished(tools, job["job_id"])
    for record in _log(status):
        for term in ("uncontrolled_discharge", "storage_underutilization"):
            per_event = [record["metrics"][f"{term}@{e['name']}"] for e in spec["events"]]
            assert record["metrics"][term] == max(per_event)
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


async def test_verified_narrative(ledger_tools, inp_path):
    tools = ledger_tools
    # 1. Run to the end.
    await tools("open_model", path=inp_path, session_id="vn")
    await _results(tools, "vn")

    # 2. Commit.
    committed = await tools("tally", action="commit", session_id="vn")
    tally_id, root = committed["tally_id"], committed["root"]
    assert tally_id.startswith("vn/") and root

    # 3. List the facts.
    listing = await tools("tally", action="facts", tally_id=tally_id)
    facts = listing["facts"]
    assert facts

    # 4-5. Narrative from the facts, citing every material diagnostic; claims from citations.
    def material(f):
        return (f["scope"] == "diagnostic" and f["value"] != 0) or f["attr"].endswith(
            "continuity_error_pct"
        )

    cited = [f for f in facts if material(f)]
    assert any(f["attr"].endswith("continuity_error_pct") for f in cited)
    extra = [f for f in facts if f["id"].startswith("f:link:") and f["scope"] == "output"][:1]
    sentences = []
    claims = []
    for f in [*extra, *cited]:
        unit = f" {f['unit']}" if f["unit"] else ""
        sentences.append(f"{f['entity']} {f['attr']} is {f['value']}{unit} [{f['id']}].")
        claims.append({"fact_id": f["id"], "value": f["value"]})
    narrative = " ".join(sentences)

    # 6. Verify.
    result = await tools(
        "tally", action="verify", tally_id=tally_id, claims=claims, narrative=narrative
    )
    assert result["summary"]["status"] == "verified", result
    assert result["coverage"]["uncited"] == [] and result["bare"] == []

    # 7. Footer from the results.
    footer = (
        f"verified against tally {tally_id}, {len(claims)} claims, "
        f"coverage {result['summary']['coverage']} (root {root})"
    )
    assert footer.startswith("verified against tally vn/") and "coverage 1.0" in footer


async def test_model_provenance(ledger_tools, inp_path, output_dir):
    tools = ledger_tools
    await tools("open_model", path=inp_path, session_id="mp")
    await _results(tools, "mp")

    layer = output_dir / "pipes.geojson"
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(i), 0.0]},
            "properties": {"FACILITYID": fid},
        }
        for i, fid in enumerate(["C2", "J2", "J11"])
    ]
    layer.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    note = output_dir / "inspection.md"
    note.write_text("# Inspection\n\nConduit C2 shows root intrusion near the outlet.\n")

    reg = await tools("sources", action="register", path=str(layer), role="network",
                      crs="local:feet")  # fmt: skip
    await tools("sources", action="register", path=str(note), role="document", crs="local:feet")
    source_id = reg["source"]["source_id"]
    await tools("sources", action="link", session_id="mp", source_id=source_id,
                rule="id_match", params={"key": "FACILITYID"})  # fmt: skip

    # 1. List the sources.
    listing = await tools("sources", action="list")
    assert {s["role"] for s in listing["sources"]} >= {"network", "document"}

    # 2. Lineage.
    lineage = await tools("sources", action="lineage", session_id="mp",
                          element_kind="link", element_id="C2")  # fmt: skip
    assert lineage["count"] >= 1
    edge = lineage["edges"][0]
    assert edge["source_id"] == source_id and edge["feature_key"] and edge["rule"] == "id_match"

    # 3. Locate.
    found = await tools("locate", session_id="mp", element_kind="link", element_id="C2")
    query = found["query"]
    assert query["buffer_unit"] == "ft" and query["crs"] == "local:feet"
    assert query["buffer"] == 25.0
    c2 = next(e for e in found["elements"] if e["id"] == "C2")
    assert any(x["source_id"] == source_id for x in c2["lineage"])
    assert any("C2" in m["text"] for m in found["mentions"])
    assert found["excluded"]["crs_incompatible"] == []
    assert {"outside_buffer", "not_yet_valid", "retired"} <= set(found["excluded"])

    # 4. The provenance block, verbatim.
    prov = found["provenance"]
    for key in ("buffer", "buffer_unit", "crs", "spatial", "sources", "model_sha256",
                "as_of", "as_known", "ledger_path"):  # fmt: skip
        assert key in prov
    assert {s["id"] for s in prov["sources"]} >= {source_id}
