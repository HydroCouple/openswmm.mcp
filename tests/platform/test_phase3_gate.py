"""The Phase 3 gate of the ecosystem plan, scripted end to end.

1. A calibration returns a behavioral parameter set and prediction bands. A Latin hypercube
   over C11's roughness and S1's imperviousness (a discrete choice) of the site drainage
   example is scored, through this MCP's gym tools, by fit_to_observed terms against a
   synthetic twin: observations a known parameter set wrote, with 3 % noise. The true
   parameters fall inside the behavioral set, and its 5 to 95 % bands hold the noise-free
   series.
2. A joint design and operations study (env_type joint) of the twin-tank benchmark scores every
   candidate over three storms on an evaluator pool (a local-pool target), their rain
   uploaded as sidecars of the revision; the chosen design becomes a derived revision that
   carries them.
3. Both re-run from their study manifests, by seed, to identical evaluation logs.

Beyond the gate, the knowledge graph built from the manifests holds the parameter set, the
event set and both studies and passes every rule. Files, including gate3_report.json, stay
under tests/_output/platform/test_phase3_gate/.
"""

from __future__ import annotations

import asyncio
import csv
import json
import shutil
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("openswmm_gymnasium.optimize.uncertainty")
pytest.importorskip("scipy.stats", reason="the calibration samples a Latin hypercube")
pytest.importorskip("platypus", reason="the joint study searches with NSGA-II")
pytest.importorskip("loaders.platform_to_rows", reason="the graph needs water-ontology")

DATA = Path(__file__).resolve().parents[1] / "data"
TRUTH = {"link.roughness": [0.0125], "subcatchment.imperv_pct": [40.0]}
STORMS = {  # name: (day of January 2024, weight, intensities every ten minutes from 00:10)
    "short": (2, 0.5, [2.0, 7.0, 3.0, 1.0]),
    "long": (3, 0.3, [1.0, 2.5, 3.5, 3.5, 2.5, 1.0]),
    "burst": (4, 0.2, [12.0, 5.0]),
}


def calibration_spec(inp: str) -> dict:
    observed = {"path": "link.flow", "element_id": "C11", "observed": "data/observed.csv"}
    return {
        "env_type": "cip",
        "inp_path": inp,
        "observations": {"node_depths": ["J1"]},
        "design_factories": [
            {
                "kind": "field_design",
                "params": {"path": "link.roughness", "ids": ["C11"], "low": 0.008, "high": 0.03},
            },
            {
                "kind": "field_choice",
                "params": {
                    "path": "subcatchment.imperv_pct",
                    "ids": ["S1"],
                    "choices": [20, 40, 60, 80],
                },
            },
        ],
        "reward_terms": [
            {"kind": "fit_to_observed", "params": observed | {"metric": m}}
            for m in ("nse", "kge", "rmse")
        ],
        "objectives": ["nse_C11"],
    }


def joint_spec(inp: str) -> dict:
    return {
        "env_type": "joint",
        "inp_path": inp,
        "observations": {"node_depths": ["T1", "T2"]},
        "design_factories": [
            {
                "kind": "field_choice",
                "params": {"path": "link.barrels", "ids": ["OUT"], "choices": [1, 2, 3]},
            },
            {
                "kind": "node_max_depth",
                "params": {"node_ids": ["T1", "T2"], "low": 2.0, "high": 4.0},
            },
        ],
        "runtime_factories": [{"kind": "orifice_setting", "params": {"link_ids": ["ORIF"]}}],
        "reward_terms": [
            {"kind": "flooding_volume"},
            {"kind": "peak_outflow", "params": {"link_ids": ["OUT"]}},
        ],
        "control_interval_steps": 20,
        "events": [
            {
                "name": name,
                "series": f"rain/{name}.csv",
                "gage": "RG",
                "start": f"2024-01-{day:02d}T00:00:00",
                "end": f"2024-01-{day:02d}T04:00:00",
                "weight": weight,
            }
            for name, (day, weight, _) in STORMS.items()
        ],
        "event_aggregation": "weighted_mean",
    }


