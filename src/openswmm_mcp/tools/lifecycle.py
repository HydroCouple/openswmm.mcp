"""Lifecycle tools for the OpenSWMM MCP server.

Provides tools for opening, running, stepping, and closing SWMM models.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import ModelSummary, SimulationResult, StepResult


# OADate <-> datetime conversion is needed by the events tools, but the
# import is deferred until first use so this module continues to import
# cleanly when the engine wheel is not built.
def _oadate_helpers() -> tuple:
    from openswmm.engine import datetime_to_oadate, oadate_to_datetime
    return oadate_to_datetime, datetime_to_oadate

logger = logging.getLogger(__name__)

lifecycle_mcp = FastMCP("lifecycle")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _node_type_name(code: int) -> str:
    """Map a NodeType int to a human-readable string."""
    _NAMES = {0: "JUNCTION", 1: "OUTFALL", 2: "STORAGE", 3: "DIVIDER"}
    return _NAMES.get(code, f"UNKNOWN({code})")


def _link_type_name(code: int) -> str:
    """Map a LinkType int to a human-readable string."""
    _NAMES = {0: "CONDUIT", 1: "PUMP", 2: "ORIFICE", 3: "WEIR", 4: "OUTLET"}
    return _NAMES.get(code, f"UNKNOWN({code})")


def _flow_units_name(code: int) -> str:
    """Map a FlowUnits int to a human-readable string."""
    _NAMES = {0: "CFS", 1: "GPM", 2: "MGD", 3: "CMS", 4: "LPS", 5: "MLD"}
    return _NAMES.get(code, f"UNKNOWN({code})")


def _route_model_name(code: int) -> str:
    """Map a RouteModel int to a human-readable string."""
    _NAMES = {0: "STEADY", 1: "KINWAVE", 2: "DYNWAVE"}
    return _NAMES.get(code, f"UNKNOWN({code})")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@lifecycle_mcp.tool
async def open_model(
    ctx: Context,
    inp_path: str,
    session_id: str = "default",
    rpt_path: str | None = None,
    out_path: str | None = None,
    engine: str = "openswmm",
) -> ModelSummary:
    """Open a SWMM model file and initialise the engine.

    Creates a new simulation session, parses the .inp file, and prepares the
    engine for simulation. Returns a summary of the loaded model.

    Parameters
    ----------
    inp_path:
        Path to the SWMM ``.inp`` input file.
    session_id:
        Identifier for the new session.  Defaults to ``"default"``.
    rpt_path / out_path:
        Optional report and binary output file paths.  When omitted they
        default to ``<inp_stem>.rpt`` / ``<inp_stem>.out``.
    engine:
        Which engine to use.  ``"openswmm"`` (default) is the modern engine
        with full feature support.  ``"legacy"`` selects the EPA SWMM 5.x
        bindings shipped alongside it; only basic query / forcing /
        mass-balance / hot-start tools are supported on legacy sessions —
        advanced tools (model building, in-place editing, controls,
        infrastructure, quality, spatial, geopackage, output reader)
        return a ``NOT_SUPPORTED`` error.
    """
    sm = get_session_manager(ctx)

    session = await sm.create_session(
        session_id=session_id,
        inp_path=inp_path,
        rpt_path=rpt_path,
        out_path=out_path,
        engine=engine,
    )

    try:
        await asyncio.to_thread(session.solver.open)
        session.state = "opened"

        await asyncio.to_thread(session.solver.initialize)
        session.state = "initialized"
    except Exception as exc:
        # Clean up the partially-opened session so it doesn't leak
        try:
            session.cleanup()
        except Exception:
            logger.debug("cleanup after open failure raised", exc_info=True)
        # Remove the session from the registry
        try:
            await sm.close_session(session_id)
        except Exception:
            pass
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Failed to open model: {exc}") from exc

    # Gather everything in one worker hop: v1 collections support len() and
    # solver exposes datetime / timedelta properties + the options mapping.
    nodes = session.nodes
    links = session.links
    subcatchments = session.subcatchments
    gages = session.gages
    pollutants = session.pollutants
    solver = session.solver

    def _gather_summary() -> dict:
        # Counts via v1 container protocol.
        node_count = len(nodes)
        link_count = len(links)
        subcatch_count = len(subcatchments)
        gage_count = len(gages)
        pollutant_count = len(pollutants)

        # Timing via v1 datetime / timedelta properties.  JSON wire format
        # wants floats (days since start, seconds), so convert here.
        start_dt = solver.start_datetime
        end_dt = solver.end_datetime
        start_t = 0.0
        end_t = (end_dt - start_dt).total_seconds() / 86400.0
        try:
            r_step = solver.routing_step.total_seconds()
        except AttributeError:
            r_step = float(solver.routing_step)

        # Option lookups via the v1 mapping.
        options = solver.options

        def _opt(key: str) -> str | None:
            try:
                return options[key]
            except (KeyError, Exception):
                return None

        # New engine returns numeric code strings ("0");
        # legacy adapter returns names ("CFS").
        fu_raw = _opt("FLOW_UNITS")
        if fu_raw is None:
            fu = "UNKNOWN"
        else:
            try:
                fu = _flow_units_name(int(fu_raw))
            except (ValueError, TypeError):
                fu = str(fu_raw).upper() or "UNKNOWN"

        rm_raw = _opt("FLOW_ROUTING")
        if rm_raw is None:
            rm = "UNKNOWN"
        else:
            try:
                rm = _route_model_name(int(rm_raw))
            except (ValueError, TypeError):
                rm = str(rm_raw).upper() or "UNKNOWN"

        return {
            "node_count": node_count,
            "link_count": link_count,
            "subcatch_count": subcatch_count,
            "gage_count": gage_count,
            "pollutant_count": pollutant_count,
            "start_time": start_t,
            "end_time": end_t,
            "routing_step": r_step,
            "flow_units": fu,
            "route_model": rm,
        }

    s = await asyncio.to_thread(_gather_summary)

    return ModelSummary(
        session_id=session_id,
        state=session.state,
        engine=session.engine_kind,
        node_count=s["node_count"],
        link_count=s["link_count"],
        subcatchment_count=s["subcatch_count"],
        gage_count=s["gage_count"],
        pollutant_count=s["pollutant_count"],
        flow_units=s["flow_units"],
        route_model=s["route_model"],
        start_time=s["start_time"],
        end_time=s["end_time"],
        routing_step=s["routing_step"],
    )


@lifecycle_mcp.tool(task=True)
async def run_simulation(
    ctx: Context,
    session_id: str = "default",
) -> SimulationResult:
    """Run the full simulation to completion.

    Starts the solver (if not already started), steps through every timestep,
    and reports progress as a percentage.  Returns continuity errors and timing.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running")

    solver = session.solver

    # Start the solver if we have not yet
    if session.state == "initialized":
        await asyncio.to_thread(solver.start)
        session.state = "running"

    # Total simulation duration (seconds) for progress reporting.  v1
    # exposes datetimes; on the legacy adapter the shim returns the same.
    start_dt = await asyncio.to_thread(lambda: solver.start_datetime)
    end_dt = await asyncio.to_thread(lambda: solver.end_datetime)
    total_seconds = max((end_dt - start_dt).total_seconds(), 1.0)

    # EngineState.RUNNING is 5 on the new engine; the legacy adapter
    # returns a different state enum but reports != 5 as "not running",
    # which is sufficient for the loop-termination condition.
    RUNNING = 5

    steps = 0
    wall_start = time.monotonic()

    # Detect completion by polling solver.state. v1's step() returns a
    # timedelta (elapsed since simulation start); the legacy adapter
    # returns a bool.  We ignore the return value and rely on .state.
    try:
        solver_state = await asyncio.to_thread(lambda: int(solver.state))
        while solver_state == RUNNING:
            await asyncio.to_thread(solver.step)
            steps += 1
            solver_state = await asyncio.to_thread(lambda: int(solver.state))

            # Report progress based on elapsed simulation time.
            if steps % 100 == 0:
                try:
                    cur_dt = await asyncio.to_thread(lambda: solver.current_datetime)
                    elapsed_sim = (cur_dt - start_dt).total_seconds()
                    pct = min(int((elapsed_sim / total_seconds) * 100), 99)
                    await ctx.report_progress(pct, 100)
                except Exception:
                    pass

        await ctx.report_progress(100, 100)

        await asyncio.to_thread(solver.end)
        session.state = "ended"
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Simulation failed at step {steps}: {exc}"
        ) from exc

    wall_elapsed = time.monotonic() - wall_start

    # Retrieve continuity errors via the v1 property surface.
    mb = session.mass_balance

    runoff_err = await asyncio.to_thread(lambda: mb.runoff_continuity_error)
    routing_err = await asyncio.to_thread(lambda: mb.routing_continuity_error)

    quality_err: float | None = None
    pollutants = session.pollutants
    poll_count = await asyncio.to_thread(lambda: len(pollutants))
    if poll_count > 0:
        try:
            quality_err = await asyncio.to_thread(mb.quality_continuity_error, 0)
        except Exception:
            quality_err = None

    # Phase 4d: backend-conditional discriminator.  Per audit Appendix A,
    # the legacy backend's quality_continuity_error ignores its
    # pollutant argument so the value reflects "first pollutant" rather
    # than "any specific pollutant" — flag it so callers know.
    engine_kind = session.engine_kind
    unsupported: list[str] = []
    if engine_kind == "legacy" and poll_count > 1:
        # On legacy with multiple pollutants, the single scalar we just
        # fetched cannot distinguish per-pollutant errors.
        unsupported.append("quality_continuity_error")

    return SimulationResult(
        session_id=session_id,
        elapsed_wall_time=round(wall_elapsed, 3),
        steps_completed=steps,
        runoff_continuity_error=runoff_err,
        routing_continuity_error=routing_err,
        quality_continuity_error=quality_err,
        engine_kind=engine_kind,
        unsupported_fields=unsupported or None,
    )


