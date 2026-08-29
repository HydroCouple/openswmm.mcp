"""Hot-start save/load and session-cloning tools."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import get_session_manager as _get_session_manager
from openswmm_mcp.dependencies import require_new_engine
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.models import HotStartResult

hotstart_mcp = FastMCP("hotstart")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@hotstart_mcp.tool()
async def save_hotstart(
    ctx: Context,
    session_id: str = "default",
    path: str = "",
) -> HotStartResult:
    """Save the current simulation state to a hot-start file.

    The session must be in the ``"running"`` or ``"ended"`` state so that
    there is meaningful hydraulic state to persist.

    Parameters
    ----------
    session_id:
        Identifier of the session to save.  Defaults to ``"default"``.
    path:
        File-system path for the hot-start file.  When empty, a path is
        generated inside the session's working directory.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)

    if session.state not in ("running", "ended"):
        raise ToolError(
            f"Cannot save hot-start: session is in state '{session.state}', "
            "but must be in one of: 'running', 'ended'."
        )

    if not path:
        path = str(session.working_dir / f"{session_id}.hsf")

    # wraps: swmm_hotstart_save
    await asyncio.to_thread(session.hotstart.save, session.solver, path)

    return HotStartResult(
        status="saved",
        path=path,
        message=f"Hot-start state saved for session '{session_id}'.",
    )


