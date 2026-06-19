"""Unit tests for EnvConfig schema validation and build_env (plan Phase 1).

Schema tests run everywhere. ``build_env`` tests construct real envs
against the bundled ``site_drainage_example.inp`` through the real
handle-based engine (no mocks) and skip when ``openswmm_gymnasium`` is
not installed.

Per CLAUDE.md §4.1, engine artifacts (.inp copy, .rpt, .out) are written
to the reviewable ``tests/_output/`` tree, not temp dirs.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from pydantic import ValidationError

from openswmm_mcp.errors import ToolError
from openswmm_mcp.gym_support.config import EnvConfig, ObservationSpec, build_env

_REFERENCE_INP = (Path(__file__).parents[1] / "data" / "site_drainage_example.inp").resolve()
_OUTPUT_ROOT = Path(__file__).parents[1] / "_output" / "gym_config"


@pytest.fixture
def output_dir(request) -> Path:
    """Per-test reviewable output directory under ``tests/_output/``."""
    path = _OUTPUT_ROOT / request.node.name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


@pytest.fixture
def gym_inp_path(output_dir: Path) -> str:
    """Copy of the reference INP inside the per-test output directory."""
    dest = output_dir / "site_drainage_example.inp"
    shutil.copy(_REFERENCE_INP, dest)
    return str(dest)


def _obs(**overrides) -> ObservationSpec:
    base = {"node_depths": ["J1"]}
    base.update(overrides)
    return ObservationSpec(**base)


# ---------------------------------------------------------------------------
# Schema validation (no gym extra required)
# ---------------------------------------------------------------------------


def test_minimal_rtc_config_valid():
    cfg = EnvConfig(env_type="rtc", inp_path="model.inp", observations=_obs())
    assert cfg.control_interval_steps == 1
    assert cfg.reward_terms == []


def test_config_round_trips_through_json():
    cfg = EnvConfig(
        env_type="joint",
        inp_path="model.inp",
        design_factories=[
            {"kind": "link_diameter", "params": {"link_ids": ["C1"], "low": 0.5, "high": 2.0}}
        ],
        runtime_factories=[{"kind": "orifice_setting", "params": {"link_ids": ["C2"]}}],
        observations=_obs(link_flows=["C1", "C2"], include_clock=True),
        reward_terms=[{"kind": "flooding_volume", "params": {}}],
        max_episode_steps=200,
    )
    again = EnvConfig.model_validate_json(cfg.model_dump_json())
    assert again == cfg


def test_empty_observations_rejected():
    with pytest.raises(ValidationError, match="at least one feature"):
        EnvConfig(env_type="rtc", inp_path="m.inp", observations=ObservationSpec())


def test_unknown_kind_rejected():
    with pytest.raises((ValidationError, ToolError), match="Unknown reward_term"):
        EnvConfig(
            env_type="rtc",
            inp_path="m.inp",
            observations=_obs(),
            reward_terms=[{"kind": "bogus_term", "params": {}}],
        )


def test_bad_kind_params_rejected():
    with pytest.raises((ValidationError, ToolError), match="Invalid params"):
        EnvConfig(
            env_type="rtc",
            inp_path="m.inp",
            observations=_obs(),
            runtime_factories=[{"kind": "orifice_setting", "params": {"link_ids": []}}],
        )


@pytest.mark.parametrize(
    ("env_type", "kwargs", "match"),
    [
        ("cip", {}, "requires design_factories"),
        ("joint", {}, "requires design_factories"),
        (
            "rtc",
            {
                "design_factories": [
                    {
                        "kind": "link_diameter",
                        "params": {"link_ids": ["C1"], "low": 0.5, "high": 2.0},
                    }
                ]
            },
            "does not accept design_factories",
        ),
        (
            "cip",
            {
                "design_factories": [
                    {
                        "kind": "link_diameter",
                        "params": {"link_ids": ["C1"], "low": 0.5, "high": 2.0},
                    }
                ],
                "runtime_factories": [
                    {"kind": "orifice_setting", "params": {"link_ids": ["C2"]}}
                ],
            },
            "does not accept runtime_factories",
        ),
        ("mo_rtc", {}, "requires at least one reward term"),
        (
            "mo_rtc",
            {"reward_terms": [{"kind": "flooding_volume", "params": {}}]},
            "requires both ideal_point and reference_point",
        ),
        (
            "mo_rtc",
            {
                "reward_terms": [{"kind": "flooding_volume", "params": {}}],
                "ideal_point": [0.0, 0.0],
                "reference_point": [1.0],
            },
            "one value per reward term",
        ),
        (
            "rtc",
            {"ideal_point": [0.0], "reference_point": [1.0]},
            "only valid for env_type 'mo_rtc'",
        ),
    ],
)
def test_env_type_constraints(env_type, kwargs, match):
    with pytest.raises(ValidationError, match=match):
        EnvConfig(env_type=env_type, inp_path="m.inp", observations=_obs(), **kwargs)


def test_control_interval_must_be_positive():
    with pytest.raises(ValidationError, match="control_interval_steps"):
        EnvConfig(
            env_type="rtc", inp_path="m.inp", observations=_obs(), control_interval_steps=0
        )


# ---------------------------------------------------------------------------
# build_env against the real engine (no mocks)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_build_env_rtc_reset_step_close(gym_inp_path):
    pytest.importorskip("openswmm_gymnasium")

    cfg = EnvConfig(
        env_type="rtc",
        inp_path=gym_inp_path,
        observations=ObservationSpec(
            node_depths=["J1", "O1"], link_flows=["C1"], include_clock=True
        ),
        reward_terms=[{"kind": "flooding_volume", "params": {}}],
        control_interval_steps=4,
        max_episode_steps=5,
    )
    env = build_env(cfg)
    try:
        obs, info = env.reset()
        # 2 node depths + 1 link flow + clock features
        assert obs.shape[0] >= 4
        terminated = truncated = False
        steps = 0
        while not (terminated or truncated):
            obs, reward, terminated, truncated, info = env.step(
                {"design": {}, "runtime": {}}
            )
            steps += 1
            assert steps <= 5
        assert truncated  # max_episode_steps=5 << full 6 h simulation
    finally:
        env.close()


@pytest.mark.integration
def test_build_env_cip_spaces_match_factories(gym_inp_path):
    pytest.importorskip("openswmm_gymnasium")

    cfg = EnvConfig(
        env_type="cip",
        inp_path=gym_inp_path,
        design_factories=[
            {
                "kind": "link_roughness",
                "params": {"link_ids": ["C1", "C2"], "low": 0.011, "high": 0.025},
            }
        ],
        observations=ObservationSpec(node_depths=["J1"]),
    )
    env = build_env(cfg)
    try:
        design_space = env.action_space["design"]["link_roughness"]
        assert design_space.shape == (2,)
        assert design_space.low[0] == pytest.approx(0.011)
        assert design_space.high[0] == pytest.approx(0.025)
    finally:
        env.close()


@pytest.mark.integration
def test_build_env_unknown_element_id_raises_engine_error(gym_inp_path):
    pytest.importorskip("openswmm_gymnasium")

    cfg = EnvConfig(
        env_type="rtc",
        inp_path=gym_inp_path,
        observations=ObservationSpec(node_depths=["NOT_A_NODE"]),
    )
    # Binding may fail at construction (wrapped into ToolError) or at
    # reset (raw engine lookup error); either way it must fail loudly,
    # never silently produce a zero-padded observation.
    with pytest.raises(Exception):
        env = build_env(cfg)
        try:
            env.reset()
        finally:
            env.close()


@pytest.mark.integration
def test_build_env_applies_wrappers(gym_inp_path, output_dir):
    pytest.importorskip("openswmm_gymnasium")

    cfg = EnvConfig(
        env_type="rtc",
        inp_path=gym_inp_path,
        observations=ObservationSpec(node_depths=["J1"]),
        max_episode_steps=2,
        wrappers=[
            {
                "kind": "record_trajectory",
                "params": {"output_dir": str(output_dir / "trajectories")},
            }
        ],
    )
    env = build_env(cfg)
    try:
        assert type(env).__name__ == "RecordTrajectory"
    finally:
        env.close()