@lifecycle_mcp.tool
async def step_simulation(
    ctx: Context,
    session_id: str = "default",
    num_steps: int = 1,
) -> StepResult:
    """Advance the simulation by one or more timesteps.

    Automatically starts the solver if the session is in the 'initialized'
    state.  Returns the current simulation time and whether the run completed.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(session, "initialized", "running")

    solver = session.solver

    # Auto-start if needed
    if session.state == "initialized":
        await asyncio.to_thread(solver.start)
        session.state = "running"

    completed = False
    steps_taken = 0

    # v1 step() returns a timedelta; the legacy adapter returns bool.
    # We ignore the return value and check solver.state for completion.
    RUNNING = 5
    try:
        for _ in range(num_steps):
            await asyncio.to_thread(solver.step)
            steps_taken += 1
            state = await asyncio.to_thread(lambda: int(solver.state))
            if state != RUNNING:
                completed = True
                break
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Step failed: {exc}") from exc

    # v1 solver.elapsed is timedelta; convert to float decimal days for
    # the JSON wire format.  Legacy adapter already returns float days.
    elapsed_raw = solver.elapsed
    if hasattr(elapsed_raw, "total_seconds"):
        elapsed = elapsed_raw.total_seconds() / 86400.0
    else:
        elapsed = float(elapsed_raw)

    # current_time as days since start, via datetime arithmetic.
    start_dt = await asyncio.to_thread(lambda: solver.start_datetime)
    cur_dt = await asyncio.to_thread(lambda: solver.current_datetime)
    current_time = (cur_dt - start_dt).total_seconds() / 86400.0

    if completed:
        await asyncio.to_thread(solver.end)
        session.state = "ended"

    return StepResult(
        session_id=session_id,
        elapsed=elapsed,
        current_time=current_time,
        completed=completed,
        steps_taken=steps_taken,
    )


@lifecycle_mcp.tool
async def get_simulation_time(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Return the current simulation timing information.

    Includes start time, end time, current time, elapsed fraction, and the
    routing timestep (seconds).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_state(
        session,
        "initialized",
        "running",
        "ended",
    )

    solver = session.solver

    def _gather_timing() -> dict:
        start_dt = solver.start_datetime
        end_dt = solver.end_datetime
        start_t = 0.0
        end_t = (end_dt - start_dt).total_seconds() / 86400.0
        try:
            r_step = solver.routing_step.total_seconds()
        except AttributeError:
            r_step = float(solver.routing_step)

        cur_t: float | None = None
        elapsed_frac = 0.0
        if session.state in ("running", "ended"):
            cur_dt = solver.current_datetime
            cur_t = (cur_dt - start_dt).total_seconds() / 86400.0
            if end_t > 0.0:
                elapsed_frac = cur_t / end_t

        return {
            "start_time": start_t,
            "end_time": end_t,
            "current_time": cur_t,
            "elapsed": round(elapsed_frac, 6),
            "routing_step": r_step,
        }

    t = await asyncio.to_thread(_gather_timing)

    return {
        "session_id": session_id,
        "start_time": t["start_time"],
        "end_time": t["end_time"],
        "current_time": t["current_time"],
        "elapsed": t["elapsed"],
        "routing_step": t["routing_step"],
    }


@lifecycle_mcp.tool
async def get_simulation_state(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Return the current session and solver state.

    Includes the session lifecycle state, the raw engine state code, and basic
    model metadata (counts and file path).
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)

    solver_state: int | None = None
    try:
        solver_state = await asyncio.to_thread(lambda: int(session.solver.state))
    except Exception:
        pass

    node_count: int | None = None
    link_count: int | None = None
    subcatch_count: int | None = None

    if session.state not in ("created", "closed"):
        try:
            node_count = await asyncio.to_thread(lambda: len(session.nodes))
            link_count = await asyncio.to_thread(lambda: len(session.links))
            subcatch_count = await asyncio.to_thread(lambda: len(session.subcatchments))
        except Exception:
            pass

    return {
        "session_id": session_id,
        "session_state": session.state,
        "solver_state": solver_state,
        "node_count": node_count,
        "link_count": link_count,
        "subcatchment_count": subcatch_count,
        "working_dir": str(session.working_dir),
    }


@lifecycle_mcp.tool
async def close_model(
    ctx: Context,
    session_id: str = "default",
) -> dict:
    """Close and clean up a simulation session.

    Tears down the solver (ending the run if necessary), releases all engine
    resources, and removes the session from the registry.
    """
    sm = get_session_manager(ctx)
    await sm.close_session(session_id)
    return {
        "status": "closed",
        "session_id": session_id,
    }


@lifecycle_mcp.tool
async def list_sessions(ctx: Context) -> list[dict]:
    """List all active simulation sessions.

    Returns metadata for every session currently managed by the server.
    """
    sm = get_session_manager(ctx)
    return await sm.list_sessions()


# ===========================================================================
# Events + steady-state skip (Phase 2 wave 2)
#
# The Phase 1 engine work added Python wrappers for the [EVENTS] section
# editor and the steady-state-skip flag. These tools surface them to MCP
# clients.
# ===========================================================================


@lifecycle_mcp.tool()
async def events_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of [EVENTS] rows in the model."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Events editor")
    n = await asyncio.to_thread(lambda: len(session.solver.events))
    return {"session_id": session_id, "count": n}


@lifecycle_mcp.tool()
async def events_get(ctx: Context, session_id: str = "default", index: int = 0) -> dict:
    """Return the start/end OADate of the I{index}-th event."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Events editor")
    _oa_from_dt, dt_to_oa = _oadate_helpers()

    def _read() -> tuple[float, float]:
        ev = session.solver.events[index]
        # v1 Event is a NamedTuple of (start: datetime, end: datetime).
        return dt_to_oa(ev.start), dt_to_oa(ev.end)

    start, end = await asyncio.to_thread(_read)
    return {
        "session_id": session_id,
        "index": index,
        "start_oadate": start,
        "end_oadate": end,
    }


