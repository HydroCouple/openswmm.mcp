"""The Phase 4 gate of the ecosystem plan (the digital twin loop), scripted end to end.

1. A twin fed replayed telemetry cycles on a schedule: six 40-minute twin.cycle cycles of the
   twin-tank benchmark on a local-pool target, each continuing from the state.h5 the cycle
   before it recorded, its rain read from the revision's rain.csv up to the cycle's end.
2. The cycles' end states equal one uninterrupted run that records at the same times and applies
   the same setpoints at the same times, bit for bit, at the pinned thread count (1); workflows
   that do not resume are refused resume_from and schedules.
3. Each cycle forecasts both tanks' depths an hour ahead. In the knowledge graph built from the
   attempt manifests, a forecast is an Observation with an issue time and a horizon beside the
   measurement of the same element, property and time (the first tank's depth, replayed from a
   truth run with 2 % noise, a sidecar of the revision); the forecast skill by lead time is a
   query over them.
4. The control curve proposes setpoints and an agent session proposes one; the agent can neither
   decide it nor run it, a person decides every proposal, the next cycle applies the approved
   ones and records them. The graph names who proposed and who decided each, with the agent
   acting for its person, and passes every rule; marking the agent's setpoint applied without a
   person's approval fails one.

Files, including gate4_report.json, stay under tests/_output/platform/test_phase4_gate/.
"""

from __future__ import annotations

import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("loaders.platform_to_rows", reason="the graph needs water-ontology")
pytest.importorskip(
    "hydrocouple_platform.runner.twin", reason="the twin needs hydrocouple.platform"
)

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_TESTS = Path(
    os.environ.get("HCP_PLATFORM_TESTS", ROOT.parent / "hydrocouple.platform" / "tests")
)
MODEL = PLATFORM_TESTS / "data" / "twin_tank_heavy" / "model.inp"
START = datetime(2024, 1, 1)
ENDS = [START + timedelta(minutes=40 * (k + 1)) for k in range(6)]
# Rain (in/h) every 5 minutes over four hours: storms in the first, third and fourth hours.
RAIN = (
    [0.0, 2.0, 6.0, 9.0, 12.0, 8.0, 5.0, 3.0, 1.0, 0.5, 0.0, 0.0]
    + [0.0] * 18
    + [4.0, 7.0, 11.0, 6.0, 3.0, 1.0]
    + [0.0] * 6
    + [2.0, 5.0, 3.0, 1.0]
    + [0.0] * 2
)
POLICY = {
    "link": "ORIF",
    "observe": "node.depth",
    "element": "T1",
    "x": [0.0, 2.0, 6.0],
    "setting": [1.0, 0.6, 0.2],
    "resolution": 0.2,
}


@pytest.fixture(scope="module", autouse=True)
def engine_continues():
    pytest.importorskip("openswmm.engine")
    from openswmm.engine import capabilities

    if "continue_extended_forcing" not in capabilities()["checkpoints"]:
        pytest.skip("the installed openswmm engine cannot continue into extended forcing")


def solver(folder: Path, rain: Path, end: datetime):
    """The model with its gage on the replayed rain before ``end``, ending there."""
    from hydrocouple_platform.runner.twin import _feed, _open

    (folder / "data").mkdir(parents=True, exist_ok=True)
    shutil.copy(rain, folder / "data" / "rain.csv")
    s = _open(MODEL, folder / f"{folder.name}.rpt", end)
    _feed(s, folder, [{"gage": "RG", "series": "data/rain.csv"}], end)
    return s


def state(s) -> dict:
    return {
        "nodes": {n.id: float(n.depth) for n in s.nodes},
        "links": {
            lk.id: {"flow": float(lk.flow), "setting": float(lk.control_setting)} for lk in s.links
        },
    }


def run(s, setpoints: list[dict], due: list[datetime], states: list[dict]) -> None:
    """Step to the end, applying ``setpoints`` from their applied_at; record states at ``due``."""
    from openswmm.engine import EngineState

    pending = sorted(setpoints, key=lambda a: a["applied_at"])
    s.initialize()
    s.start()
    while int(s.state) in (EngineState.STARTED, EngineState.RUNNING):
        while pending and s.current_datetime >= datetime.fromisoformat(pending[0]["applied_at"]):
            a = pending.pop(0)
            s.links[a["link"]].target_setting = float(a["value"])
        s.step()
        if due and s.current_datetime >= due[0] - timedelta(milliseconds=1):
            due.pop(0)
            states.append(state(s))
    states.append(state(s))
    s.end()
    s.close()
    s.destroy()


