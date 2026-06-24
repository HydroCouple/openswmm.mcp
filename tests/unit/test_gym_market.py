"""Integration tests for market-controller optimization (Phase 1).

Drives the full MCP path: a C{"market"} env config tuned by NSGA-II /
random-search over the reactive controller's cost-curve + PID params, then
C{gym_apply_design} writing the optimized C{market_config.tuned.json}. Runs the
real handle-based engine via C{openswmm_gymnasium} against the twin-tank
benchmark (controllable orifice C{ORIF}); skips when the gym extra is absent.
Artifacts land in the reviewable C{tests/_output/} tree (CLAUDE.md §4.1).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.gym_support.jobs import JobManager

_OUTPUT_ROOT = Path(__file__).parents[1] / "_output" / "gym_market"


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


async def _wait_for(job_manager: JobManager, job_id: str, *states: str, timeout: float = 300.0):
    waited = 0.0
    while True:
        snap = job_manager.get(job_id)
        if snap["state"] in states:
            return snap
        if waited >= timeout:
            pytest.fail(f"job {job_id} stuck in '{snap['state']}' (waited {timeout}s)")
        waited += 0.1
        await asyncio.sleep(0.1)


def _market_config(output_dir: Path) -> dict:
    from openswmm_gymnasium.benchmarks.b01_twin_tank import SCENARIO_INP

    inp = str(SCENARIO_INP)
    market = {
        "meta": {"model_path": inp, "output_dir": str(output_dir), "control_interval_seconds": 300},
        "cost_curves": {
            "buyer": {"type": "logistic", "onset": 0.7, "steepness": 15},
            "seller": {"type": "piecewise_linear", "onset": 0.3, "full": 1.0},
        },
        "agents": [
            {"id": "T1", "element_type": "node", "commodity": "conveyance",
             "stress_metric": "storage_fill", "curve": "buyer", "role": "buyer"},
            {"id": "T2", "element_type": "node", "commodity": "storage",
             "stress_metric": "storage_fill", "curve": "seller", "role": "seller"},
        ],
        "trade_routes": [
            {"structure_link_id": "ORIF", "buyer_agents": ["T1"],
             "seller_agents": ["T2"], "pid": {"kp": 2.0, "ki": 0.1}},
        ],
    }
    return {
        "env_type": "market",
        "inp_path": inp,
        "market_config": market,
        "observations": {"node_depths": ["T1", "T2"]},
        "reward_terms": [
            {"kind": "uncontrolled_discharge", "params": {"link_ids": ["OUT"]}},
            {"kind": "storage_underutilization", "params": {"node_ids": ["T1", "T2"]}},
        ],
        "rpt_path": str(output_dir / "b01.rpt"),
        "out_path": str(output_dir / "b01.out"),
    }


@pytest.mark.integration
async def test_market_random_search_then_apply(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.tools.gym_runs import get_job_results, start_optimization
    from openswmm_mcp.tools.gym_scoring import apply_design

    snap = await start_optimization(
        ctx,
        config=_market_config(output_dir),
        optimization={"algorithm": "random_search", "budget": 5, "seed": 3},
        output_dir=str(output_dir / "job"),
    )
    final = await _wait_for(job_manager, snap["job_id"], "done", "failed")
    assert final["state"] == "done", final["error"]

    results = await get_job_results(ctx, job_id=snap["job_id"])
    assert results["objective_names"] == ["uncontrolled_discharge", "storage_underutilization"]
    # 8 tuned policy params: buyer onset/ceiling/steepness, seller onset/ceiling, ORIF kp/ki/kd.
    assert set(results["best"]["decisions"]) == {
        "curve:buyer.onset", "curve:buyer.ceiling", "curve:buyer.steepness",
        "curve:seller.onset", "curve:seller.ceiling",
        "pid:ORIF.kp", "pid:ORIF.ki", "pid:ORIF.kd",
    }

    # Apply writes the tuned controller config (no model session for market).
    applied = await apply_design(ctx, job_id=snap["job_id"])
    assert applied["applied"] == "market_policy"
    tuned_path = Path(applied["market_config_path"])
    assert tuned_path.exists()

    from openswmm_gymnasium.config import MarketConfig

    tuned = MarketConfig.load(str(tuned_path))
    tuned.validate()  # the written file is a valid, complete market config


def _schedule_config(output_dir: Path) -> dict:
    from openswmm_gymnasium.benchmarks.b01_twin_tank import SCENARIO_INP

    inp = str(SCENARIO_INP)
    return {
        "env_type": "schedule",
        "inp_path": inp,
        "structure_ids": ["ORIF"],
        "n_points": 4,
        "control_interval_seconds": 300,
        "observations": {"node_depths": ["T1", "T2"]},
        "reward_terms": [
            {"kind": "uncontrolled_discharge", "params": {"link_ids": ["OUT"]}},
            {"kind": "storage_underutilization", "params": {"node_ids": ["T1", "T2"]}},
        ],
        "rpt_path": str(output_dir / "b01.rpt"),
        "out_path": str(output_dir / "b01.out"),
    }


@pytest.mark.integration
async def test_schedule_optimize_then_apply(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    import json

    from openswmm_mcp.tools.gym_runs import get_job_results, start_optimization
    from openswmm_mcp.tools.gym_scoring import apply_design

    snap = await start_optimization(
        ctx,
        config=_schedule_config(output_dir),
        optimization={"algorithm": "random_search", "budget": 5, "seed": 4},
        output_dir=str(output_dir / "job"),
    )
    final = await _wait_for(job_manager, snap["job_id"], "done", "failed")
    assert final["state"] == "done", final["error"]

    results = await get_job_results(ctx, job_id=snap["job_id"])
    # 1 structure x 4 points = 4 schedule decision variables.
    assert set(results["best"]["decisions"]) == {f"sched:ORIF[{k}]" for k in range(4)}

    applied = await apply_design(ctx, job_id=snap["job_id"])
    assert applied["applied"] == "schedule_policy"
    sched_path = Path(applied["schedule_path"])
    assert sched_path.exists()
    written = json.loads(sched_path.read_text())
    assert written["structure_ids"] == ["ORIF"]
    assert len(written["schedule"]["ORIF"]) == 4
    assert all(0.0 <= v <= 1.0 for v in written["schedule"]["ORIF"])


@pytest.mark.integration
async def test_market_nsga2_front(ctx, output_dir, job_manager):
    pytest.importorskip("openswmm_gymnasium")
    pytest.importorskip("platypus")
    from openswmm_mcp.tools.gym_runs import get_job_results, start_optimization

    snap = await start_optimization(
        ctx,
        config=_market_config(output_dir),
        optimization={"algorithm": "nsga2", "budget": 12, "population_size": 6, "seed": 2},
        output_dir=str(output_dir / "nsga2"),
    )
    final = await _wait_for(job_manager, snap["job_id"], "done", "failed")
    assert final["state"] == "done", final["error"]
    results = await get_job_results(ctx, job_id=snap["job_id"])
    assert results["pareto"]
    assert results["evaluations_count"] == 12
