"""Unit tests for SessionManager and SimSession against the real engine."""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError
from openswmm_mcp.session import SessionManager, SimSession

# ---------------------------------------------------------------------------
# TestSessionManager
# ---------------------------------------------------------------------------


class TestSessionManager:
    async def test_create_session(self, session_manager, inp_path):
        session = await session_manager.create_session("test", inp_path)
        assert session.state == "created"
        assert isinstance(session, SimSession)

    async def test_get_session(self, session_manager, inp_path):
        await session_manager.create_session("test", inp_path)
        session = await session_manager.get_session("test")
        assert session is not None
        assert isinstance(session, SimSession)

    async def test_get_nonexistent_session(self, session_manager):
        with pytest.raises(ToolError, match="SESSION_NOT_FOUND"):
            await session_manager.get_session("nonexistent")

    async def test_max_sessions(self, inp_path, tmp_path):
        sm = SessionManager(max_sessions=2, working_dir=str(tmp_path))
        try:
            await sm.create_session("a", inp_path)
            await sm.create_session("b", inp_path)
            with pytest.raises(ToolError, match="Maximum"):
                await sm.create_session("c", inp_path)
        finally:
            await sm.cleanup_all()

    async def test_duplicate_session(self, session_manager, inp_path):
        await session_manager.create_session("test", inp_path)
        with pytest.raises(ToolError, match="already exists"):
            await session_manager.create_session("test", inp_path)

    async def test_close_session(self, session_manager, inp_path):
        await session_manager.create_session("test", inp_path)
        await session_manager.close_session("test")
        with pytest.raises(ToolError):
            await session_manager.get_session("test")

    async def test_close_nonexistent_session(self, session_manager):
        with pytest.raises(ToolError, match="SESSION_NOT_FOUND"):
            await session_manager.close_session("nonexistent")

    async def test_list_sessions(self, session_manager, inp_path):
        await session_manager.create_session("a", inp_path)
        await session_manager.create_session("b", inp_path)
        sessions = await session_manager.list_sessions()
        assert len(sessions) == 2
        ids = {s["id"] for s in sessions}
        assert ids == {"a", "b"}

    async def test_list_sessions_reports_engine(self, session_manager, inp_path):
        await session_manager.create_session("ows", inp_path, engine="openswmm")
        await session_manager.create_session("leg", inp_path, engine="legacy")
        sessions = await session_manager.list_sessions()
        engines = {s["id"]: s["engine"] for s in sessions}
        assert engines == {"ows": "openswmm", "leg": "legacy"}

    async def test_list_sessions_empty(self, session_manager):
        sessions = await session_manager.list_sessions()
        assert sessions == []

    async def test_cleanup_all(self, session_manager, inp_path):
        await session_manager.create_session("a", inp_path)
        await session_manager.create_session("b", inp_path)
        await session_manager.cleanup_all()
        sessions = await session_manager.list_sessions()
        assert len(sessions) == 0

    async def test_session_working_dir_created(self, session_manager, inp_path):
        session = await session_manager.create_session("mydir", inp_path)
        assert session.working_dir.exists()
        assert session.working_dir.name == "mydir"

    async def test_default_rpt_and_out_paths(self, session_manager, inp_path):
        session = await session_manager.create_session("test", inp_path)
        # Paths are tracked on the SimSession itself, not on the underlying
        # engine handle (the new engine's Solver doesn't expose them).
        assert session.rpt_path.endswith(".rpt")
        assert session.out_path.endswith(".out")
        assert session.inp_path == inp_path


# ---------------------------------------------------------------------------
# TestSimSession
# ---------------------------------------------------------------------------