@lifecycle_mcp.tool()
async def events_add(
    ctx: Context,
    session_id: str = "default",
    start_oadate: float = 0.0,
    end_oadate: float = 0.0,
) -> dict:
    """Append a new event window (OADate decimal days)."""
    if end_oadate <= start_oadate:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] end_oadate must be strictly greater "
            f"than start_oadate; got ({start_oadate}, {end_oadate})."
        )
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Events editor")
    oa_to_dt, _ = _oadate_helpers()
    start_dt = oa_to_dt(float(start_oadate))
    end_dt = oa_to_dt(float(end_oadate))

    def _append() -> int:
        events = session.solver.events
        new_idx = len(events)
        events.append((start_dt, end_dt))
        return new_idx

    new_idx = await asyncio.to_thread(_append)
    return {
        "status": "ok",
        "session_id": session_id,
        "index": new_idx,
        "start_oadate": start_oadate,
        "end_oadate": end_oadate,
    }


@lifecycle_mcp.tool()
async def events_set(
    ctx: Context,
    session_id: str = "default",
    index: int = 0,
    start_oadate: float = 0.0,
    end_oadate: float = 0.0,
) -> dict:
    """Overwrite the I{index}-th event window."""
    if end_oadate <= start_oadate:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] end_oadate must be > start_oadate.")
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Events editor")
    oa_to_dt, _ = _oadate_helpers()
    start_dt = oa_to_dt(float(start_oadate))
    end_dt = oa_to_dt(float(end_oadate))

    def _set() -> None:
        session.solver.events[index] = (start_dt, end_dt)

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "index": index,
        "start_oadate": start_oadate,
        "end_oadate": end_oadate,
    }


