"""Unit tests for background optimization jobs (plan Phase 4).

Config/metadata/bookkeeping tests are pure. Job-execution tests run the
real handle-based engine via ``openswmm_gymnasium`` (no mocks) and skip
when it is absent; the NSGA-II test additionally requires platypus-opt.
Artifacts land in the reviewable ``tests/_output/`` tree (CLAUDE.md
§4.1).
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

from pydantic import ValidationError

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.errors import ToolError
from openswmm_mcp.gym_support.config import EnvConfig, ObservationSpec
from openswmm_mcp.gym_support.jobs import (
    JobManager,
    OptimizationConfig,
    design_dimensions,
)

_REFERENCE_INP = (Path(__file__).parents[1] / "data" / "site_drainage_example.inp").resolve()
_OUTPUT_ROOT = Path(__file__).parents[1] / "_output" / "gym_jobs"


class _Ctx:
    def __init__(self, working_dir: str, job_manager: JobManager) -> None:
        self.lifespan_context = {
            "session_manager": None,
            "settings": ServerSettings(working_dir=working_dir),
            "job_manager": job_manager,
        }

    async def report_progress(self, *_):
        pass


@pytest.fixture
def output_dir(request) -> Path:
    path = _OUTPUT_ROOT / request.node.name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


@pytest.fixture
def job_manager() -> JobManager:
    manager = JobManager(max_workers=2)
    yield manager
    manager.shutdown()


@pytest.fixture
def ctx(output_dir: Path, job_manager: JobManager) -> _Ctx:
    return _Ctx(str(output_dir), job_manager)


def _cip_config(inp: str) -> EnvConfig:
    return EnvConfig(
        env_type="cip",
        inp_path=inp,
        design_factories=[
            {
                "kind": "link_roughness",
                "params": {"link_ids": ["C1", "C2"], "low": 0.011, "high": 0.025},
            },
            {
                "kind": "node_max_depth",
                "params": {"node_ids": ["J1"], "low": 1.0, "high": 3.0},
            },
        ],
        observations=ObservationSpec(node_depths=["J1"]),
        reward_terms=[
            {"kind": "flooding_volume", "params": {}},
            {"kind": "peak_outflow", "params": {"link_ids": ["C1"]}},
        ],
    )


async def _wait_for(job_manager: JobManager, job_id: str, *states: str, timeout: float = 120.0):
    """Poll until the job reaches one of *states*; return final snapshot."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        snap = job_manager.get(job_id)
        if snap["state"] in states:
            return snap
        if asyncio.get_event_loop().time() > deadline:
            pytest.fail(f"job {job_id} stuck in '{snap['state']}' (waited {timeout}s)")
        await asyncio.sleep(0.1)


# ---------------------------------------------------------------------------
# Pure: config validation, dimension metadata, bookkeeping errors
# ---------------------------------------------------------------------------


def test_optimization_config_defaults_and_validation():
    opt = OptimizationConfig()
    assert opt.algorithm == "random_search"
    assert opt.budget == 50

    with pytest.raises(ValidationError):
        OptimizationConfig(budget=0)
    with pytest.raises(ValidationError):
        OptimizationConfig(bogus_field=1)


def test_design_dimensions_labels_and_bounds():
    dims = design_dimensions(_cip_config("m.inp"))
    assert [d.key for d in dims] == ["link_roughness", "node_max_depth"]
    assert dims[0].labels == ("link_roughness:C1", "link_roughness:C2")
    assert dims[0].size == 2 and dims[1].size == 1
    assert (dims[0].low, dims[0].high) == (0.011, 0.025)
    assert dims[1].labels == ("node_max_depth:J1",)


def test_design_dimensions_requires_design_factories():
    rtc = EnvConfig(
        env_type="rtc", inp_path="m.inp", observations=ObservationSpec(node_depths=["J1"])
    )
    with pytest.raises(ToolError, match="design_factories"):
        design_dimensions(rtc)


