"""Hot-start save/load and session-cloning tools."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from fastmcp import Context, FastMCP

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import HotStartResult

hotstart_mcp = FastMCP("hotstart")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session_manager(ctx: Context):
    """Extract the :class:`SessionManager` from the lifespan context."""
    try:
        return ctx.lifespan_context["session_manager"]
    except (KeyError, TypeError) as exc:
        raise ToolError(
            "Session manager is not available. The server may not have started correctly."
        ) from exc


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
    inp_path = str(source.solver.inp_path)
    target = await sm.create_session(target_id, inp_path)

    # Open and initialise the new session's solver
    await asyncio.to_thread(target.solver.open)
    target.state = "opened"
    await asyncio.to_thread(target.solver.init)
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