@lifecycle_mcp.tool()
async def events_remove(ctx: Context, session_id: str = "default", index: int = 0) -> dict:
    """Remove the I{index}-th event; trailing entries shift down."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Events editor")

    def _remove() -> None:
        del session.solver.events[index]

    await asyncio.to_thread(_remove)
    return {"status": "ok", "session_id": session_id, "removed_index": index}


@lifecycle_mcp.tool()
async def events_clear(ctx: Context, session_id: str = "default") -> dict:
    """Remove every event window. Safe on an already-empty list."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Events editor")
    await asyncio.to_thread(lambda: session.solver.events.clear())
    return {"status": "ok", "session_id": session_id, "remaining": 0}


@lifecycle_mcp.tool()
async def is_between_events(ctx: Context, session_id: str = "default") -> dict:
    """Return whether the current sim time falls inside a defined event window."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Events editor")
    between = await asyncio.to_thread(lambda: session.solver.is_between_events)
    return {"session_id": session_id, "between_events": bool(between)}


@lifecycle_mcp.tool()
async def get_steady_state_skip(ctx: Context, session_id: str = "default") -> dict:
    """Return whether SKIP_STEADY_STATE routing skip is enabled."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Steady-state skip")
    enabled = await asyncio.to_thread(lambda: session.solver.steady_state_skip)
    return {"session_id": session_id, "enabled": bool(enabled)}