class TestSimSession:
    async def test_lazy_nodes(self, open_session, reference_model):
        """Domain accessors are lazily created and cached."""
        session = await open_session()

        nodes = session.nodes
        assert nodes is not None
        # v1: collections expose the container protocol — use len().
        assert len(nodes) == reference_model.NODE_COUNT
        # Second access returns the same object (cached)
        assert session.nodes is nodes

    async def test_lazy_links(self, open_session, reference_model):
        session = await open_session()
        links = session.links
        assert len(links) == reference_model.LINK_COUNT
        assert session.links is links

    async def test_lazy_subcatchments(self, open_session, reference_model):
        session = await open_session()
        sc = session.subcatchments
        assert len(sc) == reference_model.SUBCATCH_COUNT
        assert session.subcatchments is sc

    async def test_lazy_gages(self, open_session, reference_model):
        session = await open_session()
        gages = session.gages
        assert len(gages) == reference_model.GAGE_COUNT
        assert session.gages is gages

    async def test_lazy_attribute_error(self, open_session):
        session = await open_session()
        with pytest.raises(AttributeError, match="no attribute"):
            _ = session.nonexistent_domain

    async def test_cleanup_from_running(self, open_session):
        session = await open_session(session_id="running")
        session.backend.solver.start()
        session.state = "running"
        # Populate the backend's cache so cleanup has something to wipe
        _ = session.nodes
        session.cleanup()
        assert session.state == "closed"

    async def test_cleanup_from_initialized(self, open_session):
        session = await open_session(session_id="init")
        # Already initialized via open_session fixture
        session.cleanup()
        assert session.state == "closed"

    async def test_output_reader_property(self, open_session):
        session = await open_session()
        assert session.output_reader is None
        # Just test that the setter works; we don't open a real OutputReader
        # here since the session hasn't run yet.
        sentinel = object()
        session.output_reader = sentinel
        assert session.output_reader is sentinel

    async def test_engine_kind_default(self, session_manager, inp_path):
        session = await session_manager.create_session("default_eng", inp_path)
        assert session.engine_kind == "openswmm"

    async def test_engine_kind_legacy(self, session_manager, inp_path):
        session = await session_manager.create_session("legacy_eng", inp_path, engine="legacy")
        assert session.engine_kind == "legacy"


# ---------------------------------------------------------------------------
# TestSessionMeta — Phase 4 static-metadata cache
# ---------------------------------------------------------------------------


class TestSessionMeta:
    """Tests for the ``session.meta`` lazy static-metadata cache added in
    Phase 4 of the C_API_BINDINGS_MCP_IMPROVEMENT_PLAN.

    The cache populates from the engine's bulk getters on first access and
    is shared across all tool calls for the session — every hot-path tool
    in the MCP server is expected to read ids/counts from here rather than
    looping ``get_id`` through the C ABI N times.
    """

    async def test_meta_returns_same_instance(self, open_session):
        """Subsequent accesses must return the same SessionMeta object,
        otherwise the cache would be defeated."""
        session = await open_session()
        meta1 = session.meta
        meta2 = session.meta
        assert meta1 is meta2

    async def test_counts_match_scalar_count_attrs(self, open_session):
        """``meta.n_nodes`` etc. must agree with ``len(collection)``
        (v1 container protocol — single source of truth on the C side)."""
        session = await open_session()
        assert session.meta.n_nodes == len(session.nodes)
        assert session.meta.n_links == len(session.links)
        assert session.meta.n_subcatchments == len(session.subcatchments)

    async def test_node_ids_match_scalar_getter(self, open_session):
        """Cached id list must agree with per-index ``nodes.get_id`` —
        catches a regression where the bulk getter reads the wrong
        column or stride is off."""
        session = await open_session()
        ids = session.meta.node_ids
        assert len(ids) == len(session.nodes)
        for i, cached in enumerate(ids):
            assert cached == session.nodes.get_id(i), f"node {i}"

    async def test_link_ids_match_scalar_getter(self, open_session):
        session = await open_session()
        ids = session.meta.link_ids
        assert len(ids) == len(session.links)
        for i, cached in enumerate(ids):
            assert cached == session.links.get_id(i), f"link {i}"

    async def test_subcatch_ids_match_scalar_getter(self, open_session):
        session = await open_session()
        ids = session.meta.subcatch_ids
        assert len(ids) == len(session.subcatchments)
        for i, cached in enumerate(ids):
            assert cached == session.subcatchments.get_id(i), f"subcatch {i}"

    async def test_invalidate_meta_drops_cache(self, open_session):
        """After ``invalidate_meta()`` a subsequent access must rebuild
        the cache (verifies the cache is truly lazy and not a stale
        snapshot)."""
        session = await open_session()
        m1 = session.meta
        _ = m1.node_ids  # force population
        session.invalidate_meta()
        m2 = session.meta
        assert m1 is not m2

    async def test_repeated_id_access_is_cheap(self, open_session):
        """Once populated the id list should be returned without
        re-fetching — verifies the cache is genuinely a cache."""
        session = await open_session()
        first = session.meta.node_ids
        second = session.meta.node_ids
        # Identity, not just equality — the second call should return the
        # exact same list object.
        assert first is second
