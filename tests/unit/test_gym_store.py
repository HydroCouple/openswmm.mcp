"""Unit tests for GymStore JSON persistence (plan Phase 1, §7.1).

Pure-Python: no engine or gym extra required. Per CLAUDE.md §4.1 all
store files are written under the reviewable ``tests/_output/`` tree.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.gym_support.config import EnvConfig, ObservationSpec
from openswmm_mcp.gym_support.store import GymStore

_OUTPUT_ROOT = Path(__file__).parents[1] / "_output" / "gym_store"


@pytest.fixture
def store(request) -> GymStore:
    """A GymStore rooted in a fresh, reviewable per-test directory."""
    path = _OUTPUT_ROOT / request.node.name
    if path.exists():
        shutil.rmtree(path)
    return GymStore(path)


def _config(inp: str = "model.inp") -> EnvConfig:
    return EnvConfig(
        env_type="rtc",
        inp_path=inp,
        observations=ObservationSpec(node_depths=["J1"]),
        reward_terms=[{"kind": "flooding_volume", "params": {}}],
    )


def test_save_and_get_round_trip(store: GymStore):
    cfg = _config()
    path = store.save_config("baseline", cfg)
    assert path.exists() and path.name == "baseline.json"
    assert store.get_config("baseline") == cfg


def test_configs_survive_store_reload(store: GymStore):
    store.save_config("baseline", _config())
    # A brand-new store over the same directory sees the same configs:
    # the disk is the store (plan §7.1).
    reloaded = GymStore(store.config_dir)
    assert reloaded.list_configs() == ["baseline"]
    assert reloaded.get_config("baseline") == _config()


def test_list_configs_sorted_and_empty(store: GymStore):
    assert store.list_configs() == []
    store.save_config("b-config", _config())
    store.save_config("a-config", _config())
    assert store.list_configs() == ["a-config", "b-config"]


def test_duplicate_name_rejected_unless_overwrite(store: GymStore):
    store.save_config("dup", _config())
    with pytest.raises(ToolError, match="already exists"):
        store.save_config("dup", _config())
    store.save_config("dup", _config("other.inp"), overwrite=True)
    assert store.get_config("dup").inp_path == "other.inp"


def test_get_missing_lists_available(store: GymStore):
    store.save_config("present", _config())
    with pytest.raises(ToolError) as excinfo:
        store.get_config("absent")
    msg = str(excinfo.value)
    assert "ELEMENT_NOT_FOUND" in msg and "present" in msg


def test_delete_config(store: GymStore):
    store.save_config("doomed", _config())
    removed = store.delete_config("doomed")
    assert not removed.exists()
    assert store.list_configs() == []
    with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND"):
        store.delete_config("doomed")


@pytest.mark.parametrize("bad_name", ["../escape", "a/b", "", ".hidden", "name with spaces"])
def test_unsafe_names_rejected(store: GymStore, bad_name: str):
    with pytest.raises(ToolError, match="Invalid config name"):
        store.save_config(bad_name, _config())


def test_corrupt_json_raises_actionable_error(store: GymStore):
    store.save_config("ok", _config())
    bad = store.config_dir / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError, match="corrupt or invalid"):
        store.get_config("broken")
    # Schema-invalid (but parseable) content is also caught.
    worse = store.config_dir / "wrong.json"
    worse.write_text(json.dumps({"config": {"env_type": "nope"}}), encoding="utf-8")
    with pytest.raises(ToolError, match="corrupt or invalid"):
        store.get_config("wrong")
    # Healthy neighbours are unaffected.
    assert store.get_config("ok") == _config()


def test_saved_file_is_human_readable(store: GymStore):
    """§7.1: users can review/edit/version the JSON directly."""
    path = store.save_config("readable", _config())
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["name"] == "readable"
    assert payload["config"]["env_type"] == "rtc"
    assert payload["config"]["observations"]["node_depths"] == ["J1"]
