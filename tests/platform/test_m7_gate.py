"""The M7 gate of the platform plan (agent orchestration qualification), scripted end to end.

Gate: an agent-driven optimization through openswmm.mcp is auditable from prompt to artifact.

A person opens an agent session naming the conversation it works for, with a budget of 20
evaluations, in an organization that requires an estimate above 10 and on a target with a
price table. Through MCP tools alone the agent then:

1. registers the model;
2. is refused an optimization that names no estimate, estimates an NSGA-II search of 24
   (it reserves 32: needs approval, has a cost band), and starts it; the study waits until a person
   approves it, then a gymnasium NSGA-II run from the MCP server evaluates it;
3. proposes candidates itself on a second study, approved the same way, and closes it;
4. records a finding about one Pareto member, citing both its objectives.

The gate holds when the finding passes and the same finding with one number changed fails;
a graph query walks every cited number from the record's checksum through the attempt that
evaluated it to the tool call, the agent session, the agent and the person; the platform's
audit shows the approval decided by a person on the agent's request; the MCP journal and the
platform's audit name the same tool calls for every change; and the study runs again from
its manifest, by seed, to an identical evaluation log.

The question the agent answers comes from the person in the conversation; the platform keeps
its reference (conversation_ref), and no prompt or model reasoning is stored as evidence.
Files, including m7_gate_report.json, stay under tests/_output/platform/test_m7_gate/.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

pytest.importorskip("openswmm_gymnasium.optimize.manifest")
pytest.importorskip("loaders.findings", reason="the finding verifier needs water-ontology")

DATA = Path(__file__).resolve().parents[1] / "data"
WIDTH = "subcatchment.width"
MUTATING_TOOLS = {"platform_model", "platform_study", "platform_run"}


def env_config(inp: str) -> dict:
    """Two subcatchment widths against flooding and the outfall's peak flow."""
    return {
        "env_type": "cip",
        "inp_path": inp,
        "observations": {"node_depths": ["J1"]},
        "design_factories": [
            {
                "kind": "field_design",
                "params": {"path": WIDTH, "ids": ["S1", "S2"], "low": 100.0, "high": 3000.0},
            }
        ],
        "reward_terms": [
            {"kind": "flooding_volume"},
            {"kind": "peak_outflow", "params": {"link_ids": ["C11"]}},
        ],
    }


