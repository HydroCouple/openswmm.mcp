"""Unit tests for SessionManager and SimSession."""

from __future__ import annotations

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.session import SessionManager, SimSession

# ---------------------------------------------------------------------------
# TestSessionManager
# ---------------------------------------------------------------------------


class TestSessionManager:
    async def test_create_session(self, session_manager, tmp_inp):
        session = await session_manager.create_session("test", tmp_inp)
        assert session.state == "created"
        assert isinstance(session, SimSession)

    async def test_get_session(self, session_manager, tmp_inp):
        await session_manager.create_session("test", tmp_inp)
        session = await session_manager.get_session("test")
        assert session is not None
        assert isinstance(session, SimSession)

    async def test_get_nonexistent_session(self, session_manager):
        with pytest.raises(ToolError, match="SESSION_NOT_FOUND"):
            await session_manager.get_session("nonexistent")

    async def test_max_sessions(self, tmp_inp, tmp_path):
        sm = SessionManager(max_sessions=2, working_dir=str(tmp_path))
        await sm.create_session("a", tmp_inp)
        await sm.create_session("b", tmp_inp)
        with pytest.raises(ToolError, match="Maximum"):
            await sm.create_session("c", tmp_inp)

    async def test_duplicate_session(self, session_manager, tmp_inp):
        await session_manager.create_session("test", tmp_inp)
        with pytest.raises(ToolError, match="already exists"):
            await session_manager.create_session("test", tmp_inp)

    async def test_close_session(self, session_manager, tmp_inp):
        await session_manager.create_session("test", tmp_inp)
        await session_manager.close_session("test")
        with pytest.raises(ToolError):
            await session_manager.get_session("test")

    async def test_close_nonexistent_session(self, session_manager):
        with pytest.raises(ToolError, match="SESSION_NOT_FOUND"):
            await session_manager.close_session("nonexistent")

    async def test_list_sessions(self, session_manager, tmp_inp):
        await session_manager.create_session("a", tmp_inp)
        await session_manager.create_session("b", tmp_inp)
        sessions = await session_manager.list_sessions()
        assert len(sessions) == 2
        ids = {s["id"] for s in sessions}
        assert ids == {"a", "b"}

    async def test_list_sessions_empty(self, session_manager):
        sessions = await session_manager.list_sessions()
        assert sessions == []

    async def test_cleanup_all(self, session_manager, tmp_inp):
        await session_manager.create_session("a", tmp_inp)
        await session_manager.create_session("b", tmp_inp)
        await session_manager.cleanup_all()
        sessions = await session_manager.list_sessions()
        assert len(sessions) == 0

    async def test_session_working_dir_created(self, session_manager, tmp_inp):
        session = await session_manager.create_session("mydir", tmp_inp)
        assert session.working_dir.exists()
        assert session.working_dir.name == "mydir"

    async def test_default_rpt_and_out_paths(self, session_manager, tmp_inp):
        session = await session_manager.create_session("test", tmp_inp)
        solver = session.solver
        # The updated MockSolver uses `rpt` as the attribute set by __init__,
        # but SessionManager calls Solver(str(inp), rpt_path, out_path),
        # which maps to MockSolver(inp=..., rpt=..., out=...) and stores
        # as self.rpt_path and self.out_path.
        assert solver.rpt_path.endswith(".rpt")
        assert solver.out_path.endswith(".out")


# ---------------------------------------------------------------------------
# TestSimSession
# ---------------------------------------------------------------------------


class TestSimSession:
    def test_lazy_nodes(self, tmp_inp):
        from tests.mocks.engine import MockSolver

        solver = MockSolver(tmp_inp)
        session = SimSession(solver=solver, state="initialized")

        # First access creates and caches the Nodes instance
        nodes = session.nodes
        assert nodes is not None
        assert nodes.count() == 12

        # Second access returns the same object
        assert session.nodes is nodes

    def test_lazy_links(self, tmp_inp):
        from tests.mocks.engine import MockSolver

        solver = MockSolver(tmp_inp)
        session = SimSession(solver=solver, state="initialized")

        links = session.links
        assert links.count() == 11
        assert session.links is links

    def test_lazy_attribute_error(self, tmp_inp):
        from tests.mocks.engine import MockSolver

        solver = MockSolver(tmp_inp)
        session = SimSession(solver=solver)

        with pytest.raises(AttributeError, match="no attribute"):
            _ = session.nonexistent_domain

    def test_cleanup_from_running(self, tmp_inp):
        from tests.mocks.engine import MockSolver

        solver = MockSolver(tmp_inp)
        session = SimSession(solver=solver, state="running")

        # Populate the cache
        _ = session.nodes

        session.cleanup()

        assert session.state == "closed"
        assert session._cache == {}

    def test_cleanup_from_ended(self, tmp_inp):
        from tests.mocks.engine import MockSolver

        solver = MockSolver(tmp_inp)
        session = SimSession(solver=solver, state="ended")

        session.cleanup()
        assert session.state == "closed"

    def test_cleanup_from_created(self, tmp_inp):
        from tests.mocks.engine import MockSolver

        solver = MockSolver(tmp_inp)
        session = SimSession(solver=solver, state="created")

        session.cleanup()
        assert session.state == "closed"

    def test_output_reader_property(self, tmp_inp):
        from tests.mocks.engine import MockOutputReader, MockSolver

        solver = MockSolver(tmp_inp)
        session = SimSession(solver=solver)

        assert session.output_reader is None

        reader = MockOutputReader()
        session.output_reader = reader
        assert session.output_reader is reader
