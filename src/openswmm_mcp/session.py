"""Session management for the OpenSWMM MCP server.

Provides :class:`SimSession` (a thin wrapper around a :class:`Backend`) and
:class:`SessionManager` (a concurrent-safe registry of active sessions).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, get_args

from openswmm_mcp.backends import Backend, EngineKind, make_backend
from openswmm_mcp.errors import ErrorCode, ToolError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SimSession
# ---------------------------------------------------------------------------


@dataclass
class SimSession:
    """Wraps a :class:`Backend` instance with lazy domain-accessor delegation.

    The backend supplies ``solver``, ``nodes``, ``links``, etc. and tools call
    those attributes through this session object as if it were the backend
    itself.  Setting ``session.<attr>`` for arbitrary attributes (e.g.
    ``model_builder``, ``output_reader``) is still supported for tools that
    cache state on the session.

    Parameters
    ----------
    backend:
        The engine backend wrapping the underlying solver and domain objects.
        Optional: ``None`` while in the ``"building"`` state (no solver
        exists yet — the session uses ``model_builder`` instead).
    state:
        Human-readable lifecycle label.  One of ``"created"``, ``"opened"``,
        ``"initialized"``, ``"running"``, ``"ended"``, ``"closed"``,
        ``"building"``.
    working_dir:
        Scratch directory used for temporary / output files.
    inp_path / rpt_path / out_path:
        File paths the session was created with.  Stored on the session so
        tools that need them (e.g. the output reader) don't depend on
        engine-specific solver attributes.
    model_builder:
        Optional new-engine ``ModelBuilder`` when the session was created
        programmatically rather than from an ``.inp`` file.  Always ``None``
        on the legacy backend.
    """

    backend: Backend | None = None
    state: str = "created"
    working_dir: Path = field(default_factory=lambda: Path("."))
    inp_path: str = ""
    rpt_path: str = ""
    out_path: str = ""
    model_builder: Any = None

    # Private fields ---------------------------------------------------------
    _output_reader: Any = field(default=None, repr=False)

    # -- Convenience -------------------------------------------------------

    @property
    def engine_kind(self) -> str:
        # ModelBuilder is openswmm-only, so a session without a backend (in
        # the "building" state) is implicitly an openswmm session.
        if self.backend is None:
            return "openswmm"
        return self.backend.engine_kind

    # -- Backend delegation --------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the backend (``nodes``, ``links``, …)."""
        # ``__getattr__`` only fires when normal attribute lookup fails, so
        # the dataclass fields above continue to take precedence.
        backend = object.__getattribute__(self, "backend")
        if backend is None:
            raise AttributeError(
                f"'{type(self).__name__}' has no attribute '{name}' "
                "(session has no backend yet — still in 'building' state?)"
            )
        try:
            return getattr(backend, name)
        except AttributeError as exc:
            raise AttributeError(
                f"'{type(self).__name__}' has no attribute '{name}' "
                f"(backend kind = {backend.engine_kind!r})"
            ) from exc

    # -- OutputReader property -----------------------------------------------

    @property
    def output_reader(self) -> Any:
        return self._output_reader

    @output_reader.setter
    def output_reader(self, value: Any) -> None:
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
        if self.backend is None:
            # Building session that never finalized — nothing to tear down.
            self.state = "closed"
            return

        solver = self.backend.solver
        state = self.state

        if state in ("running", "initialized"):
            try:
                solver.end()
                self.state = "ended"
                state = "ended"
            except Exception:
                logger.debug("solver.end() failed during cleanup", exc_info=True)

        if state == "ended":
            try:
                solver.report()
            except Exception:
                logger.debug("solver.report() failed during cleanup", exc_info=True)

        if state in ("ended", "opened"):
            try:
                solver.close()
                self.state = "closed"
                state = "closed"
            except Exception:
                logger.debug("solver.close() failed during cleanup", exc_info=True)

        # Always attempt to destroy the underlying handle (no-op on legacy).
        try:
            solver.destroy()
        except Exception:
            logger.debug("solver.destroy() failed during cleanup", exc_info=True)

        self.state = "closed"


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


_VALID_ENGINES: tuple[str, ...] = get_args(EngineKind)


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
        engine: str = "openswmm",
    ) -> SimSession:
        """Create a new :class:`SimSession` and register it.

        The backend (and its solver) is created and its file paths are set,
        but the caller is responsible for opening / initializing / starting
        the engine.

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
        engine:
            Which backend to use: ``"openswmm"`` (default, full-feature) or
            ``"legacy"`` (EPA SWMM 5.x — basic query / forcing / mass balance
            only).

        Returns
        -------
        SimSession
            The newly created (but not yet opened) session.

        Raises
        ------
        ToolError
            If *max_sessions* has been reached, *session_id* already exists,
            or *engine* is unknown.
        """
        if engine not in _VALID_ENGINES:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown engine '{engine}'. "
                f"Valid choices: {', '.join(_VALID_ENGINES)}."
            )

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

            backend = make_backend(engine, str(inp), rpt_path, out_path)

            session_dir = self._working_dir / session_id
            session_dir.mkdir(parents=True, exist_ok=True)

            session = SimSession(
                backend=backend,
                state="created",
                working_dir=session_dir,
                inp_path=str(inp),
                rpt_path=rpt_path,
                out_path=out_path,
            )

            self._sessions[session_id] = session
            logger.info(
                "Session '%s' created (engine=%s, inp=%s)",
                session_id,
                engine,
                inp_path,
            )
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
            Each dict contains ``id``, ``state``, ``engine``, and
            ``working_dir``.
        """
        async with self._lock:
            items = list(self._sessions.items())

        return [
            {
                "id": sid,
                "state": session.state,
                "engine": session.engine_kind,
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