def _control_curve_config(inp: str) -> EnvConfig:
    return EnvConfig(
        env_type="control_curve",
        inp_path=inp,
        control_interval_seconds=300,
        policy_factory={
            "kind": "control_curve",
            "params": {
                "assets": [
                    {
                        "link_id": "ORIF",
                        "obs_node": "T1",
                        "x_knots": [0.0, 0.5, 1.0],
                        "monotonic": "nonincreasing",
                    }
                ],
                "x_normalized": True,
            },
        },
        observations=ObservationSpec(node_depths=["T1", "T2"]),
        reward_terms=[
            {"kind": "uncontrolled_discharge", "params": {"link_ids": ["OUT"]}},
            {"kind": "storage_underutilization", "params": {"node_ids": ["T1", "T2"]}},
        ],
    )


def test_control_curve_dimensions_labels_and_bounds():
    pytest.importorskip("openswmm_gymnasium")
    dims = design_dimensions(_control_curve_config("m.inp"))
    assert [d.key for d in dims] == [
        "control_curve/ORIF/y[0]",
        "control_curve/ORIF/y[1]",
        "control_curve/ORIF/y[2]",
    ]
    assert all(d.size == 1 for d in dims)
    assert all((d.low, d.high) == (0.0, 1.0) for d in dims)


def test_precondition_gate_accepts_control_curve_rejects_bare_rtc(job_manager):
    pytest.importorskip("openswmm_gymnasium")
    # A control_curve config has a searchable static factory -> accepted.
    design_dimensions(_control_curve_config("m.inp"))  # no raise
    # A bare rtc config has neither design nor policy factory -> rejected.
    rtc = EnvConfig(
        env_type="rtc", inp_path="m.inp", observations=ObservationSpec(node_depths=["T1"])
    )
    with pytest.raises(ToolError, match="searchable static factory|design_factories"):
        design_dimensions(rtc)


def test_job_manager_unknown_job_and_algorithm(job_manager):
    with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
        job_manager.get("nope")
    with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
        job_manager.cancel("nope")
    with pytest.raises(ToolError, match="Unknown algorithm"):
        job_manager.start(
            _cip_config("m.inp"), OptimizationConfig(algorithm="simulated_annealing")
        )
    assert job_manager.list() == []


# ---------------------------------------------------------------------------
# Job execution against the real engine (no mocks)
# ---------------------------------------------------------------------------


def _copy_inp(output_dir: Path) -> str:
    dest = output_dir / "site_drainage_example.inp"
    shutil.copy(_REFERENCE_INP, dest)
    return str(dest)