@lifecycle_mcp.tool()
async def set_steady_state_skip(
    ctx: Context, session_id: str = "default", enabled: bool = False
) -> dict:
    """Enable or disable SKIP_STEADY_STATE routing.

    When enabled the engine skips routing during periods with unchanged
    flows; useful for long dry-weather periods between rainfall events.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Steady-state skip")
    value = bool(enabled)

    def _set() -> None:
        session.solver.steady_state_skip = value

    await asyncio.to_thread(_set)
    return {"status": "ok", "session_id": session_id, "enabled": value}


# ===========================================================================
# Runoff interface file (Phase 1b)
#
# Persist per-subcatchment runoff to a binary file matching the legacy
# SWMM-5 ``Frunoff`` format. The engine auto-emits one record per runoff
# substep when a file is open in SAVE mode. The two tools below wrap the
# Phase 1b ``Solver.open_runoff_interface_write`` / ``open_runoff_interface_read``
# methods so an LLM client can drive the workflow end-to-end through MCP.
# ===========================================================================


@lifecycle_mcp.tool()
async def save_runoff_interface(
    ctx: Context, session_id: str = "default", path: str = "",
) -> dict:
    """Open the runoff interface file for writing (SAVE mode).

    Call this **before** :func:`run_simulation` (or before the first
    :func:`step_simulation`).  The engine auto-emits one record per
    runoff substep until the session is closed, at which point the
    file is finalised automatically — there is no separate "close"
    tool needed for ordinary flows.

    Parameters
    ----------
    session_id:
        Identifier of the session.  Defaults to ``"default"``.
    path:
        Output file path.  Existing content is truncated.  An empty
        string is rejected.

    Returns
    -------
    dict
        ``{"status": "ok", "session_id": ..., "path": ..., "mode": "save"}``
        on success.

    Raises
    ------
    ToolError
        Backend is the legacy engine (this feature requires the new
        engine), the path is empty, or the file could not be opened.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Runoff interface file (Phase 1b)")
    if not path:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] path must be non-empty.")
    try:
        await asyncio.to_thread(session.solver.open_runoff_interface_write, path)
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to open runoff interface "
            f"file for writing: {exc}"
        ) from exc
    return {
        "status": "ok",
        "session_id": session_id,
        "path": path,
        "mode": "save",
    }


@lifecycle_mcp.tool()
async def load_runoff_interface(
    ctx: Context, session_id: str = "default", path: str = "",
) -> dict:
    """Open the runoff interface file for reading (USE mode).

    The file's header is verified against the current model
    (subcatchment count, pollutant count, flow units).

    .. note::

       USE-mode auto-skip — making the engine bypass its own runoff
       computation when the file is open — is a follow-up to Phase 1b.
       Today's USE mode is an advanced manual feature.  After opening,
       the caller must drive the simulation **and** invoke
       ``read_runoff_step`` between :func:`step_simulation` calls
       (currently only exposed through the Python binding, not MCP).
       Most LLM workflows should prefer SAVE mode plus a downstream
       routing-only run that consumes the file via an external script.

    Parameters
    ----------
    session_id:
        Identifier of the session.
    path:
        Path to an existing runoff interface file produced by a
        previous SAVE-mode run.

    Returns
    -------
    dict
        ``{"status": "ok", "session_id": ..., "path": ..., "mode": "use",
        "warning": "..."}`` on success.  The ``warning`` field documents
        the USE-mode caveat so an LLM caller is aware of the manual
        orchestration requirement.

    Raises
    ------
    ToolError
        Backend is the legacy engine, the path is empty, the file is
        missing, or its header does not match the current model.
    """
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Runoff interface file (Phase 1b)")
    if not path:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] path must be non-empty.")
    try:
        await asyncio.to_thread(session.solver.open_runoff_interface_read, path)
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to open runoff interface "
            f"file for reading: {exc}"
        ) from exc
    return {
        "status": "ok",
        "session_id": session_id,
        "path": path,
        "mode": "use",
        "warning": (
            "USE mode currently exposes the file but does not yet skip "
            "the engine's runoff computation. The engine will overwrite "
            "loaded state on every runoff substep. Full USE-mode "
            "auto-skip is a follow-up to Phase 1b."
        ),
    }


# ===========================================================================
# Phase 4 — new v1-only stepping tools
#
# v1 ``Solver`` exposes three convenience APIs the v0 surface lacked:
#
# - ``solver.steps()``  — an iterator yielding the elapsed ``timedelta`` at
#   each step.  Reach-the-end-of-sim or stop-when-some-condition flows can
#   be expressed without an outer loop.
# - ``solver.stride(n)``  — advance N steps in one C call, returning the
#   total elapsed time at the final step.  Useful when an LLM wants to
#   advance "ten steps" in one tool call without paying the asyncio
#   crossover per step.
# - ``solver.until(target)``  — advance until a target ``datetime`` or
#   ``timedelta`` is reached.  The engine stops at the next routing step
#   boundary >= target.
#
# All three auto-start the solver if it's still in ``initialized`` state
# (matches the behaviour of ``step_simulation``).
# ===========================================================================


