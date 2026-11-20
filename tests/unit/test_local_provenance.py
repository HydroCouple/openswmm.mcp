"""A local run writes platform-format manifests that the water ontology loads (plan 2.4).

Needs hydrocouple.platform (the ``platform`` extra) to write the manifests and water-ontology
to load them. Files land in ``tests/_output/test_local_provenance/``.
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path

import pytest

pytest.importorskip("hydrocouple_platform.packaging")


async def test_local_manifests_are_opt_in(tools, inp_path):
    await tools("open_model", path=inp_path, session_id="off")
    assert "provenance" not in await tools("run", session_id="off")


async def test_a_local_run_and_its_journal_load_as_a_platform_run(
    inp_path, output_dir, monkeypatch
):
    from fastmcp import Client

    from openswmm_mcp.server import build_server

    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    monkeypatch.setenv("OPENSWMM_MCP_LOCAL_MANIFESTS", "true")
    async with Client(build_server()) as client:

        async def tools(tool, **arguments):
            return (await client.call_tool(tool, arguments)).structured_content

        written = await _run_recorded(tools, inp_path)
    await _check(written, output_dir)


async def _run_recorded(tools, inp_path) -> dict:
    await tools("open_model", path=inp_path, session_id="loc")
    await tools(
        "set",
        session_id="loc",
        kind="subcatchment",
        changes=[{"id": "S1", "field": "width", "value": 300.0}],
    )
    return (await tools("run", session_id="loc"))["provenance"]


async def _check(written: dict, output_dir: Path) -> None:
    attempt = Path(written["attempt"])
    manifest = json.loads((attempt / "attempt.json").read_text())
    actor = manifest["provenance"]["actor"]
    journal = [json.loads(x) for x in Path(written["journal"]).read_text().splitlines()]
    (started,) = [x for x in journal if x["tool"] == "run"]
    assert actor["tool_call_id"] == started["tool_call_id"]
    assert actor["agent_session_id"] == started["journal_id"]
    assert actor["human_subject"] == getpass.getuser()
    assert {a["path"] for a in manifest["artifacts"]} == {
        "run.json",
        "model.rpt",
        "model.out",
        "summary.json",
    }
    snapshot = (attempt.parent / "model-as-run.inp").read_text()
    assert "300" in next(line for line in snapshot.splitlines() if line.startswith("S1 "))

    loaders = pytest.importorskip("loaders.platform_to_rows")
    from harness.duckdb_harness import CAT, Harness

    rows = output_dir / "rows"
    loader = loaders.PlatformLoader("local", "site-drainage")
    loader.add_revision(Path(written["revision_manifest"]))
    loader.add_attempt(attempt)
    calls = loader.add_journal(Path(written["journal"]))
    loader.write(rows)
    harness = Harness()
    harness.load(rows)
    assert harness.violations() == []
    (lineage,) = harness.con.execute(
        f"""SELECT revision_manifest_sha256, session_id, tool_call_id
            FROM {CAT}.reference.artifact_lineage WHERE artifact_iri LIKE '%summary.json'"""
    ).fetchall()
    assert lineage[1] == started["journal_id"] and lineage[2] == started["tool_call_id"]
    assert len(calls) == len(journal)