@pytest.mark.integration
async def test_random_search_job_runs_in_background(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.tools.gym_runs import get_job, get_job_results, start_optimization

    snap = await start_optimization(
        ctx,
        config=_cip_config(_copy_inp(output_dir)).model_dump(mode="json"),
        optimization={"algorithm": "random_search", "budget": 4, "seed": 7},
        output_dir=str(output_dir / "job"),
    )
    job_id = snap["job_id"]
    assert snap["state"] in ("pending", "running")

    # Progress is monotone while the job runs in the background.
    seen = [snap["evaluations_done"]]
    while (await get_job(ctx, job_id=job_id))["state"] not in ("done", "failed"):
        seen.append((await get_job(ctx, job_id=job_id))["evaluations_done"])
        await asyncio.sleep(0.1)
    final = await get_job(ctx, job_id=job_id)
    assert final["state"] == "done", final["error"]
    assert seen == sorted(seen)
    assert final["evaluations_done"] == 4

    results = await get_job_results(ctx, job_id=job_id)
    assert results["evaluations_count"] == 4
    assert results["objective_names"] == ["flooding_volume", "peak_outflow"]
    # Decision vectors are labeled per factory and respect bounds.
    best = results["best"]
    assert set(best["decisions"]) == {"link_roughness", "node_max_depth"}
    assert all(0.011 <= v <= 0.025 for v in best["decisions"]["link_roughness"])
    # Pareto entries are drawn from the evaluation log.
    assert results["pareto"]
    # Artifacts exist and are reviewable.
    job_dir = output_dir / "job"
    assert json.loads((job_dir / "job.json").read_text())["job_id"] == job_id
    assert len((job_dir / "evaluations.jsonl").read_text().splitlines()) == 4
    assert json.loads((job_dir / "result.json").read_text())["evaluations_count"] == 4


@pytest.mark.integration
async def test_cancel_stops_between_evaluations(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.tools.gym_runs import cancel_job, get_job_results, start_optimization

    snap = await start_optimization(
        ctx,
        config=_cip_config(_copy_inp(output_dir)).model_dump(mode="json"),
        optimization={"algorithm": "random_search", "budget": 1000, "seed": 1},
    )
    job_id = snap["job_id"]
    # Let it get going, then cancel.
    await _wait_for(job_manager, job_id, "running")
    await cancel_job(ctx, job_id=job_id)
    final = await _wait_for(job_manager, job_id, "cancelled", "done")
    assert final["state"] == "cancelled"
    assert final["evaluations_done"] < 1000
    with pytest.raises(ToolError, match="INVALID_STATE"):
        await get_job_results(ctx, job_id=job_id)


@pytest.mark.integration
async def test_grid_search_covers_levels(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.tools.gym_runs import start_optimization

    cfg = _cip_config(_copy_inp(output_dir))
    # Single 1-component dimension keeps the grid tiny: 3 evaluations.
    cfg = cfg.model_copy(
        update={
            "design_factories": [cfg.design_factories[1]],
            "reward_terms": [cfg.reward_terms[0]],
        }
    )
    snap = await start_optimization(
        ctx,
        config=cfg.model_dump(mode="json"),
        optimization={"algorithm": "grid_search", "grid_levels": 3, "budget": 100},
        output_dir=str(output_dir / "grid"),
    )
    final = await _wait_for(job_manager, snap["job_id"], "done", "failed")
    assert final["state"] == "done", final["error"]
    assert final["evaluations_done"] == 3
    lines = (output_dir / "grid" / "evaluations.jsonl").read_text().splitlines()
    values = [json.loads(line)["decisions"]["node_max_depth"][0] for line in lines]
    assert values == pytest.approx([1.0, 2.0, 3.0])


@pytest.mark.integration
async def test_nsga2_produces_nondominated_front(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    pytest.importorskip("platypus")
    from openswmm_mcp.tools.gym_runs import get_job_results, start_optimization

    snap = await start_optimization(
        ctx,
        config=_cip_config(_copy_inp(output_dir)).model_dump(mode="json"),
        optimization={"algorithm": "nsga2", "budget": 8, "population_size": 4, "seed": 5},
        output_dir=str(output_dir / "nsga2"),
    )
    final = await _wait_for(job_manager, snap["job_id"], "done", "failed", timeout=300.0)
    assert final["state"] == "done", final["error"]

    results = await get_job_results(ctx, job_id=snap["job_id"])
    front = results["pareto"]
    assert front
    # No front member dominates another (both objectives are costs).
    for a in front:
        for b in front:
            if a is b:
                continue
            a_obj = [a["objectives"][n] for n in results["objective_names"]]
            b_obj = [b["objectives"][n] for n in results["objective_names"]]
            dominates = all(x <= y for x, y in zip(a_obj, b_obj)) and any(
                x < y for x, y in zip(a_obj, b_obj)
            )
            assert not dominates


def _copy_b01(output_dir: Path) -> str:
    from openswmm_gymnasium.benchmarks.b01_twin_tank import SCENARIO_INP

    dest = output_dir / "scenario.inp"
    shutil.copy(SCENARIO_INP, dest)
    return str(dest)


@pytest.mark.integration
async def test_control_curve_nsga2_front_and_decode(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    pytest.importorskip("platypus")
    from openswmm_mcp.tools.gym_runs import (
        decode_policy,
        get_job_results,
        start_optimization,
    )

    cfg = _control_curve_config(_copy_b01(output_dir))
    snap = await start_optimization(
        ctx,
        config=cfg.model_dump(mode="json"),
        optimization={"algorithm": "nsga2", "budget": 8, "population_size": 4, "seed": 5},
        output_dir=str(output_dir / "cc_nsga2"),
    )
    final = await _wait_for(job_manager, snap["job_id"], "done", "failed", timeout=300.0)
    assert final["state"] == "done", final["error"]

    results = await get_job_results(ctx, job_id=snap["job_id"])
    front = results["pareto"]
    assert front  # size >= 1
    # Decisions decode straight back to per-asset curves.
    decoded = await decode_policy(ctx, job_id=snap["job_id"], index="best")
    assert len(decoded["curves"]) == 1
    curve = decoded["curves"][0]
    assert curve["link_id"] == "ORIF"
    assert curve["obs_node"] == "T1"
    assert curve["x_knots"] == [0.0, 0.5, 1.0]
    assert len(curve["y_values"]) == 3
    # nonincreasing projection: the applied curve is monotone non-increasing.
    ys = curve["y_values"]
    assert all(ys[i + 1] <= ys[i] + 1e-9 for i in range(len(ys) - 1))


@pytest.mark.integration
async def test_decode_policy_rejects_non_control_curve(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.tools.gym_runs import decode_policy, start_optimization

    snap = await start_optimization(
        ctx,
        config=_cip_config(_copy_inp(output_dir)).model_dump(mode="json"),
        optimization={"algorithm": "random_search", "budget": 2, "seed": 1},
        output_dir=str(output_dir / "cip_for_decode"),
    )
    await _wait_for(job_manager, snap["job_id"], "done", "failed", timeout=120.0)
    with pytest.raises(ToolError, match="control_curve"):
        await decode_policy(ctx, job_id=snap["job_id"], index="best")


# ---------------------------------------------------------------------------
# Resolved-settings echo (#9) and JSON-string optimization arg (#1)
# ---------------------------------------------------------------------------


def test_optimization_config_resolved_includes_only_relevant_fields():
    # Issue #9: resolved() echoes the applied settings; algorithm-specific
    # fields appear only when they actually apply.
    nsga2 = OptimizationConfig(
        algorithm="nsga2", budget=600, population_size=24, seed=7
    ).resolved()
    assert nsga2 == {
        "algorithm": "nsga2", "budget": 600, "seed": 7, "population_size": 24
    }
    rnd = OptimizationConfig().resolved()
    assert rnd == {"algorithm": "random_search", "budget": 50, "seed": None}
    assert "population_size" not in rnd
    grid = OptimizationConfig(algorithm="grid_search", grid_levels=4).resolved()
    assert grid["grid_levels"] == 4 and "population_size" not in grid


@pytest.mark.integration
async def test_start_optimization_accepts_json_string_and_echoes_resolved(
    ctx, output_dir, job_manager
):
    # Issue #1 + #9: a JSON-*string* optimization arg is honored (not silently
    # downgraded), and the resolved settings are echoed in the snapshot and the
    # results so a downgrade would be unmissable.
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.tools.gym_runs import get_job_results, start_optimization

    snap = await start_optimization(
        ctx,
        config=_control_curve_config(_copy_b01(output_dir)).model_dump(mode="json"),
        optimization='{"algorithm": "random_search", "budget": 4, "seed": 3}',
        output_dir=str(output_dir / "json_str_opt"),
    )
    assert snap["optimization"] == {
        "algorithm": "random_search", "budget": 4, "seed": 3
    }
    final = await _wait_for(job_manager, snap["job_id"], "done", "failed")
    assert final["state"] == "done", final["error"]
    assert final["optimization"]["budget"] == 4

    results = await get_job_results(ctx, job_id=snap["job_id"])
    assert results["optimization"]["algorithm"] == "random_search"
    assert results["evaluations_count"] == 4
