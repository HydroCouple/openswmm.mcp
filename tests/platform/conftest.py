"""Fixtures for the platform toolset tests: a local control plane with a local-pool agent.

These tests need a checkout of hydrocouple.platform beside this repository (or
``HCP_PLATFORM_TESTS`` pointing at its ``tests`` folder): its fixtures start an embedded
PostgreSQL server, the real API on a loopback port and the real execution agent. Without the
checkout, or without PostgreSQL, the tests skip.

Everything a test writes lands under ``tests/_output/platform/<module>/<test>/``.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from fastmcp import Client

ROOT = Path(__file__).resolve().parents[2]
PLATFORM_TESTS = Path(
    os.environ.get("HCP_PLATFORM_TESTS", ROOT.parent / "hydrocouple.platform" / "tests")
)
os.environ.setdefault("HCP_TEST_ARTIFACTS", str(ROOT / "tests" / "_output" / "platform"))

if (PLATFORM_TESTS / "conftest.py").is_file():
    # The platform's own conftest, under a name that cannot collide with this one.
    _spec = importlib.util.spec_from_file_location(
        "hcp_test_fixtures", PLATFORM_TESTS / "conftest.py"
    )
    _fixtures = importlib.util.module_from_spec(_spec)
    sys.modules["hcp_test_fixtures"] = _fixtures
    sys.modules.setdefault("conftest", _fixtures)  # the platform's helpers import it by this name
    _spec.loader.exec_module(_fixtures)
    from hcp_test_fixtures import (  # noqa: F401  (pytest finds fixtures by name)
        artifact_dir,
        clock,
        database_url,
        jwks_file,
        make_token,
        pg_template,
        server,
        services,
        settings,
        signing_key,
    )
else:
    collect_ignore_glob = ["test_*.py"]


class ControlPlane:
    """The dispatcher, reconciler and execution agent of one target, looping in the background."""

    def __init__(self, services, world, server: str, root: Path):
        from hydrocouple_platform.agent import Agent, AgentClient
        from hydrocouple_platform.agent.adapters.local import LocalPoolAdapter
        from hydrocouple_platform.contracts.jobs import EgressPolicy

        self.services, self.world, self.url = services, world, server
        self.adapter = LocalPoolAdapter(
            root / "agent-work", slots=2, keep_workdirs=True, grace_seconds=3
        )
        self.agent = Agent(
            AgentClient(server, world.credential),
            self.adapter,
            world.store,
            world.store_uri,
            EgressPolicy.SUMMARIES,
        )
        self.errors: list[Exception] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.services.dispatch()
                self.services.reconcile()
                self.agent.run_once()
            except Exception as exc:  # raised again by stop() so the test fails loudly
                self.errors.append(exc)
                return
            time.sleep(0.1)

    def start(self) -> ControlPlane:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)
        for handle in self.adapter.handles():
            self.adapter.cancel(handle)
        if self.errors:
            raise self.errors[0]

    def agent_session(self, **budget) -> dict:
        """An agent session the person 'alice' starts for an MCP client; its token is the
        only platform credential the MCP server is given."""
        from hydrocouple_platform.contracts.requests import AgentSessionCreate, Budget

        return self.services.create_agent_session(
            self.world.admin,
            AgentSessionCreate(
                project_id=self.world.project_id,
                client_name="openswmm.mcp tests",
                client_version="0",
                model="scripted",
                conversation_ref="walkthrough",
                budget=Budget(**budget),
            ),
        )


@pytest.fixture
def control_plane(services, server, artifact_dir):
    """A summaries-egress target with its agent running; skips when the engine cannot run."""
    from world import make_world

    plane = ControlPlane(services, make_world(services, artifact_dir), server, artifact_dir)
    plane.start()
    yield plane
    plane.stop()


class Tools:
    """Calls MCP tools through an in-memory client and returns their JSON results."""

    def __init__(self, client: Client) -> None:
        self.client = client

    async def __call__(self, tool: str, **arguments):
        result = await self.client.call_tool(tool, arguments)
        return result.structured_content


@pytest.fixture
def mcp_server(control_plane, artifact_dir, monkeypatch):
    """Starts an MCP server with the platform toolset for an agent session of a given budget:
    ``async with mcp_server(max_runs=2) as tools: ...``."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def start(toolsets: str = "core,platform", **budget):
        session = control_plane.agent_session(**budget)
        working = artifact_dir / "mcp"
        working.mkdir(exist_ok=True)
        monkeypatch.setenv("OPENSWMM_MCP_TOOLSETS", toolsets)
        monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(working))
        monkeypatch.setenv("OPENSWMM_MCP_LOG_LEVEL", "WARNING")
        monkeypatch.setenv("OPENSWMM_MCP_PLATFORM_URL", control_plane.url)
        monkeypatch.setenv("OPENSWMM_MCP_PLATFORM_TOKEN", session["agent_session_token"])
        monkeypatch.setenv("OPENSWMM_MCP_PLATFORM_PROJECT_ID", control_plane.world.project_id)
        from openswmm_mcp.config import ServerSettings
        from openswmm_mcp.server import build_server

        async with Client(build_server(ServerSettings())) as client:
            tools = Tools(client)
            tools.working_dir, tools.agent_session = working, session
            yield tools

    return start


async def wait_for_job(tools: Tools, job_id: str, timeout: float = 180.0) -> dict:
    """Poll platform_job until the job ends."""
    import asyncio

    deadline = time.monotonic() + timeout
    while True:
        view = await tools("platform_job", action="status", job_id=job_id)
        if view["state"] in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "REJECTED"}:
            return view
        assert time.monotonic() < deadline, view
        await asyncio.sleep(0.25)
