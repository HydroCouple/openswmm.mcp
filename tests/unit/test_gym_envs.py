"""Unit tests for the gym_* capability/config tools (plan Phase 2).

Capability and CRUD tools are pure (no gym extra); validation tools run
against the real engine and skip when ``openswmm_gymnasium`` is absent.
All files are written to the reviewable ``tests/_output/`` tree
(CLAUDE.md §4.1).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("fastmcp")

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.errors import ToolError
from openswmm_mcp.tools.gym_envs import (
    create_env_config,
    delete_env_config,
    get_env_config,
    list_capabilities,
    list_env_configs,
    validate_env_config,
)

_REFERENCE_INP = (Path(__file__).parents[1] / "data" / "site_drainage_example.inp").resolve()
_OUTPUT_ROOT = Path(__file__).parents[1] / "_output" / "gym_envs"


class _Ctx:
    """FakeContext carrying ServerSettings (gym tools use get_settings)."""

    def __init__(self, working_dir: str) -> None:
        self.lifespan_context = {
            "session_manager": None,
            "settings": ServerSettings(working_dir=working_dir),
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


def _rtc_config(inp: str = "model.inp") -> dict:
    return {
        "env_type": "rtc",
        "inp_path": inp,
        "observations": {"node_depths": ["J1"]},
        "reward_terms": [{"kind": "flooding_volume", "params": {}}],
    }


# ---------------------------------------------------------------------------
# Capability discovery (pure)
# ---------------------------------------------------------------------------


async def test_list_capabilities_structure(ctx):
    result = await list_capabilities(ctx)
    assert set(result["env_types"]) == {
        "rtc",
        "cip",
        "joint",
        "mo_rtc",
        "market",
        "schedule",
        "control_curve",
    }
    caps = result["capabilities"]
    assert set(caps) == {
        "reward_term",
        "runtime_factory",
        "design_factory",
        "wrapper",
        "policy_factory",
    }
    flooding = next(c for c in caps["reward_term"] if c["kind"] == "flooding_volume")
    assert flooding["description"]
    assert flooding["params_schema"]["type"] == "object"
    # Spec §9 criterion 1: control_curve is discoverable under policy_factory
    # with a complete params_schema.
    control_curve = next(c for c in caps["policy_factory"] if c["kind"] == "control_curve")
    assert control_curve["description"]
    assert control_curve["params_schema"]["type"] == "object"


async def test_list_capabilities_surfaces_env_and_observation_schema(ctx):
    # Issue #2: the EnvConfig envelope, ObservationSpec schema, the valid
    # observation feature keys, and worked examples must be discoverable
    # without reading source.
    result = await list_capabilities(ctx)
    assert result["env_config_schema"]["title"] == "EnvConfig"
    assert result["observation_spec_schema"]["title"] == "ObservationSpec"
    feats = result["observation_features"]
    assert "node_depths" in feats and "link_flows" in feats
    # Every observation_feature is a real ObservationSpec field.
    assert set(feats) <= set(result["observation_spec_schema"]["properties"])
    # A worked example per common env_type, each carrying its own env_type.
    for env_type in ("cip", "rtc", "control_curve", "schedule"):
        assert result["examples"][env_type]["env_type"] == env_type


async def test_observations_validation_error_enumerates_feature_keys(ctx):
    # Issue #2: the IDs-as-keys mistake (observations={"J1":"depthN"}) must
    # yield an error that names the valid feature keys, not a bare
    # "Extra inputs are not permitted".
    bad = {
        "env_type": "rtc",
        "inp_path": "m.inp",
        "observations": {"J1": "depthN"},
        "reward_terms": [{"kind": "flooding_volume", "params": {}}],
    }
    with pytest.raises(ToolError) as exc:
        await create_env_config(ctx, name="bad_obs", config=bad)
    msg = str(exc.value)
    assert "node_depths" in msg
    assert "not the IDs directly" in msg


async def test_json_string_config_is_accepted(ctx, output_dir):
    # Issue #1: a client that serializes the config object as a JSON *string*
    # must still succeed (defensive coercion), exactly like passing the dict.
    import json

    cfg = (await list_capabilities(ctx))["examples"]["cip"]
    created = await create_env_config(ctx, name="from_str", config=json.dumps(cfg))
    assert created["summary"]["env_type"] == "cip"


# ---------------------------------------------------------------------------
# Config CRUD via tools (pure)
# ---------------------------------------------------------------------------


async def test_create_get_list_delete_round_trip(ctx, output_dir):
    created = await create_env_config(ctx, name="baseline", config=_rtc_config())
    # Default store root is <working_dir>/gym_configs (plan §7.1).
    assert created["path"] == str(output_dir / "gym_configs" / "baseline.json")
    assert created["summary"]["env_type"] == "rtc"

    got = await get_env_config(ctx, name="baseline")
    assert got["config"]["inp_path"] == "model.inp"
    assert got["config"]["observations"]["node_depths"] == ["J1"]

    listed = await list_env_configs(ctx)
    assert listed["names"] == ["baseline"]
    assert listed["count"] == 1

    deleted = await delete_env_config(ctx, name="baseline")
    assert not Path(deleted["deleted"]).exists()
    assert (await list_env_configs(ctx))["names"] == []


async def test_create_honors_explicit_config_dir(ctx, output_dir):
    alt = output_dir / "elsewhere"
    created = await create_env_config(ctx, name="alt", config=_rtc_config(), config_dir=str(alt))
    assert created["path"] == str(alt / "alt.json")
    listed = await list_env_configs(ctx, config_dir=str(alt))
    assert listed["names"] == ["alt"]
    # The default location is untouched.
    assert (await list_env_configs(ctx))["names"] == []


async def test_create_invalid_config_raises_validation_error(ctx):
    bad = _rtc_config()
    bad["reward_terms"] = [{"kind": "bogus_term", "params": {}}]
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        await create_env_config(ctx, name="bad", config=bad)

    with pytest.raises(ToolError, match="Invalid environment config"):
        await create_env_config(ctx, name="bad2", config={"env_type": "rtc"})


async def test_create_duplicate_requires_overwrite(ctx):
    await create_env_config(ctx, name="dup", config=_rtc_config())
    with pytest.raises(ToolError, match="already exists"):
        await create_env_config(ctx, name="dup", config=_rtc_config())
    updated = await create_env_config(
        ctx, name="dup", config=_rtc_config("other.inp"), overwrite=True
    )
    assert updated["summary"]["inp_path"] == "other.inp"


# ---------------------------------------------------------------------------
# Validation against the real engine (gym extra required)
# ---------------------------------------------------------------------------


async def test_validate_requires_exactly_one_of_name_or_config(ctx):
    pytest.importorskip("openswmm_gymnasium")
    with pytest.raises(ToolError, match="exactly one"):
        await validate_env_config(ctx)
    with pytest.raises(ToolError, match="exactly one"):
        await validate_env_config(ctx, name="x", config=_rtc_config())


@pytest.mark.integration
async def test_validate_inline_config_reports_spaces(ctx, output_dir):
    pytest.importorskip("openswmm_gymnasium")
    inp = output_dir / "site_drainage_example.inp"
    shutil.copy(_REFERENCE_INP, inp)

    cfg = {
        "env_type": "rtc",
        "inp_path": str(inp),
        "observations": {"node_depths": ["J1", "O1"], "link_flows": ["C1"]},
        "reward_terms": [{"kind": "flooding_volume", "params": {}}],
        "max_episode_steps": 3,
    }
    result = await validate_env_config(ctx, config=cfg)
    assert result["valid"] is True
    assert result["observation_size"] == 3
    assert result["action_space"]["type"] == "Dict"
    assert set(result["action_space"]["spaces"]) == {"design", "runtime"}


@pytest.mark.integration
async def test_validate_stored_config_with_bad_element_id(ctx, output_dir):
    pytest.importorskip("openswmm_gymnasium")
    inp = output_dir / "site_drainage_example.inp"
    shutil.copy(_REFERENCE_INP, inp)

    cfg = _rtc_config(str(inp))
    cfg["observations"] = {"node_depths": ["NOT_A_NODE"]}
    await create_env_config(ctx, name="bad-ids", config=cfg)
    with pytest.raises(ToolError):
        await validate_env_config(ctx, name="bad-ids")
