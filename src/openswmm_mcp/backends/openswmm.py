"""OpenSWMM (new engine) backend.

Wraps :mod:`openswmm.engine` with one-time lazy construction of each domain
accessor.  Behaviour matches what :class:`~openswmm_mcp.session.SimSession`
did before the backend abstraction was introduced.
"""

from __future__ import annotations

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
    Nodes,
    Pollutants,
    Quality,
    Solver,
    Spatial,
    Statistics,
    Subcatchments,
    Tables,
)

_DOMAIN_FACTORIES: dict[str, type] = {
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


class OpenSwmmBackend:
    """Backend that delegates straight to the new ``openswmm.engine`` API."""

    engine_kind = "openswmm"

    def __init__(self, inp_path: str, rpt_path: str, out_path: str) -> None:
        self._solver = Solver(inp_path, rpt_path, out_path)
        self._cache: dict[str, Any] = {}

    @classmethod
    def from_solver(cls, solver: Solver) -> OpenSwmmBackend:
        """Wrap an already-constructed :class:`Solver` (e.g. from ModelBuilder.finalize)."""
        instance = cls.__new__(cls)
        instance._solver = solver
        instance._cache = {}
        return instance

    @property
    def solver(self) -> Solver:
        return self._solver

    def __getattr__(self, name: str) -> Any:
        # Called only when normal attribute lookup fails, so this fires for
        # the lazy domain accessors below but not for ``solver`` / ``_cache``.
        factory = _DOMAIN_FACTORIES.get(name)
        if factory is None:
            raise AttributeError(
                f"OpenSwmmBackend has no attribute '{name}'. "
                f"(Tool may need to use require_new_engine guard.)"
            )
        cache = object.__getattribute__(self, "_cache")
        if name not in cache:
            cache[name] = factory(object.__getattribute__(self, "_solver"))
        return cache[name]
