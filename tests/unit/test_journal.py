"""The tool-call journal: one line per call, with the call's effect and checksums."""

from __future__ import annotations

import getpass
import json

import pytest
from fastmcp.exceptions import ToolError

from openswmm_mcp.journal import canonical, effect_of, sha256


def _lines(output_dir) -> list[dict]:
    (journal,) = (output_dir / "journal").glob("*.jsonl")
    return [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]


async def test_every_call_is_one_line_with_its_effect_and_checksums(tools, inp_path, output_dir):
    await tools("open_model", path=inp_path, session_id="j")
    arguments = {"session_id": "j", "target": "spatial", "method": "node_coords"}
    coords = await tools("inspect", **arguments)
    await tools(
        "set",
        session_id="j",
        kind="link",
        changes=[{"id": "C1", "field": "roughness", "value": 0.02}],
    )
    await tools(
        "call",
        session_id="j",
        target="options",
        method="set_item",
        args={"key": "INERTIAL_DAMPING", "value": "PARTIAL"},
    )
    with pytest.raises(ToolError):
        await tools("get", session_id="j", kind="node", fields=["depht"])

    lines = _lines(output_dir)
    assert [(x["sequence"], x["tool"], x["effect"]) for x in lines] == [
        (1, "open_model", "file"),
        (2, "inspect", "read"),
        (3, "set", "write"),
        (4, "call", "write"),
        (5, "get", "read"),
    ]
    assert len({x["tool_call_id"] for x in lines}) == 5
    assert len({x["journal_id"] for x in lines}) == 1
    assert [x["ok"] for x in lines] == [True, True, True, True, False]
    assert lines[4]["error"] == "VALIDATION_ERROR"
    assert [x["read_only"] for x in lines] == [False, True, False, False, True]
    # Checksums of the canonical JSON, never the content itself.
    assert lines[1]["arguments_sha256"] == sha256(arguments)
    assert lines[1]["result_sha256"] == sha256(coords)
    assert "node_coords" not in json.dumps(lines)
    assert all(x["started_at"] <= x["ended_at"] for x in lines)
    # No platform: the journal is the session, for the operating-system login.
    assert all(x["agent_session_id"] == x["journal_id"] for x in lines)
    assert lines[0]["acting_for"] == getpass.getuser()


def test_effects_follow_the_catalog_and_default_to_write():
    assert effect_of("call", {"target": "options", "method": "set_item"}) == "write"
    assert effect_of("call", {"target": "builder", "method": "add_node"}) == "structural"
    assert effect_of("call", {"target": "builder", "method": "write"}) == "file"
    assert effect_of("call", {"target": "forcing", "method": "no_such_method"}) == "write"
    assert effect_of("session", {"action": "list"}) == "read"
    assert effect_of("session", {"action": "clone"}) == "write"
    assert effect_of("platform_model", {"action": "upload"}) == "file"
    assert effect_of("gym_job", {"action": "start"}) == "write"
    assert canonical({"b": 1, "a": [1.5, "x"]}) == b'{"a":[1.5,"x"],"b":1}'