def telemetry(folder: Path) -> tuple[Path, Path]:
    """The replayed rain and the first tank's measured depth (a truth run, 2 % noise), to an
    hour past the last cycle, where its forecast reaches."""
    folder.mkdir(parents=True)
    rain = folder / "rain.csv"
    rain.write_text(
        "time,RG\n"
        + "".join(
            f"{(START + i * timedelta(minutes=5)).isoformat()},{v}\n" for i, v in enumerate(RAIN)
        )
    )
    from openswmm.engine import EngineState

    until = ENDS[-1] + timedelta(hours=1)
    s = solver(folder / "truth", rain, until)
    s.initialize()
    s.start()
    rng, due, rows = random.Random(4), START + timedelta(minutes=10), []
    while int(s.state) in (EngineState.STARTED, EngineState.RUNNING):
        s.step()
        if due <= until and s.current_datetime >= due - timedelta(milliseconds=1):
            rows.append(
                (
                    due.isoformat(),
                    round(float(s.nodes["T1"].depth) * (1 + 0.02 * rng.gauss(0, 1)), 4),
                )
            )
            due += timedelta(minutes=10)
    s.end()
    s.close()
    s.destroy()
    levels = folder / "levels.csv"
    with levels.open("w", newline="") as stream:
        csv.writer(stream).writerows([("time", "T1"), *rows])
    return rain, levels


