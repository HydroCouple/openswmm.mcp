"""gym_job: one design scored now, and an alternatives job (ecosystem plan 2.2).

Runs the real engine; files land in ``tests/_output/test_gym_alternatives/``.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("openswmm_gymnasium")

from fastmcp import Client  # noqa: E402

DATA = Path(__file__).parents[1] / "data"  # site_drainage_example.inp floods
WIDTH = "subcatchment.width"


def _spec(folder: Path) -> dict:
    shutil.copy(DATA / "site_drainage_example.inp", folder / "model.inp")
    return {
        "env_type": "cip",
        "inp_path": str(folder / "model.inp"),
        "rpt_path": str(folder / "model.rpt"),
        "out_path": str(folder / "model.out"),
        "observations": {"node_depths": ["J1"]},
        "design_factories": [
            {
                "kind": "field_design",
                "params": {"path": WIDTH, "ids": ["S1"], "low": 100.0, "high": 3000.0},
            }
        ],
        "reward_terms": [{"kind": "flooding_volume"}],
    }


async def test_evaluate_scores_the_baseline_and_a_given_design(output_dir, monkeypatch):
    monkeypatch.setenv("OPENSWMM_MCP_TOOLSETS", "core,gym")
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    from openswmm_mcp.server import build_server

    spec = _spec(output_dir)
    async with Client(build_server()) as client:

        async def gym_job(**arguments):
            return (await client.call_tool("gym_job", arguments)).structured_content

        baseline = await gym_job(action="evaluate", spec=spec)
        assert baseline["baseline"] is True and baseline["decisions"] == {WIDTH: [1587.0]}
        narrow = await gym_job(action="evaluate", spec=spec, decisions={WIDTH: [300.0]})
        assert narrow["baseline"] is False
        assert narrow["objectives"]["flooding_volume"] != baseline["objectives"]["flooding_volume"]

        started = await gym_job(
            action="start",
            spec=spec,
            optimization={
                "algorithm": "alternatives",
                "workers": 0,
                "candidates": [{WIDTH: [1587.0]}, {WIDTH: [300.0]}],
            },
        )
        for _ in range(600):
            status = await gym_job(action="status", job_id=started["job_id"])
            if status["state"] in ("done", "failed"):
                break
            await asyncio.sleep(0.1)
        assert status["state"] == "done", status
        results = await gym_job(action="results", job_id=started["job_id"])
        log = Path(results["output_dir"]) / "evaluations.jsonl"
        records = [json.loads(line) for line in log.read_text().splitlines()]
        assert [r["objectives"] for r in records] == [baseline["objectives"], narrow["objectives"]]
