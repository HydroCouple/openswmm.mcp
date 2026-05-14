"""Lifecycle tools for the OpenSWMM MCP server.

Provides tools for opening, running, stepping, and closing SWMM models.
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import get_session_manager, require_state
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import ModelSummary, SimulationResult, StepResult

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

    # Gather counts from domain accessors
    nodes = session.nodes
    links = session.links
    subcatchments = session.subcatchments
    gages = session.gages
    pollutants = session.pollutants
    solver = session.solver

    node_count = await asyncio.to_thread(nodes.count)
    link_count = await asyncio.to_thread(links.count)
    subcatch_count = await asyncio.to_thread(subcatchments.count)
    gage_count = await asyncio.to_thread(gages.count)
    pollutant_count = await asyncio.to_thread(pollutants.count)

    start_time = await asyncio.to_thread(solver.get_start_time)
    end_time = await asyncio.to_thread(solver.get_end_time)
    routing_step = await asyncio.to_thread(solver.get_routing_step)

    # New engine returns numeric code strings ("0"); legacy returns names ("CFS").
    try:
        raw_units = await asyncio.to_thread(solver.get_option, "FLOW_UNITS")
        try:
            flow_units = _flow_units_name(int(raw_units))
        except (ValueError, TypeError):
            flow_units = str(raw_units).upper() or "UNKNOWN"
    except Exception:
        flow_units = "UNKNOWN"

    try:
        raw_route = await asyncio.to_thread(solver.get_option, "FLOW_ROUTING")
        try:
            route_model = _route_model_name(int(raw_route))
        except (ValueError, TypeError):
            route_model = str(raw_route).upper() or "UNKNOWN"
    except Exception:
        route_model = "UNKNOWN"

    return ModelSummary(
        session_id=session_id,
        state=session.state,
        engine=session.engine_kind,
        node_count=node_count,
        link_count=link_count,
        subcatchment_count=subcatch_count,
        gage_count=gage_count,
        pollutant_count=pollutant_count,
        flow_units=flow_units,
        route_model=route_model,
        start_time=start_time,
        end_time=end_time,
        routing_step=routing_step,
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

    start_time = await asyncio.to_thread(solver.get_start_time)
    end_time = await asyncio.to_thread(solver.get_end_time)
    total_duration = end_time - start_time if end_time > start_time else 1.0

    steps = 0
    wall_start = time.monotonic()

    # Detect completion by polling solver.state: step() returns an error code
    # (0 = success), NOT a "more steps?" boolean.
    try:
        solver_state = await asyncio.to_thread(lambda: solver.state)
        while solver_state == 5:  # EngineState.RUNNING == 5
            rc = await asyncio.to_thread(solver.step)
            if rc != 0:
                raise RuntimeError(f"step() returned error code {rc}")
            steps += 1

            solver_state = await asyncio.to_thread(lambda: solver.state)

            # Report progress based on elapsed simulation time
            if steps % 100 == 0:
                current = await asyncio.to_thread(solver.get_current_time)
                elapsed_sim = current - start_time
                pct = min(int((elapsed_sim / total_duration) * 100), 99)
                await ctx.report_progress(pct, 100)

        await ctx.report_progress(100, 100)

        await asyncio.to_thread(solver.end)
        session.state = "ended"
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Simulation failed at step {steps}: {exc}"
        ) from exc

    wall_elapsed = time.monotonic() - wall_start

    # Retrieve continuity errors
    mb = session.mass_balance

    runoff_err = await asyncio.to_thread(mb.get_runoff_continuity_error)
    routing_err = await asyncio.to_thread(mb.get_routing_continuity_error)

    quality_err: float | None = None
    pollutants = session.pollutants
    poll_count = await asyncio.to_thread(pollutants.count)
    if poll_count > 0:
        try:
            quality_err = await asyncio.to_thread(mb.get_quality_continuity_error, 0)
        except Exception:
            quality_err = None

    return SimulationResult(
        session_id=session_id,
        elapsed_wall_time=round(wall_elapsed, 3),
        steps_completed=steps,
        runoff_continuity_error=runoff_err,
        routing_continuity_error=routing_err,
        quality_continuity_error=quality_err,
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

    # step() returns error code (0 = success); check solver.state for completion.
    try:
        for _ in range(num_steps):
            rc = await asyncio.to_thread(solver.step)
            if rc != 0:
                raise RuntimeError(f"step() returned error code {rc}")
            steps_taken += 1
            state = await asyncio.to_thread(lambda: solver.state)
            if state != 5:  # EngineState.RUNNING == 5
                completed = True
                break
    except Exception as exc:
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] Step failed: {exc}") from exc

    elapsed = solver.elapsed
    current_time = await asyncio.to_thread(solver.get_current_time)

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
    start_time = await asyncio.to_thread(solver.get_start_time)
    end_time = await asyncio.to_thread(solver.get_end_time)
    routing_step = await asyncio.to_thread(solver.get_routing_step)

    current_time: float | None = None
    elapsed: float = 0.0

    if session.state in ("running", "ended"):
        current_time = await asyncio.to_thread(solver.get_current_time)
        total = end_time - start_time
        elapsed = (current_time - start_time) / total if total > 0 else 0.0

    return {
        "session_id": session_id,
        "start_time": start_time,
        "end_time": end_time,
        "current_time": current_time,
        "elapsed": round(elapsed, 6),
        "routing_step": routing_step,
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
        solver_state = await asyncio.to_thread(lambda: session.solver.state)
    except Exception:
        pass

    node_count: int | None = None
    link_count: int | None = None
    subcatch_count: int | None = None

    if session.state not in ("created", "closed"):
        try:
            node_count = await asyncio.to_thread(session.nodes.count)
            link_count = await asyncio.to_thread(session.links.count)
            subcatch_count = await asyncio.to_thread(session.subcatchments.count)
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
