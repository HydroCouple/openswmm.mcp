"""The Phase 1 gate of the ecosystem plan, scripted end to end.

An agent, through this MCP server and a local control plane, answers "close the gate, run the
storm, what floods?" for two uploaded revisions of one model (gate open, gate closed):

1. it answers by reading the run summary on a target whose egress policy allows summaries,
   without downloading the report or the binary output;
2. a lineage query returns the revision hash, attempt, agent session and tool call for every
   number in the answer;
3. the verifier rejects the same answer with one number changed;
4. a client that approves read-only tools without asking still asks for every call that
   changes something.

The model is the platform's twin-tank test model (gymnasium benchmark b01 with its design
storm tripled): tank T1 drains to tank T2 through the side orifice ORIF. For the gate, T1 is
5 ft deep instead of 12 ft in both revisions, so that holding the storm in T1 floods it (with
12 ft the closed gate fills T1 to 6.5 ft and nothing floods). The closed revision adds a control
rule that shuts ORIF. The storm is that model's design storm, not a 10-year storm.
Everything the gate writes, including gate_report.json, stays under
tests/_output/platform/test_phase1_gate/.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from .conftest import PLATFORM_TESTS

pytest.importorskip("loaders.findings", reason="the gate needs water-ontology installed")

MODEL = PLATFORM_TESTS / "data" / "twin_tank_heavy" / "model.inp"
T1_12_FT = "T1      0       12        0          FUNCTIONAL"
T1_5_FT = "T1      0       5         0          FUNCTIONAL"
CLOSE_GATE = """
[CONTROLS]
RULE CLOSE_GATE
IF SIMULATION TIME > 0
THEN ORIFICE ORIF SETTING = 0
"""


class Approvals:
    """A client policy: read-only tools run without asking; any other call asks the person,
    who says yes here. Every call is recorded with the decision that let it run."""

    def __init__(self, tools):
        self.tools, self.read_only, self.log = tools, None, []

    async def __call__(self, tool: str, **arguments):
        if self.read_only is None:
            listed = await self.tools.client.list_tools()
            self.read_only = {
                t.name for t in listed if t.annotations and t.annotations.readOnlyHint
            }
        self.log.append((tool, "without asking" if tool in self.read_only else "asked"))
        return await self.tools(tool, **arguments)


async def published(call, run_id: str, timeout: float = 180.0, **arguments) -> dict:
    """platform_results once the run is published; until then it answers NOT_READY."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return await call("platform_results", run_id=run_id, **arguments)
        except ToolError as exc:
            assert "NOT_READY" in str(exc) and time.monotonic() < deadline, str(exc)
            await asyncio.sleep(0.5)


def flooding_sentence(case: str, table: dict) -> tuple[str, list[dict]]:
    columns, rows = table["columns"], table["rows"]
    if not rows:
        return f"With the gate {case}, no node floods.", []
    parts, claims = [], []
    for row in rows:
        volume = f"{row['total_flood_volume']:,.0f}"
        seconds = f"{row['time_flooded']:.0f}"
        parts.append(
            f"{row['node_id']} floods {volume} {columns['total_flood_volume']['unit']} "
            f"over {seconds} {columns['time_flooded']['unit']}"
        )
        for column, quoted in (("total_flood_volume", volume), ("time_flooded", seconds)):
            claims.append(
                {
                    "quoted": quoted,
                    "unit": columns[column]["unit"],
                    "locator": f"node_flooding/{row['node_id']}/{column}",
                }
            )
    return f"With the gate {case}, " + " and ".join(parts) + ".", claims