def test_phase4_gate(control_plane, artifact_dir, make_token):
    from hydrocouple_platform.client import PlatformClient
    from hydrocouple_platform.contracts.errors import PlatformError
    from hydrocouple_platform.contracts.manifests import canonical_json
    from hydrocouple_platform.packaging import package_swmm_model
    from hydrocouple_platform.storage import sha256_file

    world, report = control_plane.world, {}
    rain, levels = telemetry(artifact_dir / "telemetry")
    package = package_swmm_model(
        MODEL,
        artifact_dir / "package",
        extra_files={"data/rain.csv": rain, "data/levels.csv": levels},
    )
    session = control_plane.agent_session(max_runs=5)
    with (
        PlatformClient(control_plane.url, make_token("alice")) as person,
        PlatformClient(control_plane.url, session["agent_session_token"]) as agent,
    ):
        revision = person.upload_revision(
            package, project_id=world.project_id, target_id=world.target_id
        )
        base = {
            "project_id": world.project_id,
            "revision_id": revision["revision_id"],
            "target_id": world.target_id,
        }
        schedule = person.create_schedule(
            base
            | {
                "workflow": "twin.cycle",
                "workflow_version": "1",
                "parameters": {
                    "telemetry": [{"gage": "RG", "series": "data/rain.csv"}],
                    "forecast": {
                        "horizon_minutes": 60,
                        "every_minutes": 10,
                        "observe": [
                            {"path": "node.depth", "id": "T1"},
                            {"path": "node.depth", "id": "T2"},
                        ],
                    },
                    "policy": POLICY,
                },
                "first_cycle_end": ENDS[0].isoformat(),
                "cycle_minutes": 40,
                "cycles": len(ENDS),
                "await_decisions": True,
                "retry_budget": 0,
            },
            idempotency_key="gate4-schedule",
        )["schedule_id"]

        # 2: workflows that do not resume are refused it.
        refusals = {}
        for name, request, call in (
            (
                "swmm.simulation resume_from",
                base
                | {
                    "workflow": "swmm.simulation",
                    "workflow_version": "1",
                    "resume_from": "run_any",
                },
                person.submit_run,
            ),
            (
                "gym.evaluate schedule",
                base
                | {
                    "workflow": "gym.evaluate",
                    "workflow_version": "1",
                    "parameters": {"env_config": {}},
                    "first_cycle_end": ENDS[0].isoformat(),
                    "cycle_minutes": 40,
                },
                person.create_schedule,
            ),
        ):
            with pytest.raises(PlatformError) as refused:
                call(request, idempotency_key=f"gate4-{name.split()[0]}")
            refusals[name] = refused.value.body.message

        # 4: the control curve proposes, an agent proposes once; a person decides each.
        agent_proposal, agent_refusals, decisions = None, {}, []
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            view = person.schedule(schedule)
            if view["state"] != "ACTIVE":
                break
            seen = {d["proposal_id"] for d in decisions}
            fresh = [
                p
                for p in person.proposals(schedule)
                if p["state"] == "PROPOSED" and p["proposal_id"] not in seen
            ]
            if fresh and agent_proposal is None:
                agent_proposal = agent.propose_setpoint(
                    schedule,
                    {
                        "link": "ORIF",
                        "value": 0.5,
                        "effective_from": START.replace(hour=1, minute=30).isoformat(),
                        "note": "open the orifice halfway before the second storm",
                    },
                )
                unapproved = {
                    "link": "ORIF",
                    "value": 0.5,
                    "proposal_id": agent_proposal["proposal_id"],
                    "effective_from": agent_proposal["effective_from"],
                }
                for name, call in (
                    (
                        "approve its own proposal",
                        lambda: agent.decide_proposal(agent_proposal["proposal_id"], True),
                    ),
                    (
                        "run its own proposal",
                        lambda: agent.submit_run(
                            base
                            | {
                                "workflow": "twin.cycle",
                                "workflow_version": "1",
                                "resume_from": view["last_run_id"],
                                "parameters": {
                                    "cycle_end": ENDS[1].isoformat(),
                                    "setpoints": [unapproved],
                                },
                            },
                            idempotency_key="gate4-agent-run",
                        ),
                    ),
                ):
                    with pytest.raises(PlatformError) as refused:
                        call()
                    agent_refusals[name] = refused.value.body.message
                fresh.append(agent_proposal)
            forecast = sorted((p for p in fresh if p["run_id"]), key=lambda p: p["effective_from"])
            first = forecast[0]["proposal_id"] if forecast else None
            for p in fresh:  # the agent's and each forecast's first approved, the others rejected
                approve = p["run_id"] is None or p["proposal_id"] == first
                decisions.append(
                    person.decide_proposal(
                        p["proposal_id"], approve, "agreed" if approve else "not needed"
                    )
                )
            time.sleep(0.3)
        proposals = person.proposals(schedule)
        graph = artifact_dir / "graph"
        cycles = []
        for c in view["history"]:
            folder = graph / "attempts" / str(c["number"])
            files = person.download(c["run_id"], ["forecast.json", "cycle.json"], folder)
            cycles.append(json.loads(files["cycle.json"].read_text()))
            published = person.attempt_manifest(c["run_id"])  # attempt.json, as the runner wrote it
            (folder / "attempt.json").write_bytes(canonical_json(published["attempt_manifest"]))
            assert sha256_file(folder / "attempt.json") == published["attempt_manifest_sha256"]
        (graph / "schedule.json").write_text(json.dumps(view, indent=2) + "\n")
        (graph / "proposals.json").write_text(json.dumps(proposals, indent=2) + "\n")

    # 1 and 2: the schedule completed, each cycle resuming the last; one uninterrupted run with
    # records at the cycle ends and the same setpoints at the same times reaches the same states.
    assert view["state"] == "COMPLETED", view
    history = view["history"]
    assert [c["resumed_from"] for c in history] == [None] + [c["run_id"] for c in history[:-1]]
    applied = [a for c in cycles for a in c["setpoints_applied"]]
    reference = artifact_dir / "reference"
    s = solver(reference, rain, ENDS[-1])
    r = s.restart
    r.record_file, r.record_format = str(reference / "reference.h5"), "HDF5"
    r.record_at, r.record_continuation = [*ENDS[:-1], "END"], True
    expected = []
    run(s, applied, list(ENDS[:-1]), expected)
    assert [c["end_state"] for c in cycles] == expected
    assert refusals["swmm.simulation resume_from"].endswith(
        "does not continue from an earlier run's state"
    )
    assert "cannot run on a schedule" in refusals["gym.evaluate schedule"]
    report["cycles"] = {
        "schedule_id": schedule,
        "runs": [c["run_id"] for c in history],
        "equal_to_uninterrupted": True,
        "setpoints_applied": applied,
        "refused_resume": refusals,
    }

    # 4: who decided what.
    assert "cannot approve or reject a setpoint" in agent_refusals["approve its own proposal"]
    assert "not a proposal a person approved" in agent_refusals["run its own proposal"]
    by_agent = next(p for p in proposals if p["proposal_id"] == agent_proposal["proposal_id"])
    assert (by_agent["state"], by_agent["decided_by"]) == ("APPLIED", "alice")
    assert all(p["decided_by"] == "alice" for p in proposals if p["state"] != "PROPOSED")
    assert {a["proposal_id"] for a in applied} == {
        p["proposal_id"] for p in proposals if p["state"] == "APPLIED"
    }

    # 3 and 4: the graph.
    from harness.duckdb_harness import CAT, Harness
    from loaders.platform_to_rows import PlatformLoader

    data = artifact_dir / "telemetry"  # the files the revision carries as data/*.csv
    for name, column, path, element, unit in (
        ("rain", "RG", "gage.rainfall", "RG", "in/hr"),
        ("levels", "T1", "node.depth", "T1", "ft"),
    ):
        (data / f"{name}.json").write_text(
            json.dumps(
                {
                    "file": f"{name}.csv",
                    "source": "replayed telemetry (a sidecar of the revision)",
                    "streams": [{"column": column, "path": path, "id": element, "unit": unit}],
                }
            )
            + "\n"
        )
    loader = PlatformLoader("demo", "twin-tanks")
    loader.add_revision(artifact_dir / "package" / "revision-manifest.json")
    for c in history:
        loader.add_attempt(graph / "attempts" / str(c["number"]))
    loader.add_schedule(graph / "schedule.json", graph / "proposals.json")
    loader.add_telemetry(data / "rain.json")
    loader.add_telemetry(data / "levels.json")
    rows, model_rows = artifact_dir / "graph_rows", artifact_dir / "model_rows"
    shutil.rmtree(rows, ignore_errors=True)
    shutil.rmtree(model_rows, ignore_errors=True)
    loader.write(rows)
    # The revision's model as the graph holds it: the assets the sensors are attached to.
    subprocess.run(
        [
            sys.executable,
            "-m",
            "loaders.swmm_to_rows",
            str(artifact_dir / "package" / "model" / "model.inp"),
            "--utility",
            "demo",
            "--system",
            "twin-tanks",
            "--work",
            str(artifact_dir / "model_work"),
            "-o",
            str(model_rows),
        ],
        check=True,
        cwd=str(artifact_dir),
    )
    harness = Harness()
    harness.load(model_rows)
    harness.load(rows)
    assert harness.violations() == []
    skill = harness.con.execute(f"""
        SELECT f.horizon, count(*) AS pairs, sqrt(avg(power(f.value - m.value, 2))) AS rmse_m
        FROM {CAT}.observation.observation f JOIN {CAT}.observation.observation m
          ON m.entity_iri = f.entity_iri AND m.property_iri = f.property_iri
         AND m.phenomenon_time = f.phenomenon_time AND m.result_kind = 'MEASUREMENT'
        WHERE f.result_kind = 'FORECAST'
        GROUP BY f.horizon ORDER BY length(f.horizon), f.horizon""").fetchall()
    forecasts_t1 = harness.con.execute(f"""
        SELECT count(*) FROM {CAT}.observation.observation
        WHERE result_kind = 'FORECAST' AND entity_iri LIKE '%/node/T1'""").fetchone()[0]
    assert sum(pairs for _, pairs, _ in skill) == forecasts_t1 == len(ENDS) * 6
    chain = harness.con.execute(
        f"""
        SELECT s.setpoint_state, a.agent_kind, p.iri, d.iri FROM {CAT}.observation.setpoint s
        JOIN {CAT}.reference.agent a ON a.iri = s.set_by
        JOIN {CAT}.reference.agent p ON p.iri = a.acts_on_behalf_of
        JOIN {CAT}.reference.agent d ON d.iri = s.decided_by
        WHERE s.proposal_id = ?""",
        [agent_proposal["proposal_id"]],
    ).fetchall()
    assert chain == [("APPLIED", "AI_AGENT", "wsys:agent/person/alice", "wsys:agent/person/alice")]
    # An agent-proposed setpoint applied without a person's approval fails a rule.
    broken = artifact_dir / "graph_rows_unapproved"
    shutil.rmtree(broken, ignore_errors=True)
    shutil.copytree(rows, broken)
    table = broken / "observation.setpoint.jsonl"
    records = [json.loads(line) for line in table.read_text().splitlines()]
    for rec in records:
        if rec["proposal_id"] == agent_proposal["proposal_id"]:
            rec.pop("approved_by")
            rec.pop("decided_by")
    table.write_text("".join(json.dumps(rec, sort_keys=True) + "\n" for rec in records))
    unapproved = Harness()
    unapproved.load(model_rows)
    unapproved.load(broken)
    failed = {rid for rid, _, _, _ in unapproved.violations()}
    assert failed == {"setpoint_decided_by_a_person"}
    report["graph"] = {
        "rows": str(rows),
        "forecast_skill": [{"horizon": h, "pairs": n, "rmse_m": round(e, 4)} for h, n, e in skill],
        "agent_setpoint_chain": chain[0],
        "unapproved_fails": sorted(failed),
    }
    report["proposals"] = {
        "count": len(proposals),
        "by_state": {
            s: sum(p["state"] == s for p in proposals)
            for s in sorted({p["state"] for p in proposals})
        },
        "agent_refused": agent_refusals,
    }
    (artifact_dir / "gate4_report.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )
