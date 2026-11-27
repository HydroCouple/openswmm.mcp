"""The Phase 5 gate of the ecosystem plan (compositions and scale), scripted end to end.

1. Two-component compositions from the engine's examples run through the platform: split-water
   (a 2D surface and a 1D network exchanging water, the engine's qualified split) and
   split-material (the pair exchanging water and pollutants, experimental in the engine's own
   terms), each packaged as an authored composition and run by ``composition.run@1`` on a
   local-pool target with a composition host (HydroCoupleComposer headless). One run manifest
   and one attempt manifest name both components; the material run's attempt marks it
   experimental; each component's outputs equal a direct run of the host on the same files,
   byte for byte. The knowledge graph holds each composition, its components and their
   connection, and passes every rule.
2. Study leasing is measured at the pool size the first real ensemble needs (128 tasks, decided
   November 20): hydrocouple.platform's lease measurement, run here, evaluates every candidate
   once and reports lease and completion latency.

The host comes from ``HCP_TEST_COMPOSER`` (the executable) and ``HCP_TEST_COMPONENT_PATH`` (the
directories holding the engine component module); without them the test skips. Files,
including gate5_report.json, stay under tests/_output/platform/test_phase5_gate/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("loaders.platform_to_rows", reason="the graph needs water-ontology")

ROOT = Path(__file__).resolve().parents[2]
PLATFORM = Path(
    os.environ.get("HCP_PLATFORM_TESTS", ROOT.parent / "hydrocouple.platform" / "tests")
)
COMPOSITIONS = PLATFORM / "data" / "compositions"
HOST = os.environ.get("HCP_TEST_COMPOSER")
COMPONENT_PATHS = [p for p in os.environ.get("HCP_TEST_COMPONENT_PATH", "").split(os.pathsep) if p]
EXAMPLES = {"split-water": "qualified", "split-material": "experimental"}

pytestmark = pytest.mark.skipif(
    not (HOST and COMPONENT_PATHS),
    reason="set HCP_TEST_COMPOSER and HCP_TEST_COMPONENT_PATH to a composition host",
)


def direct_run(name: str, folder: Path) -> Path:
    """The host run directly on the example's files, outside the platform."""
    shutil.copytree(COMPOSITIONS / name, folder)
    command = [HOST, "--run", str(folder / "composition.json")]
    for p in COMPONENT_PATHS:
        command += ["--components", p]
    done = subprocess.run(
        command,
        cwd=folder,
        capture_output=True,
        text=True,
        timeout=600,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    (folder / "composer.log").write_text(done.stdout + done.stderr)
    assert done.returncode == 0, done.stdout[-2000:]
    return folder


def test_phase5_gate(control_plane, services, artifact_dir, make_token):
    from hydrocouple_platform.agent.config import CompositionHostConfig
    from hydrocouple_platform.client import PlatformClient
    from hydrocouple_platform.contracts.manifests import canonical_json
    from hydrocouple_platform.packaging import package_composition
    from hydrocouple_platform.storage import sha256_file

    world, report = control_plane.world, {"compositions": {}}
    control_plane.adapter.composition_host = CompositionHostConfig(
        executable=HOST, component_paths=COMPONENT_PATHS
    )
    runs = {}
    with PlatformClient(control_plane.url, make_token("alice")) as person:
        for name in EXAMPLES:
            package = package_composition(
                COMPOSITIONS / name / "composition.json", artifact_dir / "package" / name
            )
            revision = person.upload_revision(
                package, project_id=world.project_id, target_id=world.target_id
            )
            accepted = person.submit_run(
                {
                    "project_id": world.project_id,
                    "revision_id": revision["revision_id"],
                    "workflow": "composition.run",
                    "workflow_version": "1",
                    "target_id": world.target_id,
                    "retry_budget": 0,
                },
                idempotency_key=f"gate5-{name}",
            )
            started = time.monotonic()
            view = person.wait_for_job(accepted["job_id"], timeout=600)
            assert view["state"] == "SUCCEEDED", view
            results = person.results(accepted["run_id"])
            outputs = [
                a["path"]
                for a in results["artifacts"]
                if a["path"].endswith(("model.out", "model.2d.h5"))
            ]
            folder = artifact_dir / "attempts" / name
            files = person.download(accepted["run_id"], ["run.json", *outputs], folder)
            published = person.attempt_manifest(accepted["run_id"])
            (folder / "attempt.json").write_bytes(canonical_json(published["attempt_manifest"]))
            assert sha256_file(folder / "attempt.json") == published["attempt_manifest_sha256"]
            runs[name] = (files, published["attempt_manifest"], time.monotonic() - started)

    # 1. One run manifest and one attempt manifest name both components; outputs equal a direct
    # host run byte for byte.
    for name, qualification in EXAMPLES.items():
        files, manifest, seconds = runs[name]
        run_manifest = json.loads(files["run.json"].read_text())
        assert [(c["id"], c["final_status"]) for c in run_manifest["components"]] == [
            ("surface", "Finished"),
            ("network", "Finished"),
        ]
        components = manifest["provenance"]["components"]
        assert [(c["id"], c["final_status"], c["qualification"]) for c in components] == [
            ("surface", "Finished", qualification),
            ("network", "Finished", qualification),
        ]
        direct = direct_run(name, artifact_dir / "direct" / name)
        equal = {}
        for component, rel in (
            ("network", "network-results/model.out"),
            ("surface", "surface-results/model.2d.h5"),
        ):
            published = files[f"components/{component}/{Path(rel).name}"]
            equal[component] = sha256_file(published) == sha256_file(direct / rel)
        assert all(equal.values()), (name, equal)
        report["compositions"][name] = {
            "components": components,
            "run_manifest_components": run_manifest["components"],
            "outputs_equal_direct_run": equal,
            "seconds": round(seconds, 1),
        }

    # The graph of both compositions and their runs.
    from harness.duckdb_harness import Harness
    from loaders.platform_to_rows import PlatformLoader

    for name in EXAMPLES:
        loader = PlatformLoader("demo", name)
        loader.add_revision(artifact_dir / "package" / name / "revision-manifest.json")
        loader.add_attempt(artifact_dir / "attempts" / name)
        rows = artifact_dir / "graph_rows" / name
        shutil.rmtree(rows, ignore_errors=True)
        loader.write(rows)
        harness = Harness()
        harness.load(rows)
        assert harness.violations() == []
        report["compositions"][name]["graph_rows"] = str(rows)

    # 2. Study leasing at the target pool size, with hydrocouple.platform's own measurement (in
    # this process's database; its emulated tasks run in worker processes of their own).
    sys.path.insert(0, str(PLATFORM / "integration"))
    import test_study_lease_scale as lease_scale

    (artifact_dir / "lease").mkdir(exist_ok=True)
    result = lease_scale.measure(services, artifact_dir / "lease", 128)
    assert result["evaluated"] == result["candidates"] == 1024
    report["study_leasing"] = {
        k: result[k]
        for k in ("pool", "candidates", "processes", "evaluations_per_second", "lease", "complete")
    }
    (artifact_dir / "gate5_report.json").write_text(json.dumps(report, indent=2) + "\n")