def write_twin(model: Path, folder: Path) -> dict[str, float]:
    """Observations of C11's flow from the true parameters, with 3 % noise, beside the model;
    returns the noise-free series by report time."""
    from openswmm.engine import OutLinkVar, OutputReader, Solver
    from openswmm_gymnasium._engine import SolverAdapter
    from openswmm_gymnasium.optimize import apply_decisions
    from openswmm_gymnasium.spec.config import EnvConfig

    folder.mkdir(parents=True)
    adapter = SolverAdapter(model, None, None)
    adapter.open()
    apply_decisions(EnvConfig.model_validate(calibration_spec(str(model))), TRUTH, adapter)
    adapter.solver.write(str(folder / "truth.inp"))
    adapter.close()
    solver = Solver(str(folder / "truth.inp"), str(folder / "truth.rpt"), str(folder / "truth.out"))
    solver.run()
    solver.destroy()
    reader = OutputReader(str(folder / "truth.out"))
    times = np.datetime_as_string(np.asarray(reader.period_times), unit="s").tolist()
    flows = np.asarray(reader.link_series("C11", int(OutLinkVar.FLOW)), dtype=float)
    reader.close()
    noisy = flows * (1.0 + 0.03 * np.random.default_rng(2026).standard_normal(flows.size))
    (model.parent / "data").mkdir()
    with (model.parent / "data" / "observed.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "C11"])
        writer.writerows([t, repr(float(q))] for t, q in zip(times, noisy, strict=True))
    with (folder / "noise_free.csv").open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "C11"])
        writer.writerows([t, repr(float(q))] for t, q in zip(times, flows, strict=True))
    return dict(zip(times, flows.tolist(), strict=True))


def write_storms(folder: Path) -> list[str]:
    (folder / "rain").mkdir()
    for name, (day, _, rain) in STORMS.items():
        rows = "".join(
            f"2024-01-{day:02d}T{(10 * (i + 1)) // 60:02d}:{(10 * (i + 1)) % 60:02d}:00,{v}\n"
            for i, v in enumerate(rain)
        )
        (folder / "rain" / f"{name}.csv").write_text("time,rain\n" + rows)
    return [f"rain/{name}.csv" for name in STORMS]


async def gym_done(tools, job_id: str) -> dict:
    for _ in range(3000):
        status = await tools("gym_job", action="status", job_id=job_id)
        if status["state"] in ("done", "failed", "cancelled"):
            break
        await asyncio.sleep(0.2)
    assert status["state"] == "done", status
    return status


