"""The tool surface stays small enough for any MCP client to load.

v1 registered 663 tools (~515k characters of tool definitions, over 140k
tokens) and could not be loaded by Claude Desktop. These budgets fail the
build before that can happen again.
"""

from __future__ import annotations

import json

import pytest

from openswmm_mcp.config import ServerSettings
from openswmm_mcp.server import build_server

CORE = {
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
}
GYM = {"gym_describe", "gym_config", "gym_env", "gym_job", "gym_score"}
MAX_TOOLS = 20
MAX_CHARS = 40_000  # ~11k tokens for every tool definition together


async def _definitions(settings: ServerSettings) -> list[dict]:
    tools = await build_server(settings).list_tools()
    return [t.to_mcp_tool().model_dump(exclude_none=True) for t in tools]


async def test_core_tool_set():
    names = {d["name"] for d in await _definitions(ServerSettings(toolsets="core"))}
    assert names == CORE


@pytest.mark.parametrize("toolsets", ["core", "core,gym"])
async def test_tool_definitions_fit_the_budget(toolsets):
    definitions = await _definitions(ServerSettings(toolsets=toolsets, enable_python=True))
    size = len(json.dumps(definitions))
    assert len(definitions) <= MAX_TOOLS, [d["name"] for d in definitions]
    assert size <= MAX_CHARS, f"{size} characters of tool definitions"


async def test_gym_toolset_adds_five_tools():
    names = {d["name"] for d in await _definitions(ServerSettings(toolsets="core,gym"))}
    assert names == CORE | GYM


async def test_run_python_is_opt_in_and_stdio_only():
    async def names(**settings) -> set[str]:
        return {d["name"] for d in await _definitions(ServerSettings(**settings))}

    assert "run_python" not in await names()
    assert "run_python" in await names(enable_python=True)
    assert "run_python" not in await names(enable_python=True, transport="http")


@pytest.mark.parametrize("toolsets", ["core", "core,gym"])
async def test_stdio_handshake_as_a_desktop_client(toolsets, inp_path, output_dir):
    """Launch the server the way Claude Desktop does and use it over stdio."""
    import os
    import sys

    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    env = dict(
        os.environ,
        OPENSWMM_MCP_TOOLSETS=toolsets,
        OPENSWMM_MCP_WORKING_DIR=str(output_dir),
        OPENSWMM_MCP_LOG_LEVEL="WARNING",
    )
    transport = StdioTransport(
        sys.executable,
        ["-m", "openswmm_mcp"],
        env=env,
        cwd=str(output_dir),
        log_file=output_dir / "server_stderr.log",
    )
    async with Client(transport) as client:
        assert client.initialize_result.serverInfo.name
        tools = await client.list_tools()
        expected = CORE | GYM if "gym" in toolsets else CORE
        assert {t.name for t in tools} == expected
        size = len(json.dumps([t.model_dump(exclude_none=True) for t in tools]))
        assert size <= MAX_CHARS
        await client.call_tool("open_model", {"path": inp_path, "session_id": "d"})
        result = await client.call_tool("run", {"session_id": "d"})
        assert result.structured_content["finished"]
        report = await client.call_tool("report", {"session_id": "d", "name": "mass_balance"})
        assert report.structured_content
