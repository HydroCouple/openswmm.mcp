"""Backend protocol definition.

Defines the interface every engine backend exposes to tools.  Both
backends present the v1 Pythonic surface of :mod:`openswmm.engine`:

- Container protocol on every collection: ``len(backend.nodes)``,
  ``backend.nodes[id_or_idx]``, ``for n in backend.nodes``,
  ``id_or_idx in backend.nodes``.
- Item access returns wrapper objects exposing property-style attribute
  reads/writes: ``backend.nodes["J1"].depth``,
  ``backend.nodes["J1"].lateral_inflow = 0.5``.
- Sub-views per type: ``node.stats``, ``node.storage``, ``node.outfall``,
  ``link.pump``, ``link.weir``, ``subcatchment.infiltration``, etc.
  Available only on the openswmm backend; the legacy backend raises
  :class:`AttributeError` on these.

The legacy adapter has scalar accessor methods (``get_depth(idx)``,
``get_type(idx)``, …) on each collection — these are **not** a public
transition shim, they are the SWMM-5-toolkit dispatch path that the
``_LegacyNode`` / ``_LegacyLink`` / etc. wrappers call into.  Treat them
as private implementation; the public surface is the v1 shape described
above.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Backend(Protocol):
    """Engine-agnostic surface that tools call against.

    Attributes
    ----------
    engine_kind:
        ``"openswmm"`` or ``"legacy"``.  Tools that require the new engine
        check this via :func:`openswmm_mcp.dependencies.require_new_engine`.
    solver:
        Lifecycle-managing solver handle.  Has ``open / initialize / start
        / step / end / report / close / destroy``, plus ``elapsed``,
        ``state``, ``start_datetime``, ``end_datetime``,
        ``current_datetime``, and (openswmm only) ``steps()`` /
        ``stride(n)`` / ``until(target)``.
    nodes / links / subcatchments / gages / pollutants:
        v1-shape collections: ``len(...)``, ``[key]`` returning wrappers
        with property-style attribute access, iteration, and ``in``.
    mass_balance:
        Continuity-error queries returning fractions (0.001 = 0.1 %),
        accessible via property-style ``mass_balance.runoff_continuity_error``.
    forcing:
        Runtime forcing dispatcher.  On legacy a subset of methods is
        available; unsupported methods raise :class:`NotImplementedError`
        which the forcing tool translates to a clear ``NOT_SUPPORTED``
        ``ToolError``.
    hotstart:
        ``save(solver, path)`` and ``open(path)`` returning an object with
        ``apply(solver)``.  Legacy backend wraps ``solver.save_hotstart`` /
        ``solver.use_hotstart`` to match this shape.

    New-engine-only attributes (``editor``, ``statistics``, ``spatial``,
    ``tables``, ``patterns``, ``controls``, ``inflows``,
    ``infrastructure``, ``quality``, ``save_schedule``) raise
    :class:`AttributeError` on the legacy backend; tools that need them
    must guard with :func:`openswmm_mcp.dependencies.require_new_engine`.
    """

    engine_kind: str

    @property
    def solver(self) -> Any: ...
    @property
    def nodes(self) -> Any: ...
    @property
    def links(self) -> Any: ...
    @property
    def subcatchments(self) -> Any: ...
    @property
    def gages(self) -> Any: ...
    @property
    def pollutants(self) -> Any: ...
    @property
    def mass_balance(self) -> Any: ...
    @property
    def forcing(self) -> Any: ...
    @property
    def hotstart(self) -> Any: ...
