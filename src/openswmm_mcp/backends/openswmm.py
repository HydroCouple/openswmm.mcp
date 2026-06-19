"""OpenSWMM (new engine) backend.

The v1 :class:`~openswmm.engine.Solver` already exposes every domain
collection as a typed property (``solver.nodes``, ``solver.links``,
``solver.subcatchments``, ``solver.gages``, ``solver.pollutants``,
``solver.tables``, ``solver.patterns``, ``solver.inflows``,
``solver.controls``, ``solver.forcing``, ``solver.infrastructure``,
``solver.spatial``, ``solver.quality``, ``solver.statistics``,
``solver.mass_balance``, ``solver.editor``, ``solver.save_schedule``).

This backend is therefore a thin pass-through: every attribute that is
not part of the backend's own state (``_solver``, ``engine_kind``)
delegates straight to the underlying :class:`Solver`.

Tools call ``session.<domain>`` (which resolves through
:class:`~openswmm_mcp.session.SimSession.__getattr__` to this backend's
``__getattr__``, which in turn forwards to ``solver.<domain>``).
"""

from __future__ import annotations

from typing import Any

from openswmm.engine import HotStart, Solver


class _OpenSwmmHotstart:
    """Lightweight v1-shape wrapper mirroring ``_LegacyHotstart``.

    The v1 engine doesn't expose a ``solver.hotstart`` attribute — saves
    go through :meth:`HotStart.save_from` (static) and loads through
    :meth:`HotStart.open`.  Tools written against the legacy backend's
    ``session.hotstart.save(solver, path)`` / ``session.hotstart.open(path)``
    shape keep working uniformly across backends by going through this
    wrapper instead.
    """

    def save(self, solver: Solver, path: str) -> None:
        HotStart.save_from(solver, path)

    def open(self, path: str) -> HotStart:
        return HotStart.open(path)


class OpenSwmmBackend:
    """Backend that delegates straight to the new ``openswmm.engine`` Solver."""

    engine_kind = "openswmm"

    def __init__(self, inp_path: str, rpt_path: str, out_path: str) -> None:
        self._solver = Solver(inp_path, rpt_path, out_path)
        self._hotstart_wrapper = _OpenSwmmHotstart()

    @classmethod
    def from_solver(cls, solver: Solver) -> OpenSwmmBackend:
        """Wrap an already-constructed :class:`Solver` (e.g. from ModelBuilder.to_solver)."""
        instance = cls.__new__(cls)
        instance._solver = solver
        instance._hotstart_wrapper = _OpenSwmmHotstart()
        return instance

    @property
    def solver(self) -> Solver:
        return self._solver

    @property
    def hotstart(self) -> _OpenSwmmHotstart:
        """v1-shape hot-start wrapper (``.save(solver, path)`` /
        ``.open(path) -> HotStart``).

        Bypasses the ``__getattr__`` pass-through because the v1 Solver
        has no ``hotstart`` attribute of its own — this is the wrapper
        defined locally to keep the tool layer uniform across backends.
        """
        return self._hotstart_wrapper

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only consulted when normal attribute lookup fails,
        # so ``solver`` / ``_solver`` / ``engine_kind`` / ``hotstart``
        # resolve before we get here.  Anything else is forwarded to the
        # v1 Solver, which exposes every domain collection (nodes, links,
        # subcatchments, gages, pollutants, tables, patterns, inflows,
        # controls, forcing, infrastructure, spatial, quality, statistics,
        # mass_balance, editor, save_schedule) as a typed property.
        solver = object.__getattribute__(self, "_solver")
        try:
            return getattr(solver, name)
        except AttributeError as exc:
            raise AttributeError(
                f"OpenSwmmBackend has no attribute '{name}' "
                f"(not exposed by openswmm.engine.Solver)."
            ) from exc
