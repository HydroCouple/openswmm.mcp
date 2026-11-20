"""The platform toolset end to end: upload two revisions, estimate, run, read results.

A local control plane (the real API over PostgreSQL), a local-pool agent, runner processes
and the real engine. The MCP server holds only an agent session credential.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

from .conftest import wait_for_job

DATA = Path(__file__).resolve().parents[1] / "unit" / "data"
SOURCE = Path(__file__).resolve().parents[2] / "src" / "openswmm_mcp" / "tools" / "platform.py"


def two_models(folder: Path) -> tuple[Path, Path]:
    """The site drainage model, and a copy whose conduit C1 is four times rougher."""
    base, rough = folder / "base" / "model.inp", folder / "rough" / "model.inp"
    for path in (base, rough):
        path.parent.mkdir(parents=True)
    shutil.copy(DATA / "site_drainage_model.inp", base)
    text, count = re.subn(
        r"(?m)^(C1\s+\S+\s+\S+\s+\S+\s+)\S+", r"\g<1>0.2", base.read_text(), count=1
    )
    assert count == 1 and text != base.read_text()
    rough.write_text(text)
    return base, rough


async def test_upload_estimate_run_and_read_results(mcp_server):
    async with mcp_server(max_runs=2) as tools:
        choices = await tools("platform_targets")
        assert choices["acting_for"] == "alice"
        (organization,) = choices["organizations"].values()
        target = organization["targets"][0]["target_id"]
        assert {"swmm.simulation"} <= {w["workflow"] for w in choices["workflows"]}

        runs = {}
        for name, model in zip(("base", "rough"), two_models(tools.working_dir / "models")):
            revision = await tools(
                "platform_model", action="upload", path=str(model), target_id=target
            )
            assert revision["state"] == "READY" and "model/model.inp" in revision["files"]
            same = await tools("platform_model", action="get", revision_id=revision["revision_id"])
            assert same["manifest_sha256"] == revision["manifest_sha256"]
            estimate = await tools(
                "platform_estimate", revision_id=revision["revision_id"], target_id=target
            )
            assert estimate["ok"] and not estimate["requires_approval"], estimate
            accepted = await tools(
                "platform_run",
                revision_id=revision["revision_id"],
                target_id=target,
                parameters={"progress_interval_seconds": 0.2},
                idempotency_key=f"walkthrough-{name}",
            )
            assert accepted["status"] == "ACCEPTED" and accepted["tool_call_id"]
            runs[name] = accepted | {"revision": revision}

        assert (
            runs["base"]["revision"]["manifest_sha256"]
            != (runs["rough"]["revision"]["manifest_sha256"])
        )
        peaks = {}
        for name, accepted in runs.items():
            view = await wait_for_job(tools, accepted["job_id"])
            assert view["state"] == "SUCCEEDED", view
            results = await tools("platform_results", run_id=accepted["run_id"])
            assert results["provenance"]["runtime"]["engine"]
            assert results["provenance"]["actor"]["tool_call_id"] == accepted["tool_call_id"]
            kinds = {a["path"]: a["kind"] for a in results["artifacts"]}
            assert kinds["summary.json"] == "summary" and "model.rpt" in kinds
            summary = results["summary"]
            assert summary["tables"]["link_flow_summary"]["rows"] == 11
            # Only the summary was fetched: the report and binary output stay in storage.
            fetched = {p.name for p in Path(summary["file"]).parent.iterdir()}
            assert fetched == {"summary.json"}
            flows = await tools(
                "platform_results", run_id=accepted["run_id"], table="link_flow_summary"
            )
            assert flows["table"]["columns"]["max_flow"]["unit"] == summary["flow_units"]
            peaks[name] = {r["link_id"]: r for r in flows["table"]["rows"]}
        # The rougher conduit runs fuller and slower: the two revisions gave two results.
        assert peaks["base"]["C1"]["max_velocity"] > peaks["rough"]["C1"]["max_velocity"]

        # The journal names each submission by the id the attempt manifest carries.
        journal = [
            json.loads(line)
            for path in (tools.working_dir / "journal").glob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        submitted = {x["tool_call_id"]: x for x in journal if x["tool"] == "platform_run"}
        assert set(submitted) == {r["tool_call_id"] for r in runs.values()}
        session = tools.agent_session["agent_session_id"]
        assert all(x["agent_session_id"] == session for x in journal)
        assert all(
            x["acting_for"] == "alice" and x["effect"] == "write" for x in submitted.values()
        )
        assert {x["tool"]: x["effect"] for x in journal}["platform_results"] == "read"

        listed = await tools("platform_job", action="list")
        assert {j["job_id"] for j in listed["jobs"]} == {r["job_id"] for r in runs.values()}

        # The session's budget was two runs: a third waits for a person.
        over = await tools(
            "platform_estimate",
            revision_id=runs["base"]["revision"]["revision_id"],
            target_id=target,
        )
        assert over["requires_approval"], over
        third = await tools(
            "platform_run",
            revision_id=runs["base"]["revision"]["revision_id"],
            target_id=target,
            parameters={"threads": 2},
            idempotency_key="walkthrough-third",
        )
        assert third["status"] == "PENDING_APPROVAL" and third["approval_id"]
        with pytest.raises(ToolError, match="NOT_READY"):
            await tools("platform_results", run_id=third["run_id"])


async def test_platform_sessions_and_typed_refusals(mcp_server):
    async with mcp_server() as tools:
        created = await tools("platform_session", action="create", name="Elm Street study")
        listed = await tools("platform_session", action="list")
        assert created["platform_session_id"] in {
            s["platform_session_id"] for s in listed["sessions"]
        }
        with pytest.raises(ToolError, match=r"\[NOT_FOUND\].*retryable: false"):
            await tools("platform_job", action="status", job_id="job_missing")
        with pytest.raises(ToolError, match="No model file"):
            await tools("platform_model", action="upload", path="absent.inp", target_id="tgt_x")


async def test_the_toolset_is_seven_tools_with_three_read_only(mcp_server):
    async with mcp_server() as tools:
        listed = await tools.client.list_tools()
        platform = {t.name: t for t in listed if t.name.startswith("platform_")}
        assert set(platform) == {
            "platform_targets",
            "platform_session",
            "platform_model",
            "platform_estimate",
            "platform_run",
            "platform_job",
            "platform_results",
        }
        read_only = {n for n, t in platform.items() if t.annotations and t.annotations.readOnlyHint}
        assert read_only == {"platform_targets", "platform_estimate", "platform_results"}


def test_the_server_refuses_a_persons_token(monkeypatch):
    from openswmm_mcp.config import ServerSettings
    from openswmm_mcp.tools.platform import connect

    settings = ServerSettings(platform_url="http://127.0.0.1:9", platform_token="eyJhbGciOi.person")
    with pytest.raises(ValueError, match="agent session credential"):
        connect(settings)


def test_the_module_owns_no_local_solver_or_job_pool():
    """Governing plan §1A: no import of session.py or gym_support/jobs.py."""
    imported = set()
    for node in ast.walk(ast.parse(SOURCE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    ours = {m for m in imported if m.startswith("openswmm_mcp")}
    assert ours == {"openswmm_mcp.config", "openswmm_mcp.errors", "openswmm_mcp.journal"}