async def wait_for(tools, optimization_id: str, timeout: float = 600.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        view = await tools("platform_study", action="status", optimization_id=optimization_id)
        if view["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return view
        assert time.monotonic() < deadline, view
        await asyncio.sleep(0.25)


async def approved(tools, services, admin, study_id: str, timeout: float = 60.0) -> dict:
    """A person approves the study once it waits for approval."""
    deadline = time.monotonic() + timeout
    while True:
        study = await tools("platform_study", action="status", study_id=study_id)
        if study["state"] == "PENDING_APPROVAL" and study["approval_id"]:
            services.resolve_approval(admin, study["approval_id"], True, "within reason")
            return study
        assert time.monotonic() < deadline, study
        await asyncio.sleep(0.1)


async def test_m7_gate(mcp_server, control_plane, artifact_dir):
    from harness.duckdb_harness import CAT, Harness
    from hydrocouple_platform.contracts.requests import OrganizationPolicy, PriceTable
    from hydrocouple_platform.db import transaction
    from hydrocouple_platform.db.models import AuditRecord
    from openswmm_gymnasium.optimize.manifest import rerun
    from sqlalchemy import select

    world, services = control_plane.world, control_plane.services
    services.set_policy(
        world.admin, world.org_id, OrganizationPolicy(require_estimate_above_evaluations=10)
    )
    report: dict = {}
    async with mcp_server(max_evaluations=20) as tools:
        session_id = tools.agent_session["agent_session_id"]
        budget = (await tools("platform_targets"))["budget"]
        assert budget["remaining"]["evaluations"] == 20

        # 1. The model.
        model = tools.working_dir / "base" / "model.inp"
        model.parent.mkdir()
        shutil.copy(DATA / "site_drainage_example.inp", model)
        revision = await tools(
            "platform_model", action="upload", path=str(model), target_id=world.target_id
        )
        study = {
            "revision_id": revision["revision_id"],
            "target_id": world.target_id,
            "env_config": env_config(str(model)),
        }

        # 2. Estimate before spend, a person's approval, then the search.
        search = {
            "algorithm": "nsga2",
            "budget": 24,
            "population_size": 8,
            "seed": 11,
            "workers": 0,
        }
        with pytest.raises(ToolError, match="ESTIMATE_REQUIRED"):
            await tools("platform_study", action="optimize", optimization=search, **study)
        first = await tools("platform_study", action="estimate", optimization=search, **study)
        services.set_target_prices(
            world.admin,
            world.target_id,
            PriceTable(currency="USD", per_hour={first["profile"]: 0.6}),
        )
        priced = {"estimated_seconds_per_evaluation": 2.0}
        estimate = await tools(
            "platform_study", action="estimate", optimization=search, **priced, **study
        )
        # A MOEA reserves one population past its budget: Platypus ends a generation there.
        assert estimate["evaluations"] == 24 + 8 and estimate["requires_approval"]
        assert estimate["cost_band"] != "unknown" and estimate["currency"] == "USD"
        started = await tools(
            "platform_study",
            action="optimize",
            optimization=search,
            estimate_id=estimate["estimate_id"],
            **priced,
            **study,
        )
        held = await approved(tools, services, world.admin, started["study_id"])
        done = await wait_for(tools, started["optimization_id"])
        assert done["state"] == "COMPLETED" and 0 < done["evaluations"] <= 32, done
        results = await tools(
            "platform_study", action="results", optimization_id=started["optimization_id"]
        )
        pareto = results["result"]["pareto"]
        assert pareto

        # 3. Candidates the agent proposes itself.
        own = await tools(
            "platform_study",
            action="create",
            optimization={"algorithm": "random_search", "budget": 2, "seed": 3, "workers": 0},
            **study,
        )
        await approved(tools, services, world.admin, own["study_id"])
        batch = await tools(
            "platform_study",
            action="submit",
            study_id=own["study_id"],
            candidates=[[400.0, 2800.0], [2800.0, 400.0]],
            wait_seconds=180,
        )
        assert batch["state"] == "DONE", batch
        await tools("platform_study", action="close", study_id=own["study_id"])

        # 4. The finding: one Pareto member's two objectives.
        member = pareto[0]
        claims, quoted = [], []
        for name, value in member["objectives"].items():
            text = f"{value:.3f}"
            quoted.append(f"{text} for {name}")
            claims.append(
                {
                    "quoted": text,
                    "study_id": started["study_id"],
                    "evaluation": member["evaluation"],
                    "locator": f"objectives/{name}",
                }
            )
        statement = "One trade-off on the front gives " + " and ".join(quoted) + "."
        question = "Which subcatchment widths trade flooding against peak outflow?"
        finding = await tools(
            "platform_finding", statement=statement, claims=claims, question=question
        )
        assert finding["verifier_passed"], finding
        wrong = f"{float(claims[0]['quoted']) + 1:.3f}"
        tampered = await tools(
            "platform_finding",
            statement=statement.replace(claims[0]["quoted"], wrong, 1),
            claims=[claims[0] | {"quoted": wrong}, *claims[1:]],
            question=question,
        )
        assert not tampered["verifier_passed"]
    report["finding"] = {"passed": True, "tampered_passed": False, "claims": len(claims)}

    # The graph walks every cited number to the person.
    h = Harness()
    h.load(Path(finding["rows"]))
    assert h.violations() == []
    walked = h.con.execute(
        f"""SELECT record_sha256, attempt_id, tool_call_id, agent_iri, person_iri
            FROM {CAT}.reference.candidate_lineage
            WHERE study_iri = ? AND candidate_index = ?""",
        [f"wsys:study/{started['study_id']}", member["evaluation"]],
    ).fetchall()
    lineage = json.loads((Path(done["output_dir"]) / "study-lineage.json").read_text())
    shas = {c["object"]["sha256"] for c in lineage["chunks"] if c["object"]}
    ((sha, attempt, call, agent, person),) = walked
    assert sha in shas and attempt and call == started["tool_call_id"]
    assert agent == f"wsys:agent/ai/{session_id}" and person == "wsys:agent/person/alice"
    report["lineage"] = {
        "record_sha256": sha,
        "attempt_id": attempt,
        "tool_call_id": call,
        "agent": agent,
        "person": person,
    }

    # The approval: a person decided it, on the agent's request.
    with transaction(services._factory) as s:
        audit = list(s.scalars(select(AuditRecord)))
    decision = next(
        a
        for a in audit
        if a.action == "approval.approved" and a.details.get("study_id") == started["study_id"]
    )
    assert decision.object_id == held["approval_id"]
    assert decision.actor["human_subject"] == "alice" and decision.actor["agent_session_id"] is None
    assert decision.details["requested_by"]["tool_call_id"] == started["tool_call_id"]

    # The journal and the platform's audit name the same tool calls.
    journal = [
        json.loads(line)
        for path in sorted((tools.working_dir / "journal").glob("jr_*.jsonl"))
        for line in path.read_text().splitlines()
    ]
    changes = {
        line["tool_call_id"]
        for line in journal
        if line["tool"] in MUTATING_TOOLS and line["effect"] != "read" and line.get("ok", True)
    }
    audited = {
        a.actor.get("tool_call_id")
        for a in audit
        if (a.actor or {}).get("agent_session_id") == session_id
    }
    assert changes and changes == audited, (changes, audited)
    report["journal_and_audit"] = sorted(changes)

    # The study again from its manifest, by seed, to the same evaluations.
    again = artifact_dir / "rerun"
    rerun(Path(done["output_dir"]) / "study-manifest.json", model, again, workers=0)
    first_log = (Path(done["output_dir"]) / "evaluations.jsonl").read_bytes()
    assert (again / "evaluations.jsonl").read_bytes() == first_log
    report["rerun_identical"] = True
    (artifact_dir / "m7_gate_report.json").write_text(json.dumps(report, indent=2) + "\n")
