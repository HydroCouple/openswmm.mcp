"""Unit tests for lifecycle tool functions against the real engine.

The lifecycle tools are parametrized over both backends (``openswmm`` and
``legacy``) wherever the underlying behaviour is supposed to be uniform —
mass-balance shape, step-loop semantics, and result-model fields.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import ModelSummary, SimulationResult, StepResult

# ---------------------------------------------------------------------------
# TestOpenModel — both engines
# ---------------------------------------------------------------------------


class TestOpenModel:
    async def test_open_model_default_engine(self, fake_ctx, inp_path):
        """Default engine is ``openswmm``."""
        from openswmm_mcp.tools.lifecycle import open_model

        result = await open_model(fake_ctx, inp_path=inp_path, session_id="test")

        assert isinstance(result, ModelSummary)
        assert result.session_id == "test"
        assert result.state == "initialized"
        assert result.engine == "openswmm"

    async def test_open_model_each_engine(self, fake_ctx, inp_path, engine, reference_model):
        from openswmm_mcp.tools.lifecycle import open_model

        result = await open_model(fake_ctx, inp_path=inp_path, session_id="t", engine=engine)

        assert result.engine == engine
        assert result.state == "initialized"
        assert result.node_count == reference_model.NODE_COUNT
        assert result.link_count == reference_model.LINK_COUNT
        assert result.subcatchment_count == reference_model.SUBCATCH_COUNT
        assert result.gage_count == reference_model.GAGE_COUNT
        assert result.pollutant_count == reference_model.POLLUTANT_COUNT

    async def test_open_model_flow_units(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.lifecycle import open_model

        result = await open_model(fake_ctx, inp_path=inp_path, session_id="opts")

        assert result.flow_units == "CFS"
        # ROUTING_MODEL is exposed by the new engine but not by the legacy
        # toolkit; only assert it for openswmm.
        assert result.route_model == "DYNWAVE"

    async def test_open_model_legacy_route_model_unknown(self, fake_ctx, inp_path):
        """Legacy doesn't expose ROUTING_MODEL via the toolkit; falls back to UNKNOWN."""
        from openswmm_mcp.tools.lifecycle import open_model

        result = await open_model(
            fake_ctx, inp_path=inp_path, session_id="leg_opts", engine="legacy"
        )

        assert result.flow_units == "CFS"  # legacy can read FLOW_UNITS
        assert result.route_model == "UNKNOWN"

    async def test_open_model_timing(self, fake_ctx, inp_path, engine, reference_model):
        from openswmm_mcp.tools.lifecycle import open_model

        result = await open_model(fake_ctx, inp_path=inp_path, session_id="timing", engine=engine)

        # Both backends present elapsed-day floats; the actual epoch differs
        # (openswmm uses Julian, legacy uses simulation-start = 0.0), so we
        # assert on the *duration*, not absolute values.
        duration = result.end_time - result.start_time
        assert duration == pytest.approx(reference_model.EXPECTED_DURATION_DAYS, rel=1e-3)
        assert result.routing_step == pytest.approx(
            reference_model.EXPECTED_ROUTING_STEP_SECS, rel=1e-3
        )

    async def test_open_model_duplicate(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.lifecycle import open_model

        await open_model(fake_ctx, inp_path=inp_path, session_id="dup")

        with pytest.raises(ToolError, match="already exists"):
            await open_model(fake_ctx, inp_path=inp_path, session_id="dup")

    async def test_open_model_unknown_engine(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.lifecycle import open_model

        with pytest.raises(ToolError, match="Unknown engine"):
            await open_model(fake_ctx, inp_path=inp_path, session_id="bad", engine="unknown")

    async def test_open_model_lenient_stays_opened(self, fake_ctx, inp_path):
        """lenient_open records validation issues and leaves the session in the
        editable 'opened' state (it does not auto-initialise)."""
        from openswmm_mcp.tools.lifecycle import open_model

        result = await open_model(
            fake_ctx, inp_path=inp_path, session_id="lenient", lenient_open=True
        )
        assert isinstance(result, ModelSummary)
        assert result.state == "opened"
        assert result.engine == "openswmm"


# ---------------------------------------------------------------------------
# get_open_diagnostics — read open_errors / open_warnings after lenient open
# ---------------------------------------------------------------------------


class TestOpenDiagnostics:
    async def test_clean_model_reports_no_issues(self, fake_ctx, inp_path):
        """A valid model records no errors; counts agree with the lists.

        Warnings are *not* asserted empty: a model that opens cleanly can still
        raise advisory warnings (e.g. WARNING 02, node max-depth increased).
        """
        from openswmm_mcp.tools.lifecycle import get_open_diagnostics, open_model

        await open_model(fake_ctx, inp_path=inp_path, session_id="diag", lenient_open=True)
        out = await get_open_diagnostics(fake_ctx, session_id="diag")
        assert out["session_id"] == "diag"
        assert isinstance(out["errors"], list)
        assert isinstance(out["warnings"], list)
        assert out["error_count"] == len(out["errors"])
        assert out["warning_count"] == len(out["warnings"])

    async def test_diagnostics_readable_after_strict_open(self, fake_ctx, inp_path):
        """The accumulators are readable after a strict open too.

        A strict open only succeeds when nothing was recorded as an error, so
        ``errors`` is empty; warnings may still be present and are returned as
        message strings.
        """
        from openswmm_mcp.tools.lifecycle import get_open_diagnostics, open_model

        await open_model(fake_ctx, inp_path=inp_path, session_id="diag_strict")
        out = await get_open_diagnostics(fake_ctx, session_id="diag_strict")
        assert out["errors"] == []
        assert isinstance(out["warnings"], list)
        assert all(isinstance(w, str) for w in out["warnings"])


# ---------------------------------------------------------------------------
# TestRunSimulation — both engines
# ---------------------------------------------------------------------------


class TestRunSimulation:
    async def test_run_simulation_completes(self, fake_ctx, inp_path, engine):
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="run", engine=engine)

        result = await run_simulation(fake_ctx, session_id="run")

        assert isinstance(result, SimulationResult)
        assert result.session_id == "run"
        assert result.steps_completed > 0
        # Continuity errors are returned as fractions (new-engine convention).
        # The site-drainage example produces small but nonzero errors;
        # require they be in a sane range rather than a specific value.
        assert -0.5 < result.runoff_continuity_error < 0.5
        assert -0.5 < result.routing_continuity_error < 0.5

    async def test_run_simulation_reports_progress(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="prog")
        await run_simulation(fake_ctx, session_id="prog")

        # Must have called report_progress at least once + the 100% call
        assert len(fake_ctx.progress_calls) > 0
        assert fake_ctx.progress_calls[-1] == (100, 100)

    async def test_run_simulation_wall_time(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="wall")
        result = await run_simulation(fake_ctx, session_id="wall")

        assert result.elapsed_wall_time >= 0.0

    async def test_run_simulation_leaves_session_ended(
        self, fake_ctx, inp_path, session_manager, engine
    ):
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="ended", engine=engine)
        await run_simulation(fake_ctx, session_id="ended")

        session = await session_manager.get_session("ended")
        assert session.state == "ended"

    async def test_run_simulation_carries_engine_kind(
        self,
        fake_ctx,
        inp_path,
        engine,
    ):
        """Phase 4d: SimulationResult must carry the backend
        discriminator so consumers can distinguish "feature missing
        on this backend" from "no value to report"."""
        from openswmm_mcp.tools.lifecycle import open_model, run_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="run_ek", engine=engine)
        result = await run_simulation(fake_ctx, session_id="run_ek")
        assert result.engine_kind == engine
        # The single-pollutant site_drainage fixture cannot trigger the
        # "legacy + multi-pollutant" branch, so unsupported_fields is None
        # for both backends here. We assert the field is at least defined
        # (Pydantic default-ok contract).
        assert result.unsupported_fields is None or isinstance(result.unsupported_fields, list)


