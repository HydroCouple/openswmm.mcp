"""An optimization through the MCP alone (M7, work package 7.5).

An agent session estimates a study, lets a gymnasium optimizer run it from this server, and
proposes a batch of its own on a second study. Every evaluation traces by checksum to the
chunk that recorded it and to the tool call that proposed it, and the journal tells the
study tool's reads from its writes.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("openswmm_gymnasium.optimize.manifest")

DATA = Path(__file__).resolve().parents[1] / "data"
WIDTH = "subcatchment.width"


def env_config(inp: str) -> dict:
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
        "reward_terms": [{"kind": "flooding_volume"}],
    }


async def wait_for(tools, optimization_id: str, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        view = await tools("platform_study", action="status", optimization_id=optimization_id)
        if view["state"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return view
        assert time.monotonic() < deadline, view
        await asyncio.sleep(0.25)


def journal(working_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for path in sorted((working_dir / "journal").glob("jr_*.jsonl"))
        for line in path.read_text().splitlines()
    ]


async def test_an_agent_optimizes_on_the_platform_through_mcp_alone(
    mcp_server, control_plane, artifact_dir
):
    from hydrocouple_platform.contracts.studies import StudyLineage
    from hydrocouple_platform.db import transaction
    from hydrocouple_platform.db.models import AuditRecord
    from sqlalchemy import select
    from world import read_from

    world, services = control_plane.world, control_plane.services
    async with mcp_server(max_evaluations=40) as tools:
        hints = {t.name: t.annotations for t in await tools.client.list_tools()}
        assert hints["platform_job"].readOnlyHint  # cancelling moved to platform_run
        assert not (hints["platform_study"] and hints["platform_study"].readOnlyHint)
        choices = await tools("platform_targets")
        assert choices["budget"]["remaining"]["evaluations"] == 40

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

        # A gymnasium optimizer runs the study from this server.
        search = {"algorithm": "random_search", "budget": 6, "seed": 5, "workers": 0}
        estimate = await tools("platform_study", action="estimate", optimization=search, **study)
        assert estimate["ok"] and estimate["evaluations"] == 6
        assert not estimate["requires_approval"] and estimate["estimate_id"]
        assert len(estimate["dimensions"]) == 2  # the candidate order
        started = await tools(
            "platform_study",
            action="optimize",
            optimization=search,
            estimate_id=estimate["estimate_id"],
            **study,
        )
        assert started["study_id"]  # known as soon as the platform accepted it
        done = await wait_for(tools, started["optimization_id"])
        assert done["state"] == "COMPLETED" and done["evaluations"] == 6, done
        results = await tools(
            "platform_study", action="results", optimization_id=started["optimization_id"]
        )
        assert results["result"]["best"]["objectives"]
        lineage = StudyLineage.model_validate_json(
            Path(results["files"]["study-lineage.json"]).read_text()
        )
        records = [
            json.loads(line)
            for line in Path(results["files"]["evaluation-lineage.jsonl"]).read_text().splitlines()
        ]
        assert [r["evaluation"] for r in records] == [1, 2, 3, 4, 5, 6]
        assert lineage.check_evaluations(records, read_from(world.store, artifact_dir)) == []
        assert {r["proposed_by"]["tool_call_id"] for r in records} == {started["tool_call_id"]}
        assert {r["proposed_by"]["agent_session_id"] for r in records} == {
            tools.agent_session["agent_session_id"]
        }

        # The agent proposes candidates itself.
        own = await tools(
            "platform_study",
            action="create",
            optimization={"algorithm": "random_search", "budget": 2, "seed": 1, "workers": 0},
            **study,
        )
        batch = await tools(
            "platform_study",
            action="submit",
            study_id=own["study_id"],
            candidates=[[500.0, 600.0], [2500.0, 2600.0]],
            wait_seconds=120,
        )
        assert batch["state"] == "DONE" and len(batch["results"]) == 2, batch
        await tools("platform_study", action="close", study_id=own["study_id"])
        assert (await tools("platform_study", action="results", study_id=own["study_id"]))["best"]
        proposed = services.list_evaluations(world.admin, own["study_id"])["evaluations"]
        assert {e["proposed_by"]["tool_call_id"] for e in proposed} == {batch["tool_call_id"]}

        # A run is cancelled through platform_run now.
        run = await tools(
            "platform_run", revision_id=revision["revision_id"], target_id=world.target_id
        )
        cancelled = await tools("platform_run", action="cancel", job_id=run["job_id"])
        assert cancelled["job_id"] == run["job_id"]

    calls = {line["tool_call_id"]: line for line in journal(tools.working_dir)}
    assert calls[started["tool_call_id"]]["tool"] == "platform_study"
    assert calls[started["tool_call_id"]]["effect"] == "write"
    effects = [line["effect"] for line in calls.values() if line["tool"] == "platform_study"]
    assert effects.count("write") == 4  # optimize, create, submit, close; the rest are reads
    assert set(effects) == {"read", "write"}
    upload = next(line for line in calls.values() if line["tool"] == "platform_model")
    with transaction(services._factory) as s:
        audit = s.scalar(
            select(AuditRecord).where(
                AuditRecord.action == "revision.create",
                AuditRecord.object_id == revision["revision_id"],
            )
        )
    assert audit.actor["tool_call_id"] == upload["tool_call_id"]  # the header reached it
