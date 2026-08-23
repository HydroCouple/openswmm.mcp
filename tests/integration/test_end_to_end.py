"""End-to-end integration tests using the real openswmm.engine.

These tests require the compiled OpenSWMM engine to be installed and a
valid .inp file to be available.  They are marked with ``@pytest.mark.integration``
and will be skipped if the engine is not present.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

# Skip the entire module if the openswmm engine is not installed
openswmm = pytest.importorskip("openswmm.engine")


@pytest.fixture
def inp_path():
    """Locate the site_drainage_example.inp test fixture."""
    # Prefer the in-tree copy first so the test runs without an external
    # repository checkout.
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, os.pardir, "data", "site_drainage_example.inp"),
        os.path.expanduser(
            "~/Documents/Projects/cbuahin_github/openswmm.engine/"
            "python/tests/data/solver/site_drainage_example.inp"
        ),
    ]
    for p in candidates:
        if os.path.exists(p):
            return os.path.abspath(p)
    pytest.skip("site_drainage_example.inp not found")


class TestEndToEnd:
    async def test_open_run_query_close(self, inp_path, tmp_path):
        """Full lifecycle: open model, step simulation, query state, cleanup."""
        from openswmm_mcp.session import SessionManager

        sm = SessionManager(max_sessions=2, working_dir=str(tmp_path))

        # ------------------------------------------------------------------
        # Open model
        # ------------------------------------------------------------------
        session = await sm.create_session(
            "test",
            inp_path,
            rpt_path=str(tmp_path / "test.rpt"),
            out_path=str(tmp_path / "test.out"),
        )
        session.solver.open()
        session.solver.initialize()
        session.state = "initialized"

        # ------------------------------------------------------------------
        # Verify node count is > 0 (v1 container protocol)
        # ------------------------------------------------------------------
        assert len(session.nodes) > 0

        # ------------------------------------------------------------------
        # Step a few times.  v1 ``Solver.step()`` returns the elapsed
        # ``timedelta``; check ``solver.state`` (an EngineState IntEnum,
        # value 5 == RUNNING) to detect end-of-simulation.
        # ------------------------------------------------------------------
        session.solver.start()
        session.state = "running"
        RUNNING = 5
        for _ in range(10):
            session.solver.step()
            if int(session.solver.state) != RUNNING:
                break

        # ------------------------------------------------------------------
        # Query some state via v1 property access on the Node wrapper.
        # ------------------------------------------------------------------
        depth = session.nodes[0].depth
        assert isinstance(depth, float)

        # ------------------------------------------------------------------
        # Cleanup
        # ------------------------------------------------------------------
        await sm.cleanup_all()

    async def test_session_manager_list_and_close(self, inp_path, tmp_path):
        """SessionManager can list and close sessions created with real models."""
        from openswmm_mcp.session import SessionManager

        sm = SessionManager(max_sessions=3, working_dir=str(tmp_path))

        await sm.create_session(
            "s1",
            inp_path,
            rpt_path=str(tmp_path / "s1.rpt"),
            out_path=str(tmp_path / "s1.out"),
        )
        await sm.create_session(
            "s2",
            inp_path,
            rpt_path=str(tmp_path / "s2.rpt"),
            out_path=str(tmp_path / "s2.out"),
        )

        sessions = await sm.list_sessions()
        assert len(sessions) == 2
        ids = {s["id"] for s in sessions}
        assert ids == {"s1", "s2"}

        await sm.close_session("s1")
        sessions = await sm.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["id"] == "s2"

        await sm.cleanup_all()

    async def test_lazy_domain_accessors(self, inp_path, tmp_path):
        """Domain objects (nodes, links, etc.) are lazily created from the solver."""
        from openswmm_mcp.session import SessionManager

        sm = SessionManager(max_sessions=2, working_dir=str(tmp_path))
        session = await sm.create_session(
            "lazy_test",
            inp_path,
            rpt_path=str(tmp_path / "lazy.rpt"),
            out_path=str(tmp_path / "lazy.out"),
        )
        session.solver.open()
        session.solver.initialize()
        session.state = "initialized"

        # Access domain objects -- they should be created on demand
        nodes = session.nodes
        links = session.links
        subcatchments = session.subcatchments

        # The same object should be returned on subsequent access
        assert session.nodes is nodes
        assert session.links is links
        assert session.subcatchments is subcatchments

        await sm.cleanup_all()