async def test_phase1_gate(mcp_server, control_plane, artifact_dir):
    from generators.linkml_to_sqlite import build
    from harness.duckdb_harness import Harness

    report: dict = {
        "model": str(MODEL),
        "changed": "T1 maximum depth 12 ft -> 5 ft in both revisions; closed adds RULE CLOSE_GATE",
        "storm": "the model's design storm, not a 10-year storm",
    }
    async with mcp_server(max_runs=2) as tools:
        call = Approvals(tools)
        choices = await call("platform_targets")
        (organization,) = choices["organizations"].values()
        (target,) = organization["targets"]
        assert target["egress_policy"] == "summaries"

        models = tools.working_dir / "models"
        cases = {}
        for case, extra in (("open", ""), ("closed", CLOSE_GATE)):
            folder = models / f"gate_{case}"
            folder.mkdir(parents=True)
            text = MODEL.read_text()
            assert text.count(T1_12_FT) == 1
            (folder / "model.inp").write_text(text.replace(T1_12_FT, T1_5_FT) + extra)
            revision = await call(
                "platform_model",
                action="upload",
                path=str(folder / "model.inp"),
                target_id=target["target_id"],
            )
            estimate = await call(
                "platform_estimate",
                revision_id=revision["revision_id"],
                target_id=target["target_id"],
            )
            assert estimate["ok"] and not estimate["requires_approval"], estimate
            accepted = await call(
                "platform_run",
                revision_id=revision["revision_id"],
                target_id=target["target_id"],
                idempotency_key=f"gate-{case}",
            )
            assert accepted["status"] == "ACCEPTED", accepted
            cases[case] = {"revision": revision, "run": accepted}

        sentences, claims = [], []
        for case, item in cases.items():
            results = await published(call, item["run"]["run_id"], table="node_flooding")
            item["artifacts"] = sorted(a["path"] for a in results["artifacts"])
            sentence, cited = flooding_sentence(case, results["table"])
            sentences.append(sentence)
            claims += [c | {"run_id": item["run"]["run_id"]} for c in cited]
            item["flooded"] = [r["node_id"] for r in results["table"]["rows"]]
        statement = " ".join(sentences)
        report["statement"] = statement
        assert cases["closed"]["flooded"], "closing the gate floods nothing: the gate tests nothing"
        assert claims

        # 1. Answered from the summary alone: the report and binary output exist, unfetched.
        for item in cases.values():
            assert {"model.rpt", "model.out", "summary.json"} <= set(item["artifacts"])
        fetched = {p.name for p in tools.working_dir.rglob("*") if p.is_file()}
        assert not {"model.rpt", "model.out"} & fetched, sorted(fetched)

        good = await call(
            "platform_finding",
            statement=statement,
            claims=claims,
            question="If we close the gate, what floods in this storm?",
        )
        assert good["verifier_passed"] is True, good
        report["finding"] = good

        # 3. One number changed: the verifier rejects it and names the value it found.
        first = claims[0]
        changed = f"{float(first['quoted'].replace(',', '')) + 1000:,.0f}"
        bad = await call(
            "platform_finding",
            statement=statement.replace(first["quoted"], changed, 1),
            claims=[first | {"quoted": changed}] + claims[1:],
        )
        assert bad["verifier_passed"] is False
        failed = [c for c in bad["claims"] if not c["passed"]]
        assert len(failed) == 1 and failed[0]["reason"].startswith("the artifact has ")
        report["changed_number"] = failed[0]

    # 2. Lineage of every number: revision hash, attempt, agent session, tool call.
    rows = Path(good["rows"])
    harness = Harness()
    harness.load(rows)
    assert harness.violations() == []
    snapshot = artifact_dir / "gate.sqlite"
    build(rows, snapshot)
    session = tools.agent_session["agent_session_id"]
    lineage = []
    with closing(sqlite3.connect(snapshot)) as db:
        db.row_factory = sqlite3.Row
        for claim in claims:
            evidence = next(
                c["evidence"]
                for c in good["claims"]
                if c["locator"] == claim["locator"] and c["stated"] == claim["quoted"]
            )
            (row,) = db.execute(
                """SELECT revision_manifest_sha256, revision_id, attempt_id, session_id,
                          tool_call_id FROM reference__finding_lineage
                   WHERE finding_iri = ? AND evidence_iri = ?""",
                (good["finding"], evidence),
            ).fetchall()
            item = next(i for i in cases.values() if i["run"]["run_id"] == claim["run_id"])
            assert row["revision_manifest_sha256"] == item["revision"]["manifest_sha256"]
            assert row["revision_id"] == item["revision"]["revision_id"]
            assert row["attempt_id"].startswith("att_")
            assert row["session_id"] == session
            assert row["tool_call_id"] == item["run"]["tool_call_id"]
            lineage.append({"claim": claim["quoted"], "locator": claim["locator"]} | dict(row))
    report["lineage"] = lineage

    # 4. Reads ran without asking; every call that changed something asked.
    journal = [
        json.loads(line)
        for path in (tools.working_dir / "journal").glob("*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    effects = {(x["tool"], x["effect"]) for x in journal}
    asked = {tool for tool, decision in call.log if decision == "asked"}
    quiet = {tool for tool, decision in call.log if decision == "without asking"}
    assert asked == {"platform_model", "platform_run", "platform_finding"}
    assert quiet == {"platform_targets", "platform_estimate", "platform_results"}
    assert all(effect == "read" for tool, effect in effects if tool in quiet)
    assert all(effect != "read" for tool, effect in effects if tool in asked)
    report["approvals"] = {"asked": sorted(asked), "without_asking": sorted(quiet)}
    report["journal_calls"] = len(journal)

    report["graph_rows"] = str(rows)
    (artifact_dir / "gate_report.json").write_text(json.dumps(report, indent=2) + "\n")
