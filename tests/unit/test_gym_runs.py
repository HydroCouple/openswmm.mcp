"""Unit tests for episode rollout + interactive env tools (plan Phase 3).

Action/JSON plumbing is tested against real ``gymnasium`` spaces (pure
dependency, no engine). Episode and interactive-loop tests run the real
handle-based engine via ``openswmm_gymnasium`` (no mocks) and skip when
it is absent. Artifacts go to the reviewable ``tests/_output/`` tree
(CLAUDE.md §4.1).
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")
pytest.importorskip("gymnasium")

from openswmm_gymnasium.spec.envs import EnvManager

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.errors import ToolError

_REFERENCE_INP = (Path(__file__).parents[1] / "data" / "site_drainage_example.inp").resolve()
_OUTPUT_ROOT = Path(__file__).parents[1] / "_output" / "gym_runs"


class _Ctx:
    def __init__(self, working_dir: str, env_manager: EnvManager | None = None) -> None:
        self.lifespan_context = {
            "session_manager": None,
            "settings": ServerSettings(working_dir=working_dir),
            "env_manager": env_manager or EnvManager(),
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
def ctx(output_dir: Path) -> _Ctx:
    return _Ctx(working_dir=str(output_dir))


def _rtc_config(inp: str, **overrides) -> dict:
    cfg = {
        "env_type": "rtc",
        "inp_path": inp,
        "observations": {"node_depths": ["J1", "O1"]},
        "reward_terms": [{"kind": "flooding_volume", "params": {}}],
        "control_interval_steps": 4,
        "max_episode_steps": 4,
    }
    cfg.update(overrides)
    return cfg


# ---------------------------------------------------------------------------
# Episode rollouts against the real engine (no mocks)
# ---------------------------------------------------------------------------


def _copy_inp(output_dir: Path) -> str:
    dest = output_dir / "site_drainage_example.inp"
    shutil.copy(_REFERENCE_INP, dest)
    return str(dest)


@pytest.mark.integration
async def test_run_episode_constant_policy_writes_artifacts(ctx, output_dir):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.gym_support.run_tools import run_episode

    run_dir = output_dir / "run"
    summary = await run_episode(
        ctx,
        config=_rtc_config(_copy_inp(output_dir)),
        run_dir=str(run_dir),
        seed=1,
    )
    assert summary["steps"] == 4  # max_episode_steps
    assert summary["truncated"] and not summary["terminated"]

    # Reward components sum to the (sign-flipped) total: all registered
    # terms are costs-to-minimize, so total_reward == -sum(components).
    comp_sum = sum(summary["reward_components_total"].values())
    assert summary["total_reward"] == pytest.approx(-comp_sum, abs=1e-6)

    lines = (run_dir / "trajectory.jsonl").read_text().splitlines()
    assert len(lines) == 4
    first = json.loads(lines[0])
    assert set(first) >= {"env_step", "action", "reward", "reward_components", "observation"}
    assert json.loads((run_dir / "summary.json").read_text())["steps"] == 4


@pytest.mark.integration
async def test_run_episode_replay_stops_when_exhausted(ctx, output_dir):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.gym_support.run_tools import run_episode

    summary = await run_episode(
        ctx,
        config=_rtc_config(_copy_inp(output_dir), max_episode_steps=50),
        policy={"kind": "replay", "actions": [{}, {}, {}]},
        run_dir=str(output_dir / "run"),
    )
    assert summary["steps"] == 3
    assert summary["replay_exhausted"]


@pytest.mark.integration
async def test_run_episode_default_run_dir_beside_model(ctx, output_dir):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.gym_support.run_tools import run_episode

    inp = _copy_inp(output_dir)
    summary = await run_episode(ctx, config=_rtc_config(inp), seed=2)
    run_dir = Path(summary["run_dir"])
    assert run_dir.parent == Path(inp).parent / "gym_runs"
    assert (run_dir / "summary.json").exists()


# ---------------------------------------------------------------------------
# Interactive loop against the real engine (no mocks)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_interactive_loop_matches_direct_env(ctx, output_dir):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_gymnasium.spec.config import EnvConfig, build_env

    from openswmm_mcp.gym_support.run_tools import (
        env_close,
        env_open,
        env_reset,
        env_step,
        list_envs,
    )

    cfg = _rtc_config(_copy_inp(output_dir))

    opened = await env_open(ctx, env_id="loop", config=cfg)
    assert opened["action_space"]["type"] == "Dict"
    reset = await env_reset(ctx, env_id="loop", seed=3)

    # Direct env over a separate copy of the model (handle-based engine:
    # both solvers coexist).
    direct_dir = output_dir / "direct"
    direct_dir.mkdir()
    direct_inp = direct_dir / "site_drainage_example.inp"
    shutil.copy(_REFERENCE_INP, direct_inp)
    direct = build_env(EnvConfig(**_rtc_config(str(direct_inp))))
    try:
        direct_obs, _ = direct.reset(seed=3)
        assert reset["observation"] == pytest.approx(direct_obs.tolist())

        for expected_step in range(1, 4):
            stepped = await env_step(ctx, env_id="loop")
            d_obs, d_reward, d_term, d_trunc, _ = direct.step({"design": {}, "runtime": {}})
            assert stepped["step"] == expected_step
            assert stepped["observation"] == pytest.approx(d_obs.tolist())
            assert stepped["reward"] == pytest.approx(float(d_reward))
            assert (stepped["terminated"], stepped["truncated"]) == (d_term, d_trunc)
    finally:
        direct.close()

    listed = await list_envs(ctx)
    assert [e["env_id"] for e in listed["envs"]] == ["loop"]
    assert listed["envs"][0]["step_count"] == 3

    closed = await env_close(ctx, env_id="loop")
    assert closed["closed"]
    assert (await list_envs(ctx))["count"] == 0


@pytest.mark.integration
async def test_env_open_duplicate_and_capacity(ctx, output_dir):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.gym_support.run_tools import env_close, env_open

    ctx.lifespan_context["env_manager"] = EnvManager(max_envs=1)
    cfg = _rtc_config(_copy_inp(output_dir))

    await env_open(ctx, env_id="one", config=cfg)
    with pytest.raises(ToolError, match="already open"):
        await env_open(ctx, env_id="one", config=cfg)
    with pytest.raises(ToolError, match="MAX_SESSIONS_REACHED"):
        await env_open(ctx, env_id="two", config=cfg)
    await env_close(ctx, env_id="one")


@pytest.mark.integration
async def test_idle_envs_are_swept(ctx, output_dir):
    pytest.importorskip("openswmm_gymnasium")
    from openswmm_mcp.gym_support.run_tools import env_open, list_envs

    ctx.lifespan_context["env_manager"] = EnvManager(idle_timeout_s=0.05)
    await env_open(ctx, env_id="leaky", config=_rtc_config(_copy_inp(output_dir)))
    time.sleep(0.1)
    # Any manager operation sweeps idle envs (lazy cleanup).
    assert (await list_envs(ctx))["count"] == 0
