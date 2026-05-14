"""Tests for the engine backend abstraction and per-session engine selection."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openswmm_mcp.backends import make_backend
from openswmm_mcp.dependencies import require_new_engine
from openswmm_mcp.errors import ToolError


class TestEngineSelection:
    async def test_unknown_engine_raises(self, session_manager, tmp_inp):
        with pytest.raises(ToolError, match="Unknown engine"):
            await session_manager.create_session(
                "bad", tmp_inp, engine="not-a-real-engine"
            )

    async def test_default_engine_is_openswmm(self, session_manager, tmp_inp):
        session = await session_manager.create_session("default_engine", tmp_inp)
        assert session.engine_kind == "openswmm"

    async def test_explicit_openswmm_engine(self, session_manager, tmp_inp):
        session = await session_manager.create_session(
            "explicit", tmp_inp, engine="openswmm"
        )
        assert session.engine_kind == "openswmm"

    def test_make_backend_unknown_raises_value_error(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown engine"):
            make_backend("nonsense", str(tmp_path / "x.inp"), "", "")


class TestRequireNewEngineGuard:
    def test_passes_for_openswmm_session(self):
        session = SimpleNamespace(engine_kind="openswmm")
        require_new_engine(session, "Some feature")  # should not raise

    def test_passes_when_engine_kind_missing(self):
        # Sessions without an engine_kind attribute are treated as openswmm
        # by default (e.g. building sessions before finalize()).
        session = SimpleNamespace()
        require_new_engine(session, "Some feature")

    def test_raises_for_legacy_session(self):
        session = SimpleNamespace(engine_kind="legacy")
        with pytest.raises(ToolError) as exc_info:
            require_new_engine(session, "Spatial coordinates")
        msg = str(exc_info.value)
        assert "NOT_SUPPORTED" in msg
        assert "Spatial coordinates" in msg
        assert "engine='legacy'" in msg


class TestSessionListingIncludesEngine:
    async def test_list_sessions_reports_engine(self, session_manager, tmp_inp):
        await session_manager.create_session("a", tmp_inp)
        listing = await session_manager.list_sessions()
        assert len(listing) == 1
        assert listing[0]["engine"] == "openswmm"
