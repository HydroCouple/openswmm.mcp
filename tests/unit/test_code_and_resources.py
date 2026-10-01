"""run_python (opt-in), resources, prompts and bundled skills."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastmcp import Client

from openswmm_mcp import catalog as cat
from openswmm_mcp.config import ServerSettings
from openswmm_mcp.server import build_server

SKILLS = Path(__file__).resolve().parents[2] / "src" / "openswmm_mcp" / "skills"
PROMPTS = Path(__file__).resolve().parents[2] / "src" / "openswmm_mcp" / "prompts"
TOOLS = {
    "open_model",
    "run",
    "session",
    "save",
    "describe",
    "find",
    "get",
    "set",
    "call",
    "edit",
    "timeseries",
    "report",
    "compare",
    "export",
    "gym_describe",
    "gym_config",
    "gym_env",
    "gym_job",
    "gym_score",
    "use_skill",
}


async def test_run_python_sees_the_session(output_dir, inp_path, monkeypatch):
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    server = build_server(ServerSettings(working_dir=str(output_dir), enable_python=True))
    async with Client(server) as client:
        await client.call_tool("open_model", {"path": inp_path})
        result = await client.call_tool(
            "run_python",
            {
                "session_id": "default",
                "code": "print(len(solver.nodes))\nresult = sorted(n.id for n in solver.nodes)[:2]",
            },
        )
    assert result.structured_content == {"result": ["J1", "J10"], "stdout": "12\n"}


async def test_resources_and_prompts(tools, inp_path, output_dir, monkeypatch):
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    async with Client(build_server()) as client:
        await client.call_tool("open_model", {"path": inp_path, "session_id": "r"})
        index = json.loads((await client.read_resource("swmm://catalog"))[0].text)
        assert "node" in index
        node = json.loads((await client.read_resource("swmm://catalog/node"))[0].text)
        assert any(m["name"] == "depth" for m in node["members"])
        sessions = json.loads((await client.read_resource("swmm://sessions"))[0].text)
        assert [s["session_id"] for s in sessions] == ["r"]
        summary = json.loads((await client.read_resource("swmm://session/r/summary"))[0].text)
        assert summary["counts"]["node"] == 12
        skills = json.loads((await client.read_resource("swmm://skills"))[0].text)
        assert {s["name"] for s in skills} >= {"capacity-assessment", "calibrate-model"}
        prompts = {p.name for p in await client.list_prompts()}
        assert {"analyze_model", "diagnose_flooding", "use_skill"} <= prompts


@pytest.mark.parametrize(
    "path", sorted([*SKILLS.rglob("*.md"), *PROMPTS.glob("*.py")]), ids=lambda p: p.name
)
def test_skills_and_prompts_name_only_real_tools_and_fields(path):
    text = path.read_text(encoding="utf-8")
    called = set(re.findall(r"\b([a-z_]+)\(", text))
    tool_like = {
        c
        for c in called
        if c.startswith(
            (
                "gym_",
                "query_",
                "lifecycle_",
                "analysis_",
                "building_",
                "editing_",
                "links_",
                "nodes_",
            )
        )
    }
    assert tool_like <= TOOLS, f"unknown tools {tool_like - TOOLS}"
    for kind, fields in re.findall(r"kind='(\w+)', fields=\[([^\]]*)\]", text):
        for field in re.findall(r"'([\w.]+)'", fields):
            cat.field(kind, field)
