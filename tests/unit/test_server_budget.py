"""The tool surface stays small enough for any MCP client to load.

v1 registered 663 tools (~515k characters of tool definitions, over 140k
tokens) and could not be loaded by Claude Desktop. These budgets fail the
build before that can happen again.

The budgets cover every toolset together, including the seven ``platform_*``
tools the cloud execution platform plan (2026-10-15, §1A) and the one finding
tool the ecosystem plan (2026-11-20, work package 1.8) add later, so room for
them is reserved now.
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
    "inspect",
    "call",
    "edit",
    "timeseries",
    "report",
    "compare",
    "export",
}
GYM = {"gym_describe", "gym_config", "gym_env", "gym_job", "gym_score"}
#: platform_targets/session/model/estimate/run/job/results (platform plan §1A) and the
#: finding tool (ecosystem plan, work package 1.8).
PLATFORM_TOOLS_RESERVED = 8
PLATFORM_CHARS_RESERVED = 8 * 1_500
LEDGER = {"tally", "sources", "locate"}
MAX_TOOLS = 32  # 15 core + 5 gym + 8 platform + 3 ledger + run_python
MAX_CHARS = 40_000  # ~11k tokens for every tool definition together


async def _definitions(settings: ServerSettings) -> list[dict]:
    if "gym" in settings.toolsets.split(","):
        pytest.importorskip("openswmm_gymnasium")
    tools = await build_server(settings).list_tools()
    return [t.to_mcp_tool().model_dump(exclude_none=True) for t in tools]


async def test_core_tool_set():
    names = {d["name"] for d in await _definitions(ServerSettings(toolsets="core"))}
    assert names == CORE


@pytest.mark.parametrize("toolsets", ["core", "core,gym", "core,ledger", "core,gym,ledger"])
async def test_tool_definitions_fit_the_budget(toolsets):
    definitions = await _definitions(ServerSettings(toolsets=toolsets, enable_python=True))
    size = len(json.dumps(definitions))
    names = [d["name"] for d in definitions]
    assert len(names) + PLATFORM_TOOLS_RESERVED <= MAX_TOOLS, names
    assert size + PLATFORM_CHARS_RESERVED <= MAX_CHARS, f"{size} characters of tool definitions"


async def test_reads_can_be_approved_without_writes():
    """Read-only tools carry the read-only hint; the tools that change a model do not."""
    hints = {
        d["name"]: d.get("annotations", {})
        for d in await _definitions(ServerSettings(toolsets="core"))
    }
    read_only = {name for name, a in hints.items() if a.get("readOnlyHint")}
    assert read_only == {"describe", "find", "get", "inspect", "timeseries", "report", "compare"}
    for name in ("set", "call", "edit"):
        assert hints[name] == {"readOnlyHint": False, "destructiveHint": True}, name


async def test_every_toolset_together_fits_the_budget():
    """Exercise the actual combined tool surface, including the opt-in Python tool."""
    pytest.importorskip("hydrocouple_platform")
    definitions = await _definitions(
        ServerSettings(toolsets="core,gym,platform,ledger", enable_python=True)
    )
    names = [d["name"] for d in definitions]
    assert len(names) == 15 + 5 + 8 + 3 + 1 <= MAX_TOOLS, names
    assert len(json.dumps(definitions)) <= MAX_CHARS


async def test_gym_toolset_adds_five_tools():
    names = {d["name"] for d in await _definitions(ServerSettings(toolsets="core,gym"))}
    assert names == CORE | GYM


async def test_ledger_toolset_adds_three_tools():
    names = {d["name"] for d in await _definitions(ServerSettings(toolsets="core,ledger"))}
    assert names == CORE | LEDGER


async def test_ledger_is_off_by_default():
    names = {d["name"] for d in await _definitions(ServerSettings(toolsets="core"))}
    assert not names & LEDGER


async def test_run_python_is_opt_in_and_stdio_only():
    async def names(**settings) -> set[str]:
        return {d["name"] for d in await _definitions(ServerSettings(**settings))}

    assert "run_python" not in await names()
    assert "run_python" in await names(enable_python=True)
    assert "run_python" not in await names(enable_python=True, transport="http")


@pytest.mark.parametrize("toolsets", ["core", "core,gym"])
async def test_stdio_handshake_as_a_desktop_client(toolsets, inp_path, output_dir):
    """Launch the server the way Claude Desktop does and use it over stdio."""
    if "gym" in toolsets.split(","):
        pytest.importorskip("openswmm_gymnasium")
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
