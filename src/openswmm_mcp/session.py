"""Session management for the OpenSWMM MCP server.

Provides :class:`SimSession` (a thin wrapper around a :class:`Solver` with lazy
domain accessors) and :class:`SessionManager` (a concurrent-safe registry of
active sessions).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openswmm.engine import (
    Controls,
    Forcing,
    Gages,
    HotStart,
    Inflows,
    Infrastructure,
    Links,
    MassBalance,
    ModelBuilder,
    Nodes,
    OutputReader,
    Pollutants,
    Quality,
    Solver,
    Spatial,
    Statistics,
    Subcatchments,
    Tables,
)

from openswmm_mcp.errors import ErrorCode, ToolError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapping from attribute name -> domain class constructor
# ---------------------------------------------------------------------------

_DOMAIN_CLASSES: dict[str, type] = {
    "nodes": Nodes,
    "links": Links,
    "subcatchments": Subcatchments,
    "gages": Gages,
    "forcing": Forcing,
    "mass_balance": MassBalance,
    "pollutants": Pollutants,
    "statistics": Statistics,
    "spatial": Spatial,
    "tables": Tables,
    "controls": Controls,
    "inflows": Inflows,
    "infrastructure": Infrastructure,
    "quality": Quality,
    "hotstart": HotStart,
}


# ---------------------------------------------------------------------------
# SimSession
# ---------------------------------------------------------------------------


@dataclass
class SimSession:
    """Wraps a :class:`Solver` instance with lazy domain accessors.

    Domain objects (``nodes``, ``links``, ``subcatchments``, etc.) are created
    on first access and cached for the lifetime of the session.  This avoids
    constructing objects that a particular tool invocation may never touch.

    Parameters
    ----------
    solver:
        The underlying SWMM engine handle.
    state:
        Human-readable lifecycle label.  One of ``"created"``, ``"opened"``,
        ``"initialized"``, ``"running"``, ``"ended"``, ``"closed"``.
    working_dir:
        Scratch directory used for temporary / output files.
    model_builder:
        Optional :class:`ModelBuilder` when the session was created
        programmatically rather than from an ``.inp`` file.
    """

    solver: Solver
    state: str = "created"
    working_dir: Path = field(default_factory=lambda: Path("."))
    model_builder: ModelBuilder | None = None

    # Private fields ---------------------------------------------------------
    _output_reader: OutputReader | None = field(default=None, repr=False)
    _cache: dict[str, Any] = field(default_factory=dict, repr=False)

    # -- Lazy domain accessors via __getattr__ -------------------------------

    def __getattr__(self, name: str) -> Any:
        """Lazily instantiate and cache domain accessor objects.

        Any attribute listed in :data:`_DOMAIN_CLASSES` is constructed the
        first time it is requested and then stored in ``_cache`` so that
        subsequent accesses return the same instance.
        """
        if name in _DOMAIN_CLASSES:
            # Guard against infinite recursion during __init__
            cache = object.__getattribute__(self, "_cache")
            if name not in cache:
                solver = object.__getattribute__(self, "solver")
                cache[name] = _DOMAIN_CLASSES[name](solver)
            return cache[name]
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    # -- OutputReader property -----------------------------------------------

    @property
    def output_reader(self) -> OutputReader | None:
        """Return the current :class:`OutputReader`, if one has been set."""
        return self._output_reader

    @output_reader.setter
    def output_reader(self, value: OutputReader | None) -> None:
        self._output_reader = value

    # -- Cleanup -------------------------------------------------------------

    def cleanup(self) -> None:
        """Safely tear down the solver regardless of current state.

        The method walks backward through the engine lifecycle so that the
        solver is left fully destroyed even if it was mid-run when cleanup
        was requested.  Individual lifecycle calls are wrapped in
        ``try / except`` so that one failure does not prevent subsequent
        teardown steps.
        """
        state = self.state

        if state in ("running", "initialized"):
            try:
                self.solver.end()
                self.state = "ended"
                state = "ended"
            except Exception:
                logger.debug("solver.end() failed during cleanup", exc_info=True)

        if state == "ended":
            try:
                self.solver.report()
            except Exception:
                logger.debug("solver.report() failed during cleanup", exc_info=True)

        if state in ("ended", "opened"):
            try:
                self.solver.close()
                self.state = "closed"
                state = "closed"
            except Exception:
                logger.debug("solver.close() failed during cleanup", exc_info=True)

        # Always attempt to destroy the underlying C handle.
        try:
            self.solver.destroy()
        except Exception:
            logger.debug("solver.destroy() failed during cleanup", exc_info=True)

        self.state = "closed"
        self._cache.clear()


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """Thread/async-safe registry of :class:`SimSession` instances.

    Parameters
    ----------
    max_sessions:
        Maximum number of concurrent sessions.  Attempts to exceed this limit
        raise :class:`ToolError`.
    working_dir:
        Root directory under which per-session working directories are placed.
    """

    def __init__(self, max_sessions: int = 5, working_dir: str = "./") -> None:
        self._sessions: dict[str, SimSession] = {}
        self._lock = asyncio.Lock()
        self._max_sessions = max_sessions
        self._working_dir = Path(working_dir)

    # -- Public API ----------------------------------------------------------

    async def create_session(
        self,
        session_id: str,
        inp_path: str,
        rpt_path: str | None = None,
        out_path: str | None = None,
    ) -> SimSession:
        """Create a new :class:`SimSession` and register it.

        The solver is created and its file paths are set, but the caller is
        responsible for opening / initializing / starting the engine.

        Parameters
        ----------
        session_id:
            Unique identifier for the session.
        inp_path:
            Path to the SWMM ``.inp`` input file.
        rpt_path:
            Optional path for the report file.  Defaults to
            ``<inp_stem>.rpt`` next to the input file.
        out_path:
            Optional path for the binary output file.  Defaults to
            ``<inp_stem>.out`` next to the input file.

        Returns
        -------
        SimSession
            The newly created (but not yet opened) session.

        Raises
        ------
        ToolError
            If *max_sessions* has been reached or *session_id* already exists.
        """
        async with self._lock:
            if session_id in self._sessions:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Session '{session_id}' already exists."
                )

            if len(self._sessions) >= self._max_sessions:
                raise ToolError(
                    f"[{ErrorCode.MAX_SESSIONS_REACHED}] "
                    f"Maximum number of sessions ({self._max_sessions}) reached. "
                    "Close an existing session first."
                )

            inp = Path(inp_path)

            if rpt_path is None:
                rpt_path = str(inp.with_suffix(".rpt"))
            if out_path is None:
                out_path = str(inp.with_suffix(".out"))

            solver = Solver(str(inp), rpt_path, out_path)

            session_dir = self._working_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)

            session = SimSession(
                solver=solver,
                state="created",
                working_dir=session_dir,
            )

            self._sessions[session_id] = session
            logger.info("Session '%s' created (inp=%s)", session_id, inp_path)
            return session

    async def get_session(self, session_id: str) -> SimSession:
        """Retrieve an existing session by its identifier.

        Raises
        ------
        ToolError
            If no session with *session_id* exists.
        """
        async with self._lock:
            session = self._sessions.get(session_id)

        if session is None:
            raise ToolError(f"[{ErrorCode.SESSION_NOT_FOUND}] No session with id '{session_id}'.")
        return session

    async def close_session(self, session_id: str) -> None:
        """Clean up and remove a session.

        Raises
        ------
        ToolError
            If no session with *session_id* exists.
        """
        async with self._lock:
            session = self._sessions.pop(session_id, None)

        if session is None:
            raise ToolError(f"[{ErrorCode.SESSION_NOT_FOUND}] No session with id '{session_id}'.")

        session.cleanup()
        logger.info("Session '%s' closed and removed.", session_id)

    async def list_sessions(self) -> list[dict[str, Any]]:
        """Return metadata for every active session.

        Returns
        -------
        list[dict]
            Each dict contains ``id``, ``state``, and ``working_dir``.
        """
        async with self._lock:
            items = list(self._sessions.items())

        return [
            {
                "id": sid,
                "state": session.state,
                "working_dir": str(session.working_dir),
            }
            for sid, session in items
        ]

    async def cleanup_all(self) -> None:
        """Tear down every active session.

        Intended to be called during server shutdown so that engine handles
        are not leaked.
        """
        async with self._lock:
            sessions = list(self._sessions.items())
            self._sessions.clear()

        for sid, session in sessions:
            try:
                session.cleanup()
                logger.info("Session '%s' cleaned up during shutdown.", sid)
            except Exception:
                logger.warning(
                    "Failed to clean up session '%s' during shutdown.",
                    sid,
                    exc_info=True,
                )