# ---------------------------------------------------------------------------
# TestStepSimulation — both engines
# ---------------------------------------------------------------------------


class TestStepSimulation:
    async def test_step_simulation_single(self, fake_ctx, inp_path, engine):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="step1", engine=engine)

        result = await step_simulation(fake_ctx, session_id="step1", num_steps=1)

        assert isinstance(result, StepResult)
        assert result.steps_taken == 1
        assert result.completed is False

    async def test_step_simulation_multiple(self, fake_ctx, inp_path, engine):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="step5", engine=engine)

        result = await step_simulation(fake_ctx, session_id="step5", num_steps=5)

        assert result.steps_taken == 5
        assert result.completed is False

    async def test_step_simulation_to_completion(self, fake_ctx, inp_path, engine):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="stepall", engine=engine)

        # site_drainage_model.inp runs 30 h at 5 s routing step = ~21 600 steps;
        # over-step generously to guarantee completion within one call.
        result = await step_simulation(fake_ctx, session_id="stepall", num_steps=100_000)

        assert result.completed is True
        assert result.steps_taken > 0

    async def test_step_auto_starts(self, fake_ctx, inp_path, session_manager, engine):
        from openswmm_mcp.tools.lifecycle import open_model, step_simulation

        await open_model(fake_ctx, inp_path=inp_path, session_id="autostart", engine=engine)

        session = await session_manager.get_session("autostart")
        assert session.state == "initialized"

        await step_simulation(fake_ctx, session_id="autostart", num_steps=1)

        session = await session_manager.get_session("autostart")
        assert session.state == "running"


