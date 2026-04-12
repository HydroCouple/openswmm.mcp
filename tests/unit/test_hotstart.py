"""Tests for openswmm_mcp.tools.hotstart -- save, load, and clone operations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import HotStartResult
from openswmm_mcp.tools.hotstart import clone_session, load_hotstart, save_hotstart

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}


def _make_session(state: str = "running", working_dir: str = "/tmp/test_session"):
    """Return a MagicMock that quacks like a SimSession."""
    session = MagicMock()
    session.state = state
    session.working_dir = Path(working_dir)

    # hotstart facade
    session.hotstart.save = MagicMock()
    hotstart_handle = MagicMock()
    hotstart_handle.apply = MagicMock()
    session.hotstart.open = MagicMock(return_value=hotstart_handle)

    # solver facade (needed by clone_session)
    session.solver.inp_path = "/models/test.inp"
    session.solver.open = MagicMock()
    session.solver.init = MagicMock()

    return session


def _make_session_manager(sessions: dict | None = None, allow_create: bool = True):
    """Return an async-compatible mock SessionManager."""
    sm = MagicMock()
    _sessions = sessions or {}

    async def _get_session(sid):
        if sid not in _sessions:
            raise ToolError(f"No session with id '{sid}'.")
        return _sessions[sid]

    sm.get_session = AsyncMock(side_effect=_get_session)

    if allow_create:
        created = _make_session(state="created")

        async def _create_session(sid, inp_path, **kwargs):
            _sessions[sid] = created
            return created

        sm.create_session = AsyncMock(side_effect=_create_session)

    return sm


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSaveHotstart:
    async def test_save_hotstart(self, tmp_path):
        """save_hotstart writes to the given path and returns HotStartResult."""
        session = _make_session(state="running", working_dir=str(tmp_path))
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        hsf_path = str(tmp_path / "checkpoint.hsf")
        result = await save_hotstart(ctx, session_id="default", path=hsf_path)

        assert isinstance(result, HotStartResult)
        assert result.status == "saved"
        assert result.path == hsf_path
        session.hotstart.save.assert_called_once()

    async def test_save_hotstart_auto_path(self, tmp_path):
        """When no path is given, a default is generated from the working directory."""
        session = _make_session(state="ended", working_dir=str(tmp_path))
        sm = _make_session_manager({"mysession": session})
        ctx = MockContext(sm)

        result = await save_hotstart(ctx, session_id="mysession", path="")

        assert isinstance(result, HotStartResult)
        assert "mysession" in result.path
        assert result.path.endswith(".hsf")

    async def test_save_hotstart_requires_running_or_ended(self):
        """save_hotstart rejects sessions not in 'running' or 'ended' state."""
        for bad_state in ("created", "opened", "initialized", "closed"):
            session = _make_session(state=bad_state)
            sm = _make_session_manager({f"s_{bad_state}": session})
            ctx = MockContext(sm)

            with pytest.raises(ToolError, match="Cannot save hot-start"):
                await save_hotstart(ctx, session_id=f"s_{bad_state}")


class TestLoadHotstart:
    async def test_load_hotstart(self, tmp_path):
        """load_hotstart reads a file and applies its state to the session."""
        hsf = tmp_path / "state.hsf"
        hsf.write_bytes(b"fake hotstart data")

        session = _make_session(state="initialized")
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await load_hotstart(ctx, session_id="default", path=str(hsf))

        assert isinstance(result, HotStartResult)
        assert result.status == "loaded"
        assert result.path == str(hsf)
        session.hotstart.open.assert_called_once_with(str(hsf))

    async def test_load_hotstart_missing_path(self):
        """load_hotstart raises ToolError when no path is provided."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="path must be provided"):
            await load_hotstart(ctx, session_id="default", path="")

    async def test_load_hotstart_file_not_found(self, tmp_path):
        """load_hotstart raises ToolError when the file does not exist."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="not found"):
            await load_hotstart(ctx, session_id="default", path=str(tmp_path / "nonexistent.hsf"))


class TestCloneSession:
    async def test_clone_session(self, tmp_path):
        """clone_session creates a new session from the source's hot-start state."""
        source = _make_session(state="running")
        sm = _make_session_manager({"baseline": source}, allow_create=True)
        ctx = MockContext(sm)

        result = await clone_session(ctx, source_id="baseline", target_id="whatif_1")

        assert result["status"] == "cloned"
        assert result["source_id"] == "baseline"
        assert result["target_id"] == "whatif_1"

        # Verify the source's hotstart.save was called
        source.hotstart.save.assert_called_once()
        # Verify a new session was created
        sm.create_session.assert_awaited_once()

    async def test_clone_session_requires_source_id(self):
        """clone_session rejects empty source_id."""
        sm = _make_session_manager({})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="source_id is required"):
            await clone_session(ctx, source_id="", target_id="target")

    async def test_clone_session_requires_target_id(self):
        """clone_session rejects empty target_id."""
        sm = _make_session_manager({})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="target_id is required"):
            await clone_session(ctx, source_id="source", target_id="")

    async def test_clone_session_source_must_be_running_or_ended(self):
        """clone_session rejects sessions not in 'running' or 'ended' state."""
        session = _make_session(state="initialized")
        sm = _make_session_manager({"src": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="Cannot clone"):
            await clone_session(ctx, source_id="src", target_id="dst")
