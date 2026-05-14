"""Tests for openswmm_mcp.tools.hotstart -- save, load, and clone operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import HotStartResult
from openswmm_mcp.tools.hotstart import clone_session, load_hotstart, save_hotstart


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _open_and_run(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)
    await run_simulation(fake_ctx, session_id=session_id)


async def _open_and_step(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)
    await step_simulation(fake_ctx, session_id=session_id, num_steps=1)


async def _open(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)


# ---------------------------------------------------------------------------
# save_hotstart
# ---------------------------------------------------------------------------


class TestSaveHotstart:
    async def test_save_hotstart_after_run(self, fake_ctx, inp_path, tmp_path):
        """save_hotstart writes a .hsf file when session is in 'ended' state."""
        await _open_and_run(fake_ctx, inp_path, "hs_save")
        hsf_path = str(tmp_path / "checkpoint.hsf")

        result = await save_hotstart(fake_ctx, session_id="hs_save", path=hsf_path)

        assert isinstance(result, HotStartResult)
        assert result.status == "saved"
        assert result.path == hsf_path
        assert Path(hsf_path).exists()

    async def test_save_hotstart_during_run(self, fake_ctx, inp_path, tmp_path):
        """save_hotstart works while session is in 'running' state (mid-step)."""
        await _open_and_step(fake_ctx, inp_path, "hs_mid")
        hsf_path = str(tmp_path / "mid.hsf")

        result = await save_hotstart(fake_ctx, session_id="hs_mid", path=hsf_path)

        assert isinstance(result, HotStartResult)
        assert result.status == "saved"

    async def test_save_hotstart_auto_path(self, fake_ctx, inp_path):
        """When no path is given, a path is generated from the session's working dir."""
        await _open_and_run(fake_ctx, inp_path, "hs_auto")

        result = await save_hotstart(fake_ctx, session_id="hs_auto", path="")

        assert isinstance(result, HotStartResult)
        assert result.status == "saved"
        assert "hs_auto" in result.path
        assert result.path.endswith(".hsf")

    async def test_save_hotstart_requires_running_or_ended(self, fake_ctx, inp_path):
        """save_hotstart rejects sessions not in 'running' or 'ended' state."""
        await _open(fake_ctx, inp_path, "hs_bad")
        # Session is now "initialized" — invalid for save

        with pytest.raises(ToolError, match="Cannot save hot-start"):
            await save_hotstart(fake_ctx, session_id="hs_bad")


# ---------------------------------------------------------------------------
# load_hotstart
# ---------------------------------------------------------------------------


class TestLoadHotstart:
    async def test_load_hotstart(self, fake_ctx, inp_path, tmp_path):
        """load_hotstart reads a saved .hsf and applies its state."""
        # Save a hotstart from a completed run
        await _open_and_run(fake_ctx, inp_path, "hs_src")
        hsf_path = str(tmp_path / "state.hsf")
        await save_hotstart(fake_ctx, session_id="hs_src", path=hsf_path)

        # Load it into a freshly initialized session
        await _open(fake_ctx, inp_path, "hs_dst")
        result = await load_hotstart(fake_ctx, session_id="hs_dst", path=hsf_path)

        assert isinstance(result, HotStartResult)
        assert result.status == "loaded"
        assert result.path == hsf_path

    async def test_load_hotstart_missing_path(self, fake_ctx, inp_path):
        """load_hotstart raises ToolError when no path is provided."""
        await _open(fake_ctx, inp_path, "hs_nopath")

        with pytest.raises(ToolError, match="path must be provided"):
            await load_hotstart(fake_ctx, session_id="hs_nopath", path="")

    async def test_load_hotstart_file_not_found(self, fake_ctx, inp_path, tmp_path):
        """load_hotstart raises ToolError when the file does not exist."""
        await _open(fake_ctx, inp_path, "hs_nofile")

        with pytest.raises(ToolError, match="not found"):
            await load_hotstart(
                fake_ctx,
                session_id="hs_nofile",
                path=str(tmp_path / "nonexistent.hsf"),
            )


# ---------------------------------------------------------------------------
# clone_session
# ---------------------------------------------------------------------------


class TestCloneSession:
    async def test_clone_session(self, fake_ctx, inp_path):
        """clone_session creates a new session from the source's hot-start state."""
        await _open_and_run(fake_ctx, inp_path, "hs_baseline")

        result = await clone_session(fake_ctx, source_id="hs_baseline", target_id="hs_clone")

        assert result["status"] == "cloned"
        assert result["source_id"] == "hs_baseline"
        assert result["target_id"] == "hs_clone"

    async def test_clone_session_requires_source_id(self, fake_ctx):
        """clone_session rejects empty source_id."""
        with pytest.raises(ToolError, match="source_id is required"):
            await clone_session(fake_ctx, source_id="", target_id="target")

    async def test_clone_session_requires_target_id(self, fake_ctx):
        """clone_session rejects empty target_id."""
        with pytest.raises(ToolError, match="target_id is required"):
            await clone_session(fake_ctx, source_id="source", target_id="")

    async def test_clone_session_source_must_be_running_or_ended(
        self, fake_ctx, inp_path
    ):
        """clone_session rejects source sessions not in 'running' or 'ended' state."""
        await _open(fake_ctx, inp_path, "hs_uninit")
        # Session is "initialized" — not valid for cloning

        with pytest.raises(ToolError, match="Cannot clone"):
            await clone_session(fake_ctx, source_id="hs_uninit", target_id="hs_bad_clone")