async def test_phase3_gate(mcp_server, control_plane, artifact_dir, make_token):
    from harness.duckdb_harness import Harness
    from hydrocouple_platform.client import PlatformClient
    from hydrocouple_platform.contracts.studies import StudyManifest
    from hydrocouple_platform.gym import run_platform_study
    from loaders.platform_to_rows import PlatformLoader
    from openswmm_gymnasium.benchmarks.b01_twin_tank import SCENARIO_INP
    from openswmm_gymnasium.optimize import OptimizationConfig
    from openswmm_gymnasium.optimize.manifest import rerun
    from openswmm_gymnasium.spec.config import EnvConfig

    world, report = control_plane.world, {}
    async with mcp_server(toolsets="core,gym,platform") as tools:
        # 1. Calibration on a synthetic twin, as the calibrate-model skill runs it.
        model = tools.working_dir / "calibration" / "model.inp"
        model.parent.mkdir()
        shutil.copy(DATA / "site_drainage_example.inp", model)
        truth = write_twin(model, artifact_dir / "twin")
        rule = {"thresholds": [{"metric": "nse_C11", "at_least": 0.98}]}
        job = await tools(
            "gym_job",
            action="start",
            spec=calibration_spec(str(model)),
            optimization={"algorithm": "lhs", "budget": 40, "seed": 11, "acceptance": rule},
        )
        calibration_dir = Path((await gym_done(tools, job["job_id"]))["output_dir"])
        accepted = await tools("gym_score", action="behavioural", job_id=job["job_id"])
        members = accepted["members"]
        roughness = [m["decisions"]["link.roughness"][0] for m in members]
        imperv = {m["decisions"]["subcatchment.imperv_pct"][0] for m in members}
        assert 0 < len(members) < accepted["evaluated"]  # the rule rejects some
        assert min(roughness) <= TRUTH["link.roughness"][0] <= max(roughness)
        assert TRUTH["subcatchment.imperv_pct"][0] in imperv
        bands = await tools(
            "gym_score", action="bands", job_id=job["job_id"], series=["link.flow:C11"]
        )
        with Path(bands["files"]["link.flow:C11"]).open() as fh:
            rows = {r["time"]: r for r in csv.DictReader(fh)}
        shared = sorted(set(rows) & set(truth))
        tolerance = 1e-3 * max(truth.values())
        inside = [
            float(rows[t]["q0.05"]) - tolerance <= truth[t] <= float(rows[t]["q0.95"]) + tolerance
            for t in shared
        ]
        assert len(shared) >= len(truth) - 1
        coverage = sum(inside) / len(inside)
        assert coverage >= 0.95, coverage
        report["calibration"] = {
            "evaluated": accepted["evaluated"],
            "behavioural": len(members),
            "roughness_range": [min(roughness), max(roughness)],
            "imperv_choices": sorted(imperv),
            "truth": TRUTH,
            "noise_free_inside_5_95_band": coverage,
            "bands": bands["files"],
        }

        # 2. A joint design and operations study over three storms on the evaluator pool.
        twin_tank = tools.working_dir / "twin_tank" / "model.inp"
        twin_tank.parent.mkdir()
        shutil.copy(SCENARIO_INP, twin_tank)
        rain = write_storms(twin_tank.parent)
        base = await tools(
            "platform_model",
            action="upload",
            path=str(twin_tank),
            target_id=world.target_id,
            files=rain,
        )
        assert {f"model/{r}" for r in rain} <= set(base["files"])
        joint = EnvConfig.model_validate(joint_spec(str(twin_tank)))
        opt = OptimizationConfig(
            algorithm="nsga2",
            budget=12,
            population_size=6,
            seed=4,
            workers=0,
            on_failure="penalty",
        )
        with PlatformClient(control_plane.url, make_token("alice")) as person:
            result = run_platform_study(
                joint,
                opt,
                artifact_dir / "joint_study",
                client=person,
                project_id=world.project_id,
                revision_id=base["revision_id"],
                target_id=world.target_id,
                inp_path="model/model.inp",
                pool_size=2,
            )
        log = [
            json.loads(line)
            for line in (artifact_dir / "joint_study" / "evaluations.jsonl")
            .read_text()
            .splitlines()
        ]
        for record in log:
            assert not record.get("failed"), record
            for term in ("flooding_volume", "peak_outflow"):
                assert {f"{term}@{name}" for name in STORMS} <= set(record["metrics"])
        study_id = result["execution"]["platform"]["study_id"]
        derived = await tools(
            "platform_model",
            action="derive",
            path=str(twin_tank),
            study_id=study_id,
            evaluation=result["best"]["evaluation"],
        )
        derived_files = json.loads(
            (Path(derived["package_folder"]) / "revision-manifest.json").read_text()
        )["files"]
        assert {f"model/{r}" for r in rain} <= {f["path"] for f in derived_files}
        report["joint"] = {
            "study_id": study_id,
            "evaluations": len(log),
            "events": list(STORMS),
            "best": result["best"]["evaluation"],
            "derived_revision": derived["revision_id"],
        }

    # 3. Both studies again from their manifests, to identical evaluation logs.
    for name, manifest, model_path, original in (
        ("calibration", calibration_dir / "study-manifest.json", model, calibration_dir),
        (
            "joint",
            artifact_dir / "joint_study" / "study-manifest.json",
            twin_tank,
            artifact_dir / "joint_study",
        ),
    ):
        again = artifact_dir / f"rerun_{name}"
        rerun(manifest, model_path, again, workers=2)
        first = (original / "evaluations.jsonl").read_bytes()
        assert (again / "evaluations.jsonl").read_bytes() == first, name
        assert (
            StudyManifest.model_validate_json(manifest.read_text()).comparable()
            == StudyManifest.model_validate_json(
                (again / "study-manifest.json").read_text()
            ).comparable()
        )
        report.setdefault("reruns_identical", []).append(name)

    # Beyond the gate: the knowledge graph of both studies, the parameter set and the events.
    rows = artifact_dir / "graph_rows"
    loader = PlatformLoader("demo", "phase3")
    loader.add_revision(Path(base["package_folder"]) / "revision-manifest.json")
    loader.add_revision(Path(derived["package_folder"]) / "revision-manifest.json")
    joint_study = loader.add_study(artifact_dir / "joint_study" / "study-manifest.json")
    parameter_set = loader.add_parameter_set(
        Path(accepted["path"]), calibration_dir / "study-manifest.json"
    )
    loader.write(rows)
    harness = Harness()
    harness.load(rows)
    assert harness.violations() == []
    report["graph"] = {
        "joint_study": joint_study,
        "parameter_set": parameter_set,
        "rows": str(rows),
    }
    (artifact_dir / "gate3_report.json").write_text(json.dumps(report, indent=2) + "\n")
