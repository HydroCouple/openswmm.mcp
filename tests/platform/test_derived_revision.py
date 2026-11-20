"""A study's chosen design becomes a revision that names where it came from (plan 2.6).

A person runs an alternatives study on the platform; the agent, through the MCP, derives the
best evaluation onto the base model and uploads it. The derived model's own values are the
evaluation's design, and scored as a baseline they give the evaluation's objectives.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

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


async def test_the_best_alternative_becomes_a_derived_revision(
    mcp_server, control_plane, artifact_dir, make_token
):
    from hydrocouple_platform.client import PlatformClient
    from hydrocouple_platform.gym import run_platform_study
    from openswmm_gymnasium.optimize import (
        OptimizationConfig,
        baseline_decisions,
        evaluate_decisions,
    )
    from openswmm_gymnasium.spec.config import EnvConfig

    world = control_plane.world
    async with mcp_server() as tools:
        base_model = tools.working_dir / "base" / "model.inp"
        base_model.parent.mkdir()
        shutil.copy(DATA / "site_drainage_example.inp", base_model)
        base = await tools(
            "platform_model", action="upload", path=str(base_model), target_id=world.target_id
        )

        designs = [{WIDTH: [1587.0, 1653.0]}, {WIDTH: [300.0, 300.0]}, {WIDTH: [2900.0, 2900.0]}]
        opt = OptimizationConfig(algorithm="alternatives", candidates=designs, workers=0)
        config = EnvConfig.model_validate(env_config(str(base_model)))
        with PlatformClient(control_plane.url, make_token("alice")) as person:
            result = run_platform_study(
                config,
                opt,
                artifact_dir / "study",
                client=person,
                project_id=world.project_id,
                revision_id=base["revision_id"],
                target_id=world.target_id,
                inp_path="model/model.inp",
            )
        best = result["best"]

        derived = await tools(
            "platform_model",
            action="derive",
            path=str(base_model),
            study_id=json.loads((artifact_dir / "study" / "study-manifest.json").read_text())[
                "runtime"
            ]["study_id"],
            evaluation=best["evaluation"],
        )
        lineage = derived["derived_from"]
        assert lineage["revision_id"] == base["revision_id"]
        assert lineage["revision_manifest_sha256"] == base["manifest_sha256"]
        assert lineage["evaluation"] == best["evaluation"] and lineage["study_id"].startswith(
            "std_"
        )
        assert lineage["decisions"] == best["decisions"]
        assert derived["state"] == "READY"

        derived_model = Path(derived["package_folder"]).parent / "model.inp"
        as_derived = config.model_copy(update={"inp_path": str(derived_model)})
        assert baseline_decisions(as_derived) == best["decisions"]
        scored = evaluate_decisions(as_derived, best["decisions"])
        assert scored["objectives"] == best["objectives"]

        other = tools.working_dir / "other" / "model.inp"
        other.parent.mkdir()
        other.write_text(base_model.read_text().replace("1587", "1588", 1))
        with pytest.raises(ToolError, match="is not the model of revision"):
            await tools(
                "platform_model",
                action="derive",
                path=str(other),
                study_id=lineage["study_id"],
                evaluation=1,
            )
