"""Fixtures for the openswmm.mcp unit tests.

Tests run against the real engine on ``data/site_drainage_model.inp`` (12 nodes,
11 conduits, 7 subcatchments, 1 rain gage, 1 pollutant; 30 h at a 5 s routing
step). Everything a test writes lands in the reviewable ``tests/_output/`` tree
(CLAUDE.md §4.1), one folder per test, replaced on each run.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client

DATA = Path(__file__).parent / "data"
OUTPUT_ROOT = Path(__file__).resolve().parents[1] / "_output"


@pytest.fixture
def output_dir(request) -> Path:
    module = request.node.module.__name__.rsplit(".", 1)[-1]
    path = OUTPUT_ROOT / module / request.node.name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path


@pytest.fixture
def inp_path(output_dir: Path) -> str:
    dest = output_dir / "site_drainage_model.inp"
    shutil.copy(DATA / "site_drainage_model.inp", dest)
    return str(dest)


@pytest.fixture
async def session_manager(output_dir: Path):
    from openswmm_mcp.session import SessionManager

    manager = SessionManager(max_sessions=10, working_dir=str(output_dir))
    try:
        yield manager
    finally:
        await manager.cleanup_all()


class Tools:
    """Calls MCP tools through an in-memory client and returns their JSON results."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def __call__(self, tool: str, **arguments: Any) -> Any:
        result = await self._client.call_tool(tool, arguments)
        return result.structured_content


@pytest.fixture
async def tools(output_dir: Path, monkeypatch):
    """A client to a fresh core server whose working directory is the test's folder."""
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    monkeypatch.setenv("OPENSWMM_MCP_LOG_LEVEL", "WARNING")
    from openswmm_mcp.server import build_server

    async with Client(build_server()) as client:
        yield Tools(client)
