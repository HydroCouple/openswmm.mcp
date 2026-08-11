"""Unit tests for scoring tools + the apply_design bridge (plan Phase 5).

Argument-shape helpers are pure; indicator values are checked against
direct ``openswmm_gymnasium.scoring`` calls, and ``apply_design`` is
verified by reading the mutated properties back from a real engine
session (no mocks). Artifacts land in ``tests/_output/`` (CLAUDE.md
§4.1).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.errors import ToolError
from openswmm_mcp.gym_support.jobs import JobManager
from openswmm_mcp.tools.gym_scoring import _as_matrix, _check_indicator_args, _pick_evaluation

_REFERENCE_INP = (Path(__file__).parents[1] / "data" / "site_drainage_example.inp").resolve()
_OUTPUT_ROOT = Path(__file__).parents[1] / "_output" / "gym_scoring"

_FRONT = [[1.0, 3.0], [2.0, 2.0], [3.0, 1.0]]
_REF_POINT = [4.0, 4.0]


class _Ctx:
    def __init__(self, working_dir: str, job_manager=None, session_manager=None) -> None:
        self.lifespan_context = {
            "session_manager": session_manager,
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


# ---------------------------------------------------------------------------
# Binding-layer JSON-string coercion (issue #1, real fix)
# ---------------------------------------------------------------------------


async def test_pareto_filter_accepts_json_string_front_via_binding_layer():
    # The real #1 fix: a client that serializes the 'front' array as a JSON
    # *string* must succeed through FastMCP's argument binding (a
    # BeforeValidator decodes it), NOT fail with a pydantic list/dict_type
    # error before the tool body ever runs. Goes through mcp.call_tool to
    # exercise that binding layer (a plain function call would bypass it).
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.server import mcp

    res = await mcp.call_tool(
        "gym_pareto_filter", {"front": "[[1.0, 2.0], [2.0, 1.0], [3.0, 3.0]]"}
    )
    data = getattr(res, "structured_content", None) or res.data
    assert data["input_count"] == 3
    # (3,3) is dominated; the non-dominated front is the other two corners.
    assert data["front"] == [[1.0, 2.0], [2.0, 1.0]]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_as_matrix_validation():
    matrix = _as_matrix("front", _FRONT)
    assert matrix.shape == (3, 2)
    for bad in (None, [], [[1.0], [1.0, 2.0]], "nope"):
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            _as_matrix("front", bad)


def test_check_indicator_args():
    _check_indicator_args("spread", {})
    _check_indicator_args("hypervolume", {"reference_point": _REF_POINT})
    with pytest.raises(ToolError, match="Unknown indicator"):
        _check_indicator_args("gd_plus", {})
    with pytest.raises(ToolError, match="requires reference_point"):
        _check_indicator_args("hypervolume", {})
    with pytest.raises(ToolError, match="ideal_point"):
        _check_indicator_args("normalized_hypervolume", {"reference_point": _REF_POINT})


def test_pick_evaluation(tmp_path):
    results = {"best": {"evaluation": 2, "objectives": {"f": 1.0}}}
    assert _pick_evaluation(results, str(tmp_path), "best")["evaluation"] == 2

    log = tmp_path / "evaluations.jsonl"
    log.write_text('{"evaluation": 1, "objectives": {"f": 9.0}}\n', encoding="utf-8")
    assert _pick_evaluation(results, str(tmp_path), 1)["objectives"]["f"] == 9.0
    with pytest.raises(ToolError, match="No evaluation #5"):
        _pick_evaluation(results, str(tmp_path), 5)
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        _pick_evaluation(results, str(tmp_path), "second")


# ---------------------------------------------------------------------------
# Indicators match direct scoring calls (gym extra required)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_score_front_matches_direct_calls(output_dir):
    pytest.importorskip("openswmm_gymnasium")
    import numpy as np
    from openswmm_gymnasium import scoring

    from openswmm_mcp.tools.gym_scoring import score_front

    ctx = _Ctx(str(output_dir))
    weights = [[1.0, 0.0], [0.5, 0.5], [0.0, 1.0]]
    ref_front = [[1.0, 1.0]]
    result = await score_front(
        ctx,
        indicators=[
            "hypervolume",
            "normalized_hypervolume",
            "igd",
            "igd_plus",
            "epsilon_indicator",
            "spread",
            "r2_indicator",
        ],
        front=_FRONT,
        reference_point=_REF_POINT,
        ideal_point=[0.0, 0.0],
        reference_front=ref_front,
        weights=weights,
    )
    front = np.asarray(_FRONT)
    expected = {
        "hypervolume": scoring.hypervolume(front, np.asarray(_REF_POINT)),
        "normalized_hypervolume": scoring.normalized_hypervolume(
            front, np.zeros(2), np.asarray(_REF_POINT)
        ),
        "igd": scoring.igd(front, np.asarray(ref_front)),
        "igd_plus": scoring.igd_plus(front, np.asarray(ref_front)),
        "epsilon_indicator": scoring.epsilon_indicator(front, np.asarray(ref_front)),
        "spread": scoring.spread(front),
        "r2_indicator": scoring.r2_indicator(front, np.asarray(weights), np.asarray(_REF_POINT)),
    }
    assert result["front_size"] == 3
    for name, value in expected.items():
        assert result["scores"][name] == pytest.approx(float(value)), name


@pytest.mark.integration
async def test_pareto_filter_inline(output_dir):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.tools.gym_scoring import pareto_filter

    ctx = _Ctx(str(output_dir))
    # [2.5, 2.5] is dominated by [2, 2]; the three corners survive.
    result = await pareto_filter(ctx, front=[*_FRONT, [2.5, 2.5]])
    assert result["input_count"] == 4
    assert result["indices"] == [0, 1, 2]

    with pytest.raises(ToolError, match="exactly one"):
        await pareto_filter(ctx)


# ---------------------------------------------------------------------------
# End-to-end: optimize -> compare -> apply to a live session (no mocks)
# ---------------------------------------------------------------------------


def _cip_config(inp: str) -> dict:
    return {
        "env_type": "cip",
        "inp_path": inp,
        "design_factories": [
            {
                "kind": "link_roughness",
                "params": {"link_ids": ["C1", "C2"], "low": 0.011, "high": 0.025},
            },
            {
                "kind": "node_max_depth",
                "params": {"node_ids": ["J1"], "low": 1.0, "high": 3.0},
            },
        ],
        "observations": {"node_depths": ["J1"]},
        "reward_terms": [
            {"kind": "flooding_volume", "params": {}},
            {"kind": "peak_outflow", "params": {"link_ids": ["C1"]}},
        ],
    }


async def _finished_job(ctx, job_manager, config: dict, out: Path, seed: int) -> str:
    from openswmm_mcp.tools.gym_runs import start_optimization

    snap = await start_optimization(
        ctx,
        config=config,
        optimization={"algorithm": "random_search", "budget": 3, "seed": seed},
        output_dir=str(out),
    )
    while job_manager.get(snap["job_id"])["state"] not in ("done", "failed"):
        await asyncio.sleep(0.1)
    final = job_manager.get(snap["job_id"])
    assert final["state"] == "done", final["error"]
    return snap["job_id"]


@pytest.mark.integration
async def test_compare_runs_and_apply_design(output_dir, session_manager, inp_path):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.tools.gym_scoring import apply_design, compare_runs

    job_manager = JobManager(max_workers=2)
    ctx = _Ctx(str(output_dir), job_manager=job_manager, session_manager=session_manager)
    try:
        inp = output_dir / "site_drainage_example.inp"
        shutil.copy(_REFERENCE_INP, inp)
        cfg = _cip_config(str(inp))
        job_a = await _finished_job(ctx, job_manager, cfg, output_dir / "a", seed=1)
        job_b = await _finished_job(ctx, job_manager, cfg, output_dir / "b", seed=2)

        compared = await compare_runs(
            ctx,
            job_ids=[job_a, job_b],
            indicators=["hypervolume", "spread"],
            reference_point=[1e9, 1e9],
        )
        assert [r["job_id"] for r in compared["runs"]] == [job_a, job_b]
        assert all("hypervolume" in r and "spread" in r for r in compared["runs"])

        with pytest.raises(ToolError, match="at least two"):
            await compare_runs(ctx, job_ids=[job_a], indicators=["spread"])

        # Apply the best design onto a live session and read it back.
        session = await session_manager.create_session(
            session_id="default", inp_path=inp_path, engine="openswmm"
        )
        session.backend.solver.open()
        session.state = "opened"

        applied = await apply_design(ctx, job_id=job_a, session_id="default")
        assert {a["element_id"] for a in applied["applied"]} == {"C1", "C2", "J1"}

        links, nodes = session.links, session.nodes
        for change in applied["applied"]:
            if change["kind"] == "link_roughness":
                idx = links.get_index(change["element_id"])
                assert links[idx].roughness == pytest.approx(change["value"], rel=1e-5)
                assert 0.011 <= change["value"] <= 0.025
            else:
                idx = nodes.get_index(change["element_id"])
                assert nodes[idx].max_depth == pytest.approx(change["value"], rel=1e-5)
    finally:
        job_manager.shutdown()