@hotstart_mcp.tool()
async def load_hotstart(
    ctx: Context,
    session_id: str = "default",
    path: str = "",
) -> HotStartResult:
    """Load a previously saved hot-start file into a session.

    The hot-start file is opened and its state is applied to the session's
    solver, allowing a simulation to resume from a saved checkpoint.

    Parameters
    ----------
    session_id:
        Identifier of the target session.  Defaults to ``"default"``.
    path:
        File-system path of the hot-start file to load.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)

    if not path:
        raise ToolError("A hot-start file path must be provided.")

    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ToolError(f"Hot-start file not found: {resolved}")

    hotstart = await asyncio.to_thread(session.hotstart.open, str(resolved))
    await asyncio.to_thread(hotstart.apply, session.solver)

    return HotStartResult(
        status="loaded",
        path=str(resolved),
        message=f"Hot-start state applied to session '{session_id}'.",
    )


@hotstart_mcp.tool()
async def seed_hotstart_state(
    ctx: Context,
    session_id: str = "default",
    path: str = "",
    node_depths: dict[str, float] | None = None,
    node_heads: dict[str, float] | None = None,
    link_depths: dict[str, float] | None = None,
    link_flows: dict[str, float] | None = None,
    subcatchment_runoffs: dict[str, float] | None = None,
) -> HotStartResult:
    """Seed specific element states from a hot-start file into a session.

    Opens the hot-start file at ``path``, overrides individual element
    states with the supplied id→value maps, and applies the result to the
    session's solver. This surfaces the engine's hot-start state setters
    (``set_node_depth`` / ``set_node_head`` / ``set_link_depth`` /
    ``set_link_flow`` / ``set_subcatchment_runoff``) so callers can build
    deterministic initial conditions — e.g. reproducible RL episode resets.

    All values are in the model's project units (see ``get_unit_system``):
    depths/heads in project length units, flows in project flow units,
    runoff in project flow units.

    Parameters
    ----------
    session_id:
        Target session. Defaults to ``"default"``.
    path:
        Hot-start file to open as the seed baseline (required, must exist).
    node_depths, node_heads:
        Maps of node id → depth / head override.
    link_depths, link_flows:
        Maps of link id → depth / flow override.
    subcatchment_runoffs:
        Map of subcatchment id → runoff override.
    """
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)

    if not path:
        raise ToolError("A hot-start file path must be provided.")
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ToolError(f"Hot-start file not found: {resolved}")

    overrides = {
        "node_depths": node_depths or {},
        "node_heads": node_heads or {},
        "link_depths": link_depths or {},
        "link_flows": link_flows or {},
        "subcatchment_runoffs": subcatchment_runoffs or {},
    }

    # ``HotStart.apply`` requires an INITIALIZED solver (post-initialize,
    # pre-start). The session may be in any other state (e.g. "ended" after a
    # run), so re-open and initialize a fresh solver on the same model first.
    if session.state != "initialized" and session.backend is not None:
        from openswmm_mcp.backends import make_backend

        engine_kind = session.engine_kind

        def _reopen_initialized() -> None:
            backend = make_backend(
                engine_kind, session.inp_path, session.rpt_path, session.out_path
            )
            backend.solver.open()
            backend.solver.initialize()
            session.backend = backend
            session.state = "initialized"
            session._meta = None

        await asyncio.to_thread(_reopen_initialized)

    def _seed():
        hotstart = session.hotstart.open(str(resolved))
        for nid, v in overrides["node_depths"].items():
            hotstart.set_node_depth(nid, float(v))
        for nid, v in overrides["node_heads"].items():
            hotstart.set_node_head(nid, float(v))
        for lid, v in overrides["link_depths"].items():
            hotstart.set_link_depth(lid, float(v))
        for lid, v in overrides["link_flows"].items():
            hotstart.set_link_flow(lid, float(v))
        for sid, v in overrides["subcatchment_runoffs"].items():
            hotstart.set_subcatchment_runoff(sid, float(v))
        hotstart.apply(session.solver)

    await asyncio.to_thread(_seed)

    n_overrides = sum(len(d) for d in overrides.values())
    return HotStartResult(
        status="seeded",
        path=str(resolved),
        message=(
            f"Seeded {n_overrides} element override(s) from hot-start into session '{session_id}'."
        ),
    )


@hotstart_mcp.tool()
async def get_file_sim_time(ctx: Context, session_id: str = "default", path: str = "") -> dict:
    """Return the simulation moment stored *inside* a hot-start file.

    This is the timestamp the state was captured at, read from the file's
    header — not the live clock. ``lifecycle_get_simulation_time`` reports the
    running session's current time; this tool answers "what point in the run
    does this checkpoint represent?" without applying it to anything.

    No session state is touched; ``session_id`` is accepted only so the tool
    is uniform with the rest of the namespace.

    Parameters
    ----------
    path:
        Path to an existing hot-start file.
    """
    # wraps: swmm_hotstart_get_sim_time
    from openswmm.engine import datetime_to_oadate

    if not path:
        raise ToolError("A hot-start file path must be provided.")
    resolved = Path(path).expanduser().resolve()
    if not resolved.exists():
        raise ToolError(f"Hot-start file not found: {resolved}")

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Hot-start file metadata")

    def _read() -> tuple[str, float]:
        hs = session.hotstart.open(str(resolved))
        when = hs.sim_datetime
        return when.isoformat(), datetime_to_oadate(when)

    iso, oadate = await asyncio.to_thread(_read)
    return {
        "session_id": session_id,
        "path": str(resolved),
        "sim_datetime": iso,
        "sim_datetime_oadate": oadate,
    }


@hotstart_mcp.tool()
async def clone_session(
    ctx: Context,
    source_id: str = "",
    target_id: str = "",
) -> dict:
    """Clone an existing session by saving and re-applying its hot-start state.

    A new session is created using the same ``.inp`` file as the source.
    The source's current hydraulic state is written to a temporary hot-start
    file and then applied to the freshly opened target session.

    Parameters
    ----------
    source_id:
        Identifier of the session to clone.
    target_id:
        Identifier for the new cloned session.
    """
    if not source_id:
        raise ToolError("source_id is required.")
    if not target_id:
        raise ToolError("target_id is required.")

    sm = _get_session_manager(ctx)
    source = await sm.get_session(source_id)
    require_new_engine(source, "Session cloning")

    if source.state not in ("running", "ended"):
        raise ToolError(
            f"Cannot clone: source session is in state '{source.state}', "
            "but must be in one of: 'running', 'ended'."
        )

    # Save hot-start from source to a temporary file
    with tempfile.NamedTemporaryFile(suffix=".hsf", delete=False) as tmp:
        tmp_path = tmp.name

    await asyncio.to_thread(source.hotstart.save, source.solver, tmp_path)

    # Create a new session using the same inp file as the source
    inp_path = source.inp_path
    target = await sm.create_session(target_id, inp_path)

    # Open and initialise the new session's solver
    await asyncio.to_thread(target.solver.open)
    target.state = "opened"
    await asyncio.to_thread(target.solver.initialize)
    target.state = "initialized"

    # Apply the hot-start state
    hotstart = await asyncio.to_thread(target.hotstart.open, tmp_path)
    await asyncio.to_thread(hotstart.apply, target.solver)

    # Clean up the temporary file
    try:
        Path(tmp_path).unlink()
    except OSError:
        pass

    return {
        "status": "cloned",
        "source_id": source_id,
        "target_id": target_id,
        "inp_path": inp_path,
        "message": (
            f"Session '{target_id}' cloned from '{source_id}' with hot-start state applied."
        ),
    }


# ===========================================================================
# Multi-slot saves management (Phase 2 wave 2)
#
# The Phase 1 engine work added saves-list editor functions to HotStart;
# these wrap them as MCP tools so callers can configure scheduled hotstart
# saves in the model's [FILES] section without writing .inp text by hand.
# ===========================================================================


@hotstart_mcp.tool()
async def saves_count(ctx: Context, session_id: str = "default") -> dict:
    """Return the number of scheduled SAVE HOTSTART entries in [FILES]."""
    # wraps: swmm_hotstart_saves_count
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Hotstart saves management")
    n = await asyncio.to_thread(lambda: len(session.solver.save_schedule))
    return {"session_id": session_id, "count": n}


@hotstart_mcp.tool()
async def saves_get(ctx: Context, session_id: str = "default", index: int = 0) -> dict:
    """Return the path + datetime of the I{index}-th scheduled save."""
    # wraps: swmm_hotstart_saves_get_path swmm_hotstart_saves_get_datetime
    from openswmm.engine import datetime_to_oadate

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Hotstart saves management")

    def _read() -> tuple[str, float]:
        # v1 SaveScheduleEntry NamedTuple: (when: datetime, path: str).
        entry = session.solver.save_schedule[index]
        return entry.path, datetime_to_oadate(entry.when)

    path, dt = await asyncio.to_thread(_read)
    return {
        "session_id": session_id,
        "index": index,
        "path": path,
        "datetime_oadate": dt,
    }


@hotstart_mcp.tool()
async def saves_add(
    ctx: Context,
    session_id: str = "default",
    path: str = "",
    datetime_oadate: float = 0.0,
) -> dict:
    """Append a new SAVE HOTSTART entry.

    ``datetime_oadate`` is decimal days (OADate). Use ``0.0`` to schedule
    a save at end of simulation.
    """
    if not path:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] path must not be empty.")
    from openswmm.engine import oadate_to_datetime

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Hotstart saves management")
    when = oadate_to_datetime(float(datetime_oadate))

    def _append() -> int:
        sched = session.solver.save_schedule
        sched.append((when, path))
        return len(sched)

    n = await asyncio.to_thread(_append)
    return {
        "status": "ok",
        "session_id": session_id,
        "index": n - 1,
        "path": path,
        "datetime_oadate": datetime_oadate,
    }


@hotstart_mcp.tool()
async def saves_set(
    ctx: Context,
    session_id: str = "default",
    index: int = 0,
    path: str | None = None,
    datetime_oadate: float | None = None,
) -> dict:
    """Update the path and/or datetime of the I{index}-th scheduled save.

    Fields not supplied (None) are left unchanged.  v1 SaveSchedule
    requires a full entry replacement, so we read-modify-write.
    """
    # wraps: swmm_hotstart_saves_set_path swmm_hotstart_saves_set_datetime swmm_hotstart_saves_get_path swmm_hotstart_saves_get_datetime  # noqa: E501
    from openswmm.engine import datetime_to_oadate, oadate_to_datetime

    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Hotstart saves management")

    def _update() -> tuple[str, float]:
        sched = session.solver.save_schedule
        existing = sched[index]
        new_when = (
            oadate_to_datetime(float(datetime_oadate))
            if datetime_oadate is not None
            else existing.when
        )
        new_path = path if path is not None else existing.path
        sched[index] = (new_when, new_path)
        return new_path, datetime_to_oadate(new_when)

    final_path, final_oadate = await asyncio.to_thread(_update)
    return {
        "status": "ok",
        "session_id": session_id,
        "index": index,
        "path": final_path,
        "datetime_oadate": final_oadate,
    }


@hotstart_mcp.tool()
async def saves_remove(ctx: Context, session_id: str = "default", index: int = 0) -> dict:
    """Remove the I{index}-th scheduled save. Trailing entries shift down."""
    # wraps: swmm_hotstart_saves_remove
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Hotstart saves management")

    def _remove() -> None:
        del session.solver.save_schedule[index]

    await asyncio.to_thread(_remove)
    return {"status": "ok", "session_id": session_id, "removed_index": index}


@hotstart_mcp.tool()
async def saves_clear(ctx: Context, session_id: str = "default") -> dict:
    """Remove every scheduled save."""
    sm = _get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Hotstart saves management")
    await asyncio.to_thread(lambda: session.solver.save_schedule.clear())
    return {"status": "ok", "session_id": session_id, "remaining": 0}
