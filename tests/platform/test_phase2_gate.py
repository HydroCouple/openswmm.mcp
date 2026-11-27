"""The Phase 2 gate of the ecosystem plan, scripted end to end.

1. An alternatives analysis of the site drainage example gives the same study manifest
   locally (through this MCP's gym_job) and on the platform (a local-pool target), apart from
   runtime fields.
2. The chosen alternative is a revision whose manifest names its base revision and its study.

Beyond the gate, the chosen revision's run gives the same binary output as a swmm.simulation@2
scenario of the base revision with the same design as overrides, and the knowledge graph
built from the manifests and this session's journal holds the lineage and passes every rule.
The plan says "on k3s": no cluster is reachable here, so the platform side is the local pool.
Files, including gate2_report.json, stay under tests/_output/platform/test_phase2_gate/.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

pytest.importorskip("openswmm_gymnasium.optimize.manifest")
pytest.importorskip("loaders.platform_to_rows", reason="the gate needs water-ontology")

DATA = Path(__file__).resolve().parents[1] / "data"
WIDTH = "subcatchment.width"
IDS = ["S1", "S2", "S3"]
DESIGNS = [  # the model's own widths first, then three alternatives
    {WIDTH: [1587.0, 1653.0, 1456.0]},
    {WIDTH: [400.0, 400.0, 400.0]},
    {WIDTH: [2900.0, 2900.0, 2900.0]},
    {WIDTH: [2900.0, 800.0, 2900.0]},
]


def spec(inp: str) -> dict:
    return {
        "env_type": "cip",
        "inp_path": inp,
        "observations": {"node_depths": ["J1"]},
        "design_factories": [
            {
                "kind": "field_design",
                "params": {"path": WIDTH, "ids": IDS, "low": 100.0, "high": 3000.0},
            }
        ],
        "reward_terms": [
            {"kind": "flooding_volume"},
            {"kind": "peak_outflow", "params": {"link_ids": ["C11"]}},
        ],
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def finished(tools, job_id: str, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        view = await tools("platform_job", action="status", job_id=job_id)
        if view["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "REJECTED"}:
            return view
        assert time.monotonic() < deadline, view
        await asyncio.sleep(0.25)


async def test_phase2_gate(mcp_server, control_plane, artifact_dir, make_token):
    from generators.linkml_to_sqlite import build
    from harness.duckdb_harness import Harness
    from hydrocouple_platform.client import PlatformClient
    from hydrocouple_platform.contracts.studies import StudyManifest
    from hydrocouple_platform.gym import run_platform_study
    from loaders.platform_to_rows import PlatformLoader
    from openswmm_gymnasium.optimize import OptimizationConfig
    from openswmm_gymnasium.spec.config import EnvConfig

    world, report = control_plane.world, {"designs": DESIGNS}
    optimization = {"algorithm": "alternatives", "candidates": DESIGNS, "seed": 1, "workers": 0}
    async with mcp_server(toolsets="core,gym,platform") as tools:
        model = tools.working_dir / "site_drainage" / "model.inp"
        model.parent.mkdir()
        shutil.copy(DATA / "site_drainage_example.inp", model)
        base = await tools(
            "platform_model", action="upload", path=str(model), target_id=world.target_id
        )

        # 1a. Locally, as the agent would: a gym_job on this machine.
        job = await tools(
            "gym_job", action="start", spec=spec(str(model)), optimization=optimization
        )
        for _ in range(1200):
            status = await tools("gym_job", action="status", job_id=job["job_id"])
            if status["state"] in ("done", "failed"):
                break
            await asyncio.sleep(0.1)
        assert status["state"] == "done", status
        local_dir = Path(
            (await tools("gym_job", action="results", job_id=job["job_id"]))["output_dir"]
        )

        # 1b. On the platform, as the person who owns the project runs a study.
        with PlatformClient(control_plane.url, make_token("alice")) as person:
            result = run_platform_study(
                EnvConfig.model_validate(spec(str(model))),
                OptimizationConfig(**optimization),
                artifact_dir / "platform_study",
                client=person,
                project_id=world.project_id,
                revision_id=base["revision_id"],
                target_id=world.target_id,
                inp_path="model/model.inp",
            )

        local, remote = (
            StudyManifest.model_validate_json((d / "study-manifest.json").read_text())
            for d in (local_dir, artifact_dir / "platform_study")
        )
        assert local.comparable() == remote.comparable()
        assert [e.decisions for e in remote.evaluations] == DESIGNS
        study_id = remote.runtime["study_id"]
        report["manifests_equal_apart_from_runtime"] = True
        report["runtime"] = {"local": local.runtime["executor"], "platform": study_id}

        # 2. The best alternative becomes a revision that names its base and its study.
        best = result["best"]
        derived = await tools(
            "platform_model",
            action="derive",
            path=str(model),
            study_id=study_id,
            evaluation=best["evaluation"],
        )
        lineage = derived["derived_from"]
        assert (
            lineage["revision_id"],
            lineage["revision_manifest_sha256"],
            lineage["study_id"],
            lineage["evaluation"],
        ) == (base["revision_id"], base["manifest_sha256"], study_id, best["evaluation"])
        report["chosen"] = {
            "evaluation": best["evaluation"],
            "objectives": best["objectives"],
            "decisions": best["decisions"],
            "revision": derived["revision_id"],
        }

        # Beyond the gate: the chosen revision runs as a scenario of the base would.
        overrides = [
            {"path": WIDTH, "id": i, "value": v}
            for i, v in zip(IDS, best["decisions"][WIDTH], strict=True)
        ]
        runs = {}
        for name, revision, version, parameters in (
            ("chosen", derived["revision_id"], "1", {}),
            ("scenario", base["revision_id"], "2", {"overrides": overrides}),
        ):
            accepted = await tools(
                "platform_run",
                revision_id=revision,
                target_id=world.target_id,
                workflow_version=version,
                parameters=parameters,
                idempotency_key=f"gate2-{name}",
            )
            assert (await finished(tools, accepted["job_id"]))["state"] == "SUCCEEDED"
            runs[name] = accepted
        with PlatformClient(control_plane.url, make_token("alice")) as person:
            outs = {
                n: person.download(a["run_id"], ["model.out"], artifact_dir / "downloads" / n)[
                    "model.out"
                ]
                for n, a in runs.items()
            }
        assert sha256(outs["chosen"]) == sha256(outs["scenario"])
        report["chosen_run_equals_scenario_run"] = True
        with pytest.raises(ToolError, match="not the model of revision"):
            other = tools.working_dir / "other.inp"
            other.write_text(model.read_text().replace("1587", "1588", 1))
            await tools(
                "platform_model", action="derive", path=str(other), study_id=study_id, evaluation=1
            )

    # The knowledge graph: both revisions, both studies and the journal.
    rows = artifact_dir / "graph_rows"
    loader = PlatformLoader("demo", "site-drainage")
    loader.add_revision(Path(base["package_folder"]) / "revision-manifest.json")
    chosen = loader.add_revision(Path(derived["package_folder"]) / "revision-manifest.json")
    platform_study = loader.add_study(artifact_dir / "platform_study" / "study-manifest.json")
    loader.add_study(local_dir / "study-manifest.json")
    for journal in (tools.working_dir / "journal").glob("*.jsonl"):
        loader.add_journal(journal)
    loader.write(rows)
    harness = Harness()
    harness.load(rows)
    assert harness.violations() == []
    snapshot = artifact_dir / "gate2.sqlite"
    build(rows, snapshot)
    with closing(sqlite3.connect(snapshot)) as db:
        (row,) = db.execute(
            "SELECT was_derived_from, derived_in_study, chosen_evaluation FROM analysis__revision "
            "WHERE iri = ?",
            (chosen,),
        ).fetchall()
        (pareto,) = db.execute(
            "SELECT count(*) FROM analysis__candidate WHERE of_study = ? AND is_pareto = 1",
            (platform_study,),
        ).fetchone()
    assert json.loads(row[0]) == [f"wsys:revision/demo/site-drainage/{base['manifest_sha256']}"]
    assert row[1] == platform_study and row[2] == best["evaluation"]
    report["graph"] = {
        "derived_revision": chosen,
        "was_derived_from": json.loads(row[0]),
        "derived_in_study": row[1],
        "pareto_candidates": pareto,
        "rows": str(rows),
    }
    (artifact_dir / "gate2_report.json").write_text(json.dumps(report, indent=2) + "\n")
