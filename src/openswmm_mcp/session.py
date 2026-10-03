"""Session management for the OpenSWMM MCP server.

A :class:`SimSession` owns one ``openswmm.engine.Solver``.
:class:`SessionManager` is the concurrent-safe registry of sessions. Every
engine call for a session runs under that session's lock, in a worker thread.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from openswmm.engine import EngineState, OutputReader, Solver

from openswmm_mcp.errors import ErrorCode, ToolError, engine_error

logger = logging.getLogger(__name__)
T = TypeVar("T")
_SESSION_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}")


@dataclass
class SimSession:
    """One model: its solver, files and cached output readers."""

    session_id: str
    working_dir: Path
    solver: Solver | None = None
    inp_path: str = ""
    rpt_path: str = ""
    out_path: str = ""
    # Set by structural edits (add/delete/convert); ``run`` then writes and reopens
    # the model so the engine rebuilds its derived data before initializing.
    structure_edited: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    readers: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def state(self) -> str:
        if self.solver is None:
            return "CLOSED"
        try:
            return EngineState(int(self.solver.state)).name
        except Exception:
            return "CLOSED"

    def require_solver(self) -> Solver:
        if self.solver is None:
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{self.session_id}' has no "
                "open model; use session(action='reset') or open_model."
            )
        return self.solver

    async def call(self, fn: Callable[..., T], *args: Any, context: str = "", **kw: Any) -> T:
        """Run *fn* in a worker thread under the session lock, translating engine errors.

        A cancelled request still waits for the worker to finish before the lock
        is released, so no other call can touch the engine while it is busy.
        """
        async with self.lock:
            work = asyncio.ensure_future(asyncio.to_thread(fn, *args, **kw))
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                await asyncio.wait({work})
                raise
            except ToolError:
                raise
            except Exception as exc:
                raise engine_error(exc, context) from exc

    def output_reader(self, path: str | None = None) -> OutputReader:
        """A cached reader of this session's (or another) binary output file."""
        key = path or self.out_path
        reader = self.readers.get(key)
        if reader is None:
            reader = self.readers[key] = OutputReader(key)
        return reader

    def close_readers(self) -> None:
        for reader in self.readers.values():
            try:
                reader.close()
            except Exception:
                logger.debug("reader close failed", exc_info=True)
        self.readers.clear()

    def cleanup(self) -> None:
        """Tear the solver down from whatever state it is in."""
        self.close_readers()
        solver, self.solver = self.solver, None
        if solver is None:
            return
        for step in ("end", "close", "destroy"):
            try:
                state = int(solver.state)
            except Exception:
                break
            if step == "end" and state not in (EngineState.STARTED, EngineState.RUNNING):
                continue
            if step == "close" and state == EngineState.CLOSED:
                continue
            try:
                getattr(solver, step)()
            except Exception:
                logger.debug("solver.%s() failed during cleanup", step, exc_info=True)


class SessionManager:
    """Thread/async-safe registry of :class:`SimSession` instances."""

    def __init__(self, max_sessions: int = 5, working_dir: str = "./") -> None:
        self._sessions: dict[str, SimSession] = {}
        self._lock = asyncio.Lock()
        self._max_sessions = max_sessions
        self.working_dir = Path(working_dir).expanduser().resolve()

    async def add(self, session: SimSession) -> SimSession:
        async with self._lock:
            if session.session_id in self._sessions:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Session '{session.session_id}' "
                    "already exists; close it or choose another session_id."
                )
            if len(self._sessions) >= self._max_sessions:
                raise ToolError(
                    f"[{ErrorCode.MAX_SESSIONS_REACHED}] Maximum number of sessions "
                    f"({self._max_sessions}) reached. Close one first."
                )
            self._sessions[session.session_id] = session
        return session

    def session_dir(self, session_id: str) -> Path:
        if not _SESSION_ID.fullmatch(session_id):
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] session_id may use letters, digits, '_', '-' "
                f"and '.' (not starting with '.'); got '{session_id}'."
            )
        path = self.working_dir / session_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def exists(self, session_id: str) -> bool:
        async with self._lock:
            return session_id in self._sessions

    async def get(self, session_id: str) -> SimSession:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            known = ", ".join(self._sessions) or "none"
            raise ToolError(
                f"[{ErrorCode.SESSION_NOT_FOUND}] No session '{session_id}' "
                f"(open sessions: {known}). Use open_model first."
            )
        return session

    async def remove(self, session_id: str) -> SimSession:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            raise ToolError(f"[{ErrorCode.SESSION_NOT_FOUND}] No session '{session_id}'.")
        return session

    async def close(self, session_id: str) -> None:
        session = await self.remove(session_id)
        async with session.lock:
            await asyncio.to_thread(session.cleanup)

    async def sessions(self) -> list[SimSession]:
        async with self._lock:
            return list(self._sessions.values())

    async def cleanup_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.cleanup()
            except Exception:
                logger.warning("Failed to clean up session '%s'", session.session_id, exc_info=True)