@lifecycle_mcp.tool()
async def stride(
    ctx: Context,
    session_id: str = "default",
    num_steps: int = 1,
) -> StepResult:
    """Advance the simulation by N timesteps in a single engine call.

    Faster than calling :func:`step_simulation` N times — the C engine
    loops internally, so we pay one ``asyncio.to_thread`` crossing
    regardless of N.

    Auto-starts the solver if the session is in the ``initialized``
    state.

    Parameters
    ----------
    session_id:
        Identifier of the simulation session.
    num_steps:
        Number of timesteps to advance.  Negative or zero raises a
        validation error.
    """
    if num_steps <= 0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] num_steps must be > 0; got {num_steps}."
        )
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Solver.stride")
    require_state(session, "initialized", "running")

    solver = session.solver
    if session.state == "initialized":
        await asyncio.to_thread(solver.start)
        session.state = "running"

    RUNNING = 5

    def _stride_and_inspect() -> tuple[float, int, int]:
        # v1 Solver.stride(n) returns the total elapsed timedelta after the
        # final step; we project to decimal days for the JSON wire shape.
        elapsed_td = solver.stride(num_steps)
        try:
            elapsed_days = elapsed_td.total_seconds() / 86400.0
        except AttributeError:
            elapsed_days = float(elapsed_td)
        state_int = int(solver.state)
        # Solver.stride may short-circuit if the simulation ends partway
        # through.  We can't directly tell how many steps were actually
        # advanced, so report num_steps as a best-effort upper bound and
        # let the caller compare elapsed_days against end_time.
        return elapsed_days, num_steps, state_int

    try:
        elapsed_days, steps_taken, state_int = await asyncio.to_thread(_stride_and_inspect)
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] stride failed: {exc}") from exc

    completed = state_int != RUNNING

    # Compute the current_time in days-since-start for the response shape.
    def _current_days() -> float:
        return (
            solver.current_datetime - solver.start_datetime
        ).total_seconds() / 86400.0

    current_time = await asyncio.to_thread(_current_days)

    if completed:
        await asyncio.to_thread(solver.end)
        session.state = "ended"

    return StepResult(
        session_id=session_id,
        elapsed=elapsed_days,
        current_time=current_time,
        completed=completed,
        steps_taken=steps_taken,
    )


@lifecycle_mcp.tool()
async def until_elapsed(
    ctx: Context,
    session_id: str = "default",
    seconds: float = 0.0,
) -> StepResult:
    """Advance the simulation until at least *seconds* of sim-time have elapsed.

    Maps to ``Solver.until(timedelta(seconds=seconds))``.  The engine
    stops at the next routing-step boundary >= the target.  Auto-starts
    the solver if needed.

    Parameters
    ----------
    seconds:
        Target elapsed simulation time in seconds, measured from the
        start of the simulation (not from the current step).
    """
    if seconds <= 0.0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] seconds must be > 0; got {seconds}."
        )
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Solver.until (elapsed)")
    require_state(session, "initialized", "running")

    solver = session.solver
    if session.state == "initialized":
        await asyncio.to_thread(solver.start)
        session.state = "running"

    target = timedelta(seconds=float(seconds))

    RUNNING = 5

    def _until() -> tuple[float, int]:
        elapsed_td = solver.until(target)
        try:
            elapsed_days = elapsed_td.total_seconds() / 86400.0
        except AttributeError:
            elapsed_days = float(elapsed_td)
        return elapsed_days, int(solver.state)

    try:
        elapsed_days, state_int = await asyncio.to_thread(_until)
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] until_elapsed failed: {exc}") from exc

    completed = state_int != RUNNING

    def _current_days() -> float:
        return (
            solver.current_datetime - solver.start_datetime
        ).total_seconds() / 86400.0

    current_time = await asyncio.to_thread(_current_days)

    if completed:
        await asyncio.to_thread(solver.end)
        session.state = "ended"

    return StepResult(
        session_id=session_id,
        elapsed=elapsed_days,
        current_time=current_time,
        completed=completed,
        steps_taken=0,  # the engine doesn't surface a step count for until()
    )


