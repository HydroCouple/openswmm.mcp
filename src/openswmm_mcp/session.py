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
# SessionMeta — lazy static-metadata cache
# ---------------------------------------------------------------------------


class SessionMeta:
    """Read-only snapshot of static model metadata shared across tool calls.

    Populated lazily from the backend the first time a field is read.
    Topology (node / link / subcatchment ids and counts) is fixed once the
    model is parsed, so we cache it once per session and avoid the N C-ABI
    crossings that scalar ``get_id`` loops incur.

    All fields use Python lists rather than NumPy arrays so the cache is
    JSON-serialisable for diagnostic and debugging purposes.  Tool authors
    needing numeric arrays should call the corresponding ``*_bulk`` accessor
    directly — those return fresh NumPy arrays per call (no shared state).

    Attributes
    ----------
    n_nodes / n_links / n_subcatchments / n_pollutants / n_gages:
        Element counts (cached on first access; remain valid until the
        session's topology is mutated and ``invalidate_meta()`` is called).
    node_ids / link_ids / subcatch_ids / pollutant_ids / gage_ids:
        Lists of element IDs in canonical (index) order.  Each list is
        populated from the corresponding ``*.get_ids_bulk()`` accessor
        when available (added in Phase 3), with a transparent fallback to
        per-element ``get_id`` when running against an older binding
        (notably the legacy backend, which exposes only the scalar form).
    """

    __slots__ = (
        "_session",
        "_n_nodes", "_n_links", "_n_subcatchments", "_n_pollutants", "_n_gages",
        "_node_ids", "_link_ids", "_subcatch_ids",
        "_pollutant_ids", "_gage_ids",
    )

    def __init__(self, session: "SimSession") -> None:
        # Keep a back-reference for lazy fetch; do NOT materialise anything
        # here so that an inexpensive ``session.meta`` access in a tool
        # that never reads from it costs ~zero.
        self._session = session
        self._n_nodes: int | None = None
        self._n_links: int | None = None
        self._n_subcatchments: int | None = None
        self._n_pollutants: int | None = None
        self._n_gages: int | None = None
        self._node_ids: list[str] | None = None
        self._link_ids: list[str] | None = None
        self._subcatch_ids: list[str] | None = None
        self._pollutant_ids: list[str] | None = None
        self._gage_ids: list[str] | None = None

    # -- counts --------------------------------------------------------------

    @property
    def n_nodes(self) -> int:
        if self._n_nodes is None:
            self._n_nodes = len(self._session.nodes)
        return self._n_nodes

    @property
    def n_links(self) -> int:
        if self._n_links is None:
            self._n_links = len(self._session.links)
        return self._n_links

    @property
    def n_subcatchments(self) -> int:
        if self._n_subcatchments is None:
            self._n_subcatchments = len(self._session.subcatchments)
        return self._n_subcatchments

    @property
    def n_pollutants(self) -> int:
        if self._n_pollutants is None:
            pollutants = getattr(self._session, "pollutants", None)
            self._n_pollutants = len(pollutants) if pollutants is not None else 0
        return self._n_pollutants

    @property
    def n_gages(self) -> int:
        if self._n_gages is None:
            gages = getattr(self._session, "gages", None)
            self._n_gages = len(gages) if gages is not None else 0
        return self._n_gages

    # -- id arrays -----------------------------------------------------------

    def _fetch_ids(
        self, proxy: Any, count: int, bulk_attr: str = "get_ids_bulk",
    ) -> list[str]:
        """Single-pass id fetch.

        Tries in order:

        1. The v1 ``ids`` property (np.ndarray of object dtype).  One C
           call, returned as a NumPy array — convert to a Python list.
        2. The Phase 3 bulk getter (``get_ids_bulk``) when available.
        3. Per-element ``get_id`` for older bindings (e.g. legacy backend
           shim).  Same cost as the old scalar loop, so this is at worst
           neutral.
        """
        ids_attr = getattr(proxy, "ids", None)
        if ids_attr is not None:
            return [str(x) for x in ids_attr]
        bulk = getattr(proxy, bulk_attr, None)
        if bulk is not None:
            return list(bulk())
        return [proxy.get_id(i) for i in range(count)]

    @property
    def node_ids(self) -> list[str]:
        if self._node_ids is None:
            self._node_ids = self._fetch_ids(self._session.nodes, self.n_nodes)
        return self._node_ids

    @property
    def link_ids(self) -> list[str]:
        if self._link_ids is None:
            self._link_ids = self._fetch_ids(self._session.links, self.n_links)
        return self._link_ids

    @property
    def subcatch_ids(self) -> list[str]:
        if self._subcatch_ids is None:
            self._subcatch_ids = self._fetch_ids(
                self._session.subcatchments, self.n_subcatchments)
        return self._subcatch_ids

    @property
    def pollutant_ids(self) -> list[str]:
        if self._pollutant_ids is None:
            pollutants = getattr(self._session, "pollutants", None)
            if pollutants is None:
                self._pollutant_ids = []
            else:
                # pollutants binding may not have get_ids_bulk yet — fall back.
                self._pollutant_ids = self._fetch_ids(
                    pollutants, self.n_pollutants)
        return self._pollutant_ids

    @property
    def gage_ids(self) -> list[str]:
        if self._gage_ids is None:
            gages = getattr(self._session, "gages", None)
            if gages is None:
                self._gage_ids = []
            else:
                self._gage_ids = self._fetch_ids(gages, self.n_gages)
        return self._gage_ids


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

    # Lazy static-metadata cache.  Populated on first access from the
    # underlying backend's bulk accessors (see ``SessionMeta`` below).  Topology
    # is fixed at parse time, so once cached these never change for the
    # lifetime of the session — caching them eliminates the per-tool-call cost
    # of looping ``get_id`` / ``count`` through the C ABI N times.
    _meta: Any = field(default=None, repr=False)

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

    # -- Static-metadata cache -----------------------------------------------
    #
    # Topology and pollutant lists are fixed once the model is parsed, so
    # every tool that loops them through the C ABI is paying redundant
    # crossings.  ``session.meta`` provides a single shared view that any
    # tool can read; the first access populates the cache via the new
    # Phase 3 bulk getters.

    @property
    def meta(self) -> "SessionMeta":
        """Lazy cache of static (topology + pollutant) metadata.

        Computed on first access from the backend's bulk getters.  Safe to
        call from any state where the backend is open; raises an
        :class:`AttributeError` (via ``__getattr__``) on a backend-less
        building session.

        Returns the same :class:`SessionMeta` instance on subsequent calls,
        so cached id-lists and counts cost nothing after the first read.
        """
        if self._meta is None:
            self._meta = SessionMeta(self)
        return self._meta

    def invalidate_meta(self) -> None:
        """Drop the cached metadata.

        Should be called when model topology changes mid-session (e.g.
        after an ``editing.delete_object`` call).  Most callers will never
        need this — topology is fixed for the lifetime of the simulation
        in normal usage.
        """
        self._meta = None

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