# ---------------------------------------------------------------------------
# TestCloseModel
# ---------------------------------------------------------------------------


class TestCloseModel:
    async def test_close_model(self, fake_ctx, inp_path, session_manager, engine):
        from openswmm_mcp.tools.lifecycle import close_model, open_model

        await open_model(fake_ctx, inp_path=inp_path, session_id="close", engine=engine)

        result = await close_model(fake_ctx, session_id="close")

        assert result["status"] == "closed"
        assert result["session_id"] == "close"

        with pytest.raises(ToolError):
            await session_manager.get_session("close")

    async def test_close_nonexistent(self, fake_ctx):
        from openswmm_mcp.tools.lifecycle import close_model

        with pytest.raises(ToolError, match="SESSION_NOT_FOUND"):
            await close_model(fake_ctx, session_id="nope")


# ---------------------------------------------------------------------------
# TestListSessions
# ---------------------------------------------------------------------------


class TestListSessions:
    async def test_list_sessions_empty(self, fake_ctx):
        from openswmm_mcp.tools.lifecycle import list_sessions

        result = await list_sessions(fake_ctx)
        assert result == []

    async def test_list_sessions_after_open(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.lifecycle import list_sessions, open_model

        await open_model(fake_ctx, inp_path=inp_path, session_id="listed")

        result = await list_sessions(fake_ctx)

        assert len(result) == 1
        assert result[0]["id"] == "listed"
        assert result[0]["state"] == "initialized"
        assert result[0]["engine"] == "openswmm"

    async def test_list_sessions_mixed_engines(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.lifecycle import list_sessions, open_model

        await open_model(fake_ctx, inp_path=inp_path, session_id="ows", engine="openswmm")
        await open_model(fake_ctx, inp_path=inp_path, session_id="leg", engine="legacy")

        result = await list_sessions(fake_ctx)
        engines = {s["id"]: s["engine"] for s in result}
        assert engines == {"ows": "openswmm", "leg": "legacy"}


# ---------------------------------------------------------------------------
# Runoff interface file (Phase 1b — task #28)
# ---------------------------------------------------------------------------


class TestRunoffInterfaceTools:
    """End-to-end contract tests for ``save_runoff_interface`` and
    ``load_runoff_interface``.  These wrap the Phase 1b Solver methods
    and complete the engine→bindings→MCP chain for the runoff interface.
    """

    async def test_save_runoff_interface_rejects_empty_path(
        self,
        fake_ctx,
        inp_path,
    ):
        from openswmm_mcp.tools.lifecycle import open_model, save_runoff_interface

        await open_model(fake_ctx, inp_path=inp_path, session_id="rfi_empty")
        with pytest.raises(ToolError, match="VALIDATION_ERROR"):
            await save_runoff_interface(fake_ctx, session_id="rfi_empty", path="")

    async def test_save_runoff_interface_rejects_legacy_backend(
        self,
        fake_ctx,
        inp_path,
        tmp_path,
    ):
        """Runoff iface is a new-engine feature; legacy must be rejected
        with a clear ToolError rather than silently failing."""
        from openswmm_mcp.tools.lifecycle import open_model, save_runoff_interface

        await open_model(fake_ctx, inp_path=inp_path, session_id="rfi_leg", engine="legacy")
        with pytest.raises(ToolError):
            await save_runoff_interface(
                fake_ctx, session_id="rfi_leg", path=str(tmp_path / "leg.rfi")
            )

    async def test_save_runoff_interface_writes_file_after_run(
        self,
        fake_ctx,
        inp_path,
        tmp_path,
    ):
        """Headline path: open in SAVE mode, run the simulation, the
        engine auto-emits records into the file."""
        from openswmm_mcp.tools.lifecycle import (
            open_model,
            run_simulation,
            save_runoff_interface,
        )

        path = str(tmp_path / "phase1b.rfi")
        await open_model(fake_ctx, inp_path=inp_path, session_id="rfi_save")
        result = await save_runoff_interface(fake_ctx, session_id="rfi_save", path=path)
        assert result["status"] == "ok"
        assert result["mode"] == "save"
        assert result["path"] == path

        await run_simulation(fake_ctx, session_id="rfi_save")

        import os

        # File header is 28 bytes; a successful run should produce many
        # additional substep records.
        assert os.path.exists(path)
        assert os.path.getsize(path) > 28

    async def test_load_runoff_interface_after_save_round_trip(
        self,
        fake_ctx,
        inp_path,
        tmp_path,
    ):
        """Round trip: produce a file via SAVE mode then open it via
        ``load_runoff_interface`` in a fresh session."""
        from openswmm_mcp.tools.lifecycle import (
            close_model,
            load_runoff_interface,
            open_model,
            run_simulation,
            save_runoff_interface,
        )

        path = str(tmp_path / "phase1b_rt.rfi")

        # SAVE pass.
        await open_model(fake_ctx, inp_path=inp_path, session_id="rfi_rt_save")
        await save_runoff_interface(fake_ctx, session_id="rfi_rt_save", path=path)
        await run_simulation(fake_ctx, session_id="rfi_rt_save")
        await close_model(fake_ctx, session_id="rfi_rt_save")

        # USE pass — reopen the file on a fresh session.
        await open_model(fake_ctx, inp_path=inp_path, session_id="rfi_rt_use")
        result = await load_runoff_interface(fake_ctx, session_id="rfi_rt_use", path=path)
        assert result["status"] == "ok"
        assert result["mode"] == "use"
        # Audit the documented caveat is surfaced to the caller.
        assert "warning" in result
        assert "USE mode" in result["warning"]

    async def test_load_runoff_interface_rejects_missing_file(
        self,
        fake_ctx,
        inp_path,
    ):
        from openswmm_mcp.tools.lifecycle import (
            load_runoff_interface,
            open_model,
        )

        await open_model(fake_ctx, inp_path=inp_path, session_id="rfi_miss")
        with pytest.raises(ToolError):
            await load_runoff_interface(
                fake_ctx, session_id="rfi_miss", path="/nonexistent/path/to/file.rfi"
            )