@lifecycle_mcp.tool()
async def until_datetime(
    ctx: Context,
    session_id: str = "default",
    target_iso: str = "",
) -> StepResult:
    """Advance the simulation until the wall-clock simulation datetime reaches *target_iso*.

    Maps to ``Solver.until(datetime)``.  The engine stops at the next
    routing-step boundary >= the target datetime.  Auto-starts the
    solver if needed.

    Parameters
    ----------
    target_iso:
        ISO-8601 datetime string (e.g. ``"2026-01-01T12:00:00"``).  Must
        be > the simulation start and <= the simulation end.
    """
    from datetime import datetime as _datetime

    if not target_iso:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] target_iso is required.")
    try:
        target_dt = _datetime.fromisoformat(target_iso)
    except ValueError as exc:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] target_iso is not a valid ISO-8601 "
            f"datetime: {exc}"
        ) from exc

    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Solver.until (datetime)")
    require_state(session, "initialized", "running")

    solver = session.solver
    if session.state == "initialized":
        await asyncio.to_thread(solver.start)
        session.state = "running"

    RUNNING = 5

    def _until() -> tuple[float, int]:
        elapsed_td = solver.until(target_dt)
        try:
            elapsed_days = elapsed_td.total_seconds() / 86400.0
        except AttributeError:
            elapsed_days = float(elapsed_td)
        return elapsed_days, int(solver.state)

    try:
        elapsed_days, state_int = await asyncio.to_thread(_until)
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] until_datetime failed: {exc}") from exc

    completed = state_int != RUNNING

    def _current_days() -> float:
        return (
            solver.current_datetime - solver.start_datetime
        ).total_seconds() / 86400.0

    current_time = await asyncio.to_thread(_current_days)

    if completed:
        await asyncio.to_thread(solver.end)
        session.state = "ended"

    return StepResult(
        session_id=session_id,
        elapsed=elapsed_days,
        current_time=current_time,
        completed=completed,
        steps_taken=0,
    )


@lifecycle_mcp.tool()
async def run_for_steps(
    ctx: Context,
    session_id: str = "default",
    max_steps: int = 100,
    progress_interval: int = 0,
) -> StepResult:
    """Run up to *max_steps* steps using the v1 ``Solver.steps()`` iterator.

    Slightly different from ``stride(max_steps)``: ``stride`` is one C
    call, while ``run_for_steps`` issues the steps inside a Python loop
    so progress can be reported (via ``ctx.report_progress``) every
    *progress_interval* steps.  Use ``stride`` for raw speed, this one
    when you want intermediate progress.

    The simulation stops at whichever happens first: *max_steps* steps
    completed, or the engine reaches the end of the simulation.
    Auto-starts the solver if needed.

    Parameters
    ----------
    max_steps:
        Upper bound on steps to advance.
    progress_interval:
        Emit progress every N steps (0 = no progress).
    """
    if max_steps <= 0:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] max_steps must be > 0; got {max_steps}."
        )
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Solver.steps iterator")
    require_state(session, "initialized", "running")

    solver = session.solver
    if session.state == "initialized":
        await asyncio.to_thread(solver.start)
        session.state = "running"

    # Resolve total_seconds for progress reporting.
    start_dt = await asyncio.to_thread(lambda: solver.start_datetime)
    end_dt = await asyncio.to_thread(lambda: solver.end_datetime)
    total_seconds = max((end_dt - start_dt).total_seconds(), 1.0)

    RUNNING = 5

    def _step_once() -> tuple[float, int]:
        # One step, then read state + elapsed.
        elapsed_td = solver.step()
        try:
            elapsed_days = elapsed_td.total_seconds() / 86400.0
        except AttributeError:
            elapsed_days = float(elapsed_td) if elapsed_td else 0.0
        return elapsed_days, int(solver.state)

    steps_taken = 0
    elapsed_days = 0.0
    state_int = RUNNING
    try:
        for _ in range(max_steps):
            elapsed_days, state_int = await asyncio.to_thread(_step_once)
            steps_taken += 1
            if state_int != RUNNING:
                break
            if progress_interval > 0 and steps_taken % progress_interval == 0:
                try:
                    cur_dt = await asyncio.to_thread(lambda: solver.current_datetime)
                    elapsed_sim = (cur_dt - start_dt).total_seconds()
                    pct = min(int((elapsed_sim / total_seconds) * 100), 99)
                    await ctx.report_progress(pct, 100)
                except Exception:
                    pass
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] run_for_steps failed at step {steps_taken}: {exc}"
        ) from exc

    completed = state_int != RUNNING

    def _current_days() -> float:
        return (
            solver.current_datetime - solver.start_datetime
        ).total_seconds() / 86400.0

    current_time = await asyncio.to_thread(_current_days)

    if completed:
        await asyncio.to_thread(solver.end)
        session.state = "ended"

    return StepResult(
        session_id=session_id,
        elapsed=elapsed_days,
        current_time=current_time,
        completed=completed,
        steps_taken=steps_taken,
    )
