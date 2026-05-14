"""Backend protocol definition.

Defines the interface every engine backend exposes to tools.  The shape
mirrors the new ``openswmm.engine`` API (indexed-getter / domain-collection
pattern) so existing tool code that uses ``session.nodes.get_depth(idx)``
keeps working unchanged on the openswmm backend, and the legacy backend
translates this shape onto the property-based ``LegacyNode.depth`` surface.
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
        Lifecycle-managing solver handle.  Has ``open / initialize / start /
        step / end / report / close / destroy`` plus ``get_start_time``,
        ``get_end_time``, ``get_current_time``, ``get_routing_step``,
        ``get_option``, ``elapsed``, ``state``.  For the legacy backend this
        is a thin adapter; for openswmm it is the raw new-engine ``Solver``.
    nodes / links / subcatchments / gages / pollutants:
        Domain-collection accessors with ``count()``, ``get_id(idx)``,
        ``get_index(name)``, plus typed ``get_*`` / ``set_*`` methods.
    mass_balance:
        Continuity-error queries returning fractions (0.001 = 0.1 %).
    forcing:
        Runtime forcing dispatcher.  On legacy a subset of methods is
        available; unsupported methods raise :class:`NotImplementedError`
        which the forcing tool translates to a clear ``NOT_SUPPORTED``
        ``ToolError``.
    hotstart:
        ``save(solver, path)`` and ``open(path)`` returning an object with
        ``apply(solver)``.  Legacy backend wraps ``solver.save_hotstart`` /
        ``solver.use_hotstart`` to match this shape.

    New-engine-only attributes (``model_builder``, ``editor``, ``statistics``,
    ``spatial``, ``tables``, ``controls``, ``inflows``, ``infrastructure``,
    ``quality``, ``output_reader``) raise :class:`AttributeError` on the
    legacy backend; tools that need them must guard with
    :func:`openswmm_mcp.dependencies.require_new_engine`.
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
