"""Legacy SWMM (EPA SWMM 5.x) backend.

Translates the indexed-getter / domain-collection shape that tools call
against onto the legacy property-based ``LegacyNode.depth`` API exposed by
:mod:`openswmm.legacy.engine`.

Translations that need to happen:

- Lifecycle: legacy ``Solver.initialize()`` does open + start in one call,
  whereas the new engine treats them separately.  This adapter routes
  ``open / initialize / start`` so that callers get the same three-phase
  shape regardless of which engine is underneath.
- Stepping: legacy ``step()`` returns ``(elapsed_days, current_datetime)``;
  this adapter caches those and returns ``bool`` (continue-or-not) to match
  the new engine.
- Mass balance: legacy reports continuity errors in **percent**; this
  adapter divides by 100 so callers always see the new-engine fraction
  (0.001 = 0.1 %).
- Hotstart: legacy ``Solver.use_hotstart`` / ``save_hotstart`` are exposed
  via a class that mirrors the new-engine ``HotStart.save / open / apply``
  three-method shape so the hotstart tools work uniformly.

Anything genuinely missing from legacy (ModelBuilder, ModelEditor, Controls,
Inflows, Infrastructure, Quality, Spatial, Tables, Statistics, OutputReader,
GeoPackage) is **not** implemented here.  The session's __getattr__ falls
through to the backend, AttributeError bubbles up, and the tool's
``require_new_engine(...)`` guard raises a clean ``NOT_SUPPORTED`` error
before the AttributeError path is ever hit.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

from openswmm.legacy.engine import (
    Solver as LegacySolver,
)
from openswmm.legacy.engine import (
    SWMMFlowUnits,
    SWMMLinkProperties,
    SWMMNodeProperties,
    SWMMObjects,
    SWMMRainGageProperties,
    SWMMSubcatchmentProperties,
    SWMMSystemProperties,
)

# ---------------------------------------------------------------------------
# Solver adapter
# ---------------------------------------------------------------------------


_FLOW_UNIT_NAMES = {
    SWMMFlowUnits.CFS: "CFS",
    SWMMFlowUnits.GPM: "GPM",
    SWMMFlowUnits.MGD: "MGD",
    SWMMFlowUnits.CMS: "CMS",
    SWMMFlowUnits.LPS: "LPS",
    SWMMFlowUnits.MLD: "MLD",
}


def _datetime_to_days(dt: datetime, epoch: datetime) -> float:
    """Convert a datetime to elapsed decimal days since *epoch*."""
    delta = dt - epoch
    return delta.total_seconds() / 86400.0


class _LegacySolverAdapter:
    """Wraps the legacy ``Solver`` to expose the new-engine ``Solver`` shape.

    Lifecycle method semantics (matching the new engine):

    - ``open()``         : parse the .inp file
    - ``initialize()``   : allocate arrays, set initial conditions
    - ``start()``        : begin the simulation
    - ``step()`` -> bool : advance one timestep; ``False`` when finished
    - ``end() / report() / close() / destroy()`` : standard teardown

    Internally the legacy solver collapses ``initialize()`` into ``start()``
    via :meth:`LegacySolver.initialize` (which calls open+start under the
    hood), so the adapter tracks which calls have already happened and
    routes accordingly.
    """

    def __init__(self, inp_path: str, rpt_path: str, out_path: str) -> None:
        self._solver = LegacySolver(inp_path, rpt_path, out_path)
        self._inp_path = inp_path
        self._rpt_path = rpt_path
        self._out_path = out_path
        self._opened = False
        self._initialized = False
        self._started = False
        self._last_elapsed_days = 0.0
        self._last_current_dt: datetime | None = None
        self._epoch: datetime | None = None
        self._end_dt: datetime | None = None

    # -- raw handle ----------------------------------------------------------

    @property
    def raw(self) -> LegacySolver:
        return self._solver

    @property
    def inp_path(self) -> str:
        return self._inp_path

    @property
    def out_path(self) -> str:
        return self._out_path

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        if self._opened:
            return
        self._solver.open()
        self._opened = True

    def initialize(self) -> None:
        # Legacy has no separate "initialize" between open and start; treat
        # this as a no-op and let start() begin the simulation.
        if not self._opened:
            self.open()
        self._initialized = True

    def start(self, save_results: bool = True) -> None:
        if self._started:
            return
        if not self._opened:
            self._solver.open()
            self._opened = True
        self._solver.start()
        self._started = True
        # Cache start/end datetimes so we can convert legacy datetime returns
        # to decimal-day floats compatible with new-engine signatures.
        self._epoch = self._solver.start_datetime
        self._end_dt = self._solver.end_datetime
        self._last_current_dt = self._epoch

    def step(self) -> bool:
        elapsed_days, current_dt = self._solver.step()
        self._last_elapsed_days = elapsed_days
        self._last_current_dt = current_dt
        # Legacy convention: step returns elapsed=0 when the simulation has
        # finished.  Mirror the new-engine "continue?" boolean.
        return elapsed_days > 0.0

    def end(self) -> None:
        self._solver.end()

    def report(self) -> None:
        self._solver.report()

    def close(self) -> None:
        self._solver.close()

    def destroy(self) -> None:
        # Legacy has no destroy() — close handles teardown.
        pass

    # -- timing --------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        """Elapsed simulation time in decimal days."""
        return self._last_elapsed_days

    @property
    def state(self) -> int:
        """Mirror the legacy SolverState integer code."""
        try:
            return int(self._solver.solver_state.value)
        except Exception:
            return -1

    def get_start_time(self) -> float:
        """Return start time as decimal days since the same epoch as ``get_current_time``."""
        if self._epoch is None:
            self._epoch = self._solver.start_datetime
        # By construction, the epoch *is* the start time -> 0.0 days.
        # We mirror the new engine which also uses start as 0 reference.
        return 0.0

    def get_end_time(self) -> float:
        """Return end time as decimal days since start."""
        if self._epoch is None:
            self._epoch = self._solver.start_datetime
        if self._end_dt is None:
            self._end_dt = self._solver.end_datetime
        return _datetime_to_days(self._end_dt, self._epoch)

    def get_current_time(self) -> float:
        """Return current simulation time as decimal days since start."""
        if self._epoch is None:
            self._epoch = self._solver.start_datetime
        current = self._last_current_dt or self._solver.current_datetime
        return _datetime_to_days(current, self._epoch)

    def get_routing_step(self) -> float:
        return float(self._solver.routing_step)

    def get_option(self, key: str) -> str:
        """Best-effort key/value lookup mirroring the new engine's get_option.

        Only a handful of keys map cleanly onto legacy ``SWMMSystemProperties``
        / introspectable Solver state.  Unknown keys raise ``KeyError`` so
        existing tool code that wraps the call in ``try/except`` falls back
        to ``"UNKNOWN"`` automatically.
        """
        upper = key.strip().upper()
        if upper == "FLOW_UNITS":
            code = int(
                self._solver.get_value(SWMMObjects.SYSTEM, SWMMSystemProperties.FLOW_UNITS, 0)
            )
            try:
                return _FLOW_UNIT_NAMES[SWMMFlowUnits(code)]
            except (KeyError, ValueError):
                return f"UNKNOWN({code})"
        # The legacy engine doesn't expose ROUTING_MODEL / SURCHARGE_METHOD /
        # DPS_* / event_count / steady_state_skip via the toolkit API.
        raise KeyError(f"Option '{key}' is not exposed by the legacy engine.")

    # ------------------------------------------------------------------------
    # v1-shape properties (alongside the v0 get_* methods above).
    #
    # Tools that have migrated to the v1 surface read these property names
    # directly (``solver.start_datetime``, ``solver.options[key]``, …).  The
    # underlying SWMM 5 toolkit only surfaces a small subset of options, so
    # ``options`` is read-only and raises ``KeyError`` on unsupported keys.
    # ------------------------------------------------------------------------

    @property
    def start_datetime(self) -> datetime:
        if self._epoch is None:
            self._epoch = self._solver.start_datetime
        return self._epoch

    @property
    def end_datetime(self) -> datetime:
        if self._end_dt is None:
            self._end_dt = self._solver.end_datetime
        return self._end_dt

    @property
    def current_datetime(self) -> datetime:
        if self._last_current_dt is not None:
            return self._last_current_dt
        return self._solver.current_datetime

    @property
    def routing_step(self) -> timedelta:
        return timedelta(seconds=float(self._solver.routing_step))

    @property
    def options(self) -> "_LegacyOptionsView":
        view = getattr(self, "_options_view", None)
        if view is None:
            view = _LegacyOptionsView(self)
            self._options_view = view
        return view


class _LegacyOptionsView:
    """Read-only v1-shape ``solver.options`` view for the legacy backend.

    Forwards ``[key]`` reads through :meth:`_LegacySolverAdapter.get_option`.
    Iteration yields the keys legacy actually supports; ``__contains__`` is
    a try/except over ``[key]``.  Writes are not supported — the legacy
    toolkit doesn't expose option setters via the C API.
    """

    _SUPPORTED_KEYS: tuple[str, ...] = ("FLOW_UNITS",)

    def __init__(self, adapter: "_LegacySolverAdapter") -> None:
        self._adapter = adapter

    def __getitem__(self, key: str) -> str:
        return self._adapter.get_option(key)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        try:
            self._adapter.get_option(key)
            return True
        except KeyError:
            return False

    def __iter__(self) -> Iterator[str]:
        return iter(self._SUPPORTED_KEYS)

    def __len__(self) -> int:
        return len(self._SUPPORTED_KEYS)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self._adapter.get_option(key)
        except KeyError:
            return default


# ---------------------------------------------------------------------------
# Element collection adapters
# ---------------------------------------------------------------------------


class _LegacyNode:
    """v1-shape proxy for a single legacy node.

    Exposes only the attributes the legacy engine can actually compute.
    Anything v1 surfaces that legacy can't provide (``.stats``,
    ``.storage``, ``.outfall``, ``.divider``, ``.quality()``,
    ``.set_quality_mass_flux()``, ``.depth_from_volume()``) raises
    :class:`AttributeError`; tools that hit those code paths must guard
    with :func:`openswmm_mcp.dependencies.require_new_engine`.
    """

    __slots__ = ("_collection", "_index")

    def __init__(self, collection: "_LegacyNodes", index: int) -> None:
        self._collection = collection
        self._index = index

    @property
    def id(self) -> str:
        return self._collection.get_id(self._index)

    @property
    def index(self) -> int:
        return self._index

    @property
    def type(self) -> int:
        return self._collection.get_type(self._index)

    @property
    def invert_elev(self) -> float:
        return self._collection.get_invert_elev(self._index)

    @property
    def max_depth(self) -> float:
        return self._collection.get_max_depth(self._index)

    @property
    def depth(self) -> float:
        return self._collection.get_depth(self._index)

    @property
    def head(self) -> float:
        return self._collection.get_head(self._index)

    @property
    def volume(self) -> float:
        return self._collection.get_volume(self._index)

    @property
    def lateral_inflow(self) -> float:
        return self._collection.get_lateral_inflow(self._index)

    @lateral_inflow.setter
    def lateral_inflow(self, value: float) -> None:
        self._collection.set_lateral_inflow(self._index, value)

    @property
    def overflow(self) -> float:
        return self._collection.get_overflow(self._index)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _LegacyNode):
            return NotImplemented
        return self._index == other._index and self._collection is other._collection

    def __hash__(self) -> int:
        return hash((id(self._collection), self._index))

    def __repr__(self) -> str:
        try:
            return f"<LegacyNode id={self.id!r} index={self._index}>"
        except Exception:
            return f"<LegacyNode index={self._index}>"


class _LegacyNodes:
    """Indexed-getter shim over the legacy ``Solver`` node API.

    Exposes both the v0 ``get_*(idx)`` methods (used by un-migrated tools)
    and the v1 container protocol (``len``, ``iter``, ``[key]``,
    ``in``) returning :class:`_LegacyNode` wrappers.
    """

    def __init__(self, solver: LegacySolver, backend: "LegacyBackend" | None = None) -> None:
        self._solver = solver
        self._backend = backend

    # -- v1 container protocol ----------------------------------------------

    def __len__(self) -> int:
        return self._solver.get_object_count(SWMMObjects.NODE)

    def __iter__(self) -> Any:
        for i in range(len(self)):
            yield _LegacyNode(self, i)

    def __getitem__(self, key: Any) -> _LegacyNode:
        if isinstance(key, int):
            if key < 0 or key >= len(self):
                raise IndexError(f"Node index {key} out of range.")
            return _LegacyNode(self, key)
        if isinstance(key, str):
            idx = self.get_index(key)
            if idx < 0:
                raise KeyError(f"Node id {key!r} not found.")
            return _LegacyNode(self, idx)
        raise TypeError(f"Node lookup expects int or str, got {type(key).__name__}.")

    def __contains__(self, key: object) -> bool:
        if isinstance(key, int):
            return 0 <= key < len(self)
        if isinstance(key, str):
            return self.get_index(key) >= 0
        return False

    # -- v0-named scalar accessors (implementation backing for _LegacyNode) --
    #
    # These look like the old v0 method-style API but are *not* a transition
    # shim — the v1 ``_LegacyNode`` wrapper class calls back into them as
    # the actual SWMM-5-toolkit dispatch path.  Removing any of them breaks
    # the corresponding wrapper property.

    def get_id(self, index: int) -> str:
        return self._solver.get_object_name(SWMMObjects.NODE, index)

    def get_index(self, node_id: str) -> int:
        try:
            return self._solver.get_object_index(SWMMObjects.NODE, node_id)
        except Exception:
            return -1

    def get_type(self, index: int) -> int:
        return int(self._solver.get_value(SWMMObjects.NODE, SWMMNodeProperties.TYPE, index))

    def get_invert_elev(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.NODE, SWMMNodeProperties.INVERT_ELEVATION, index)

    def get_max_depth(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.NODE, SWMMNodeProperties.MAX_DEPTH, index)

    def get_depth(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.NODE, SWMMNodeProperties.DEPTH, index)

    def get_head(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.NODE, SWMMNodeProperties.HYDRAULIC_HEAD, index)

    def get_volume(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.NODE, SWMMNodeProperties.VOLUME, index)

    def get_lateral_inflow(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.NODE, SWMMNodeProperties.LATERAL_INFLOW, index)

    def get_overflow(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.NODE, SWMMNodeProperties.FLOODING, index)

    def set_lateral_inflow(self, index: int, value: float) -> None:
        self._solver.set_value(SWMMObjects.NODE, SWMMNodeProperties.LATERAL_INFLOW, index, value)


class _LegacyLink:
    """v1-shape proxy for a single legacy link.

    Sub-views (``.pump``, ``.weir``, ``.orifice``, ``.outlet``, ``.stats``,
    ``.xsect``) and bulk quality / pump-stat methods are NOT exposed —
    callers must guard with :func:`require_new_engine`.
    """

    __slots__ = ("_collection", "_index")

    def __init__(self, collection: "_LegacyLinks", index: int) -> None:
        self._collection = collection
        self._index = index

    @property
    def id(self) -> str:
        return self._collection.get_id(self._index)

    @property
    def index(self) -> int:
        return self._index

    @property
    def type(self) -> int:
        return self._collection.get_type(self._index)

    @property
    def from_node(self) -> "_LegacyNode":
        # v1 returns a Node wrapper, not an int index.  Mirror that shape
        # by resolving through the backend's nodes collection.
        backend = self._collection._backend
        if backend is None:
            raise AttributeError("Link.from_node requires a backend-bound Links collection.")
        return backend.nodes[self._collection.get_from_node(self._index)]

    @property
    def to_node(self) -> "_LegacyNode":
        backend = self._collection._backend
        if backend is None:
            raise AttributeError("Link.to_node requires a backend-bound Links collection.")
        return backend.nodes[self._collection.get_to_node(self._index)]

    @property
    def length(self) -> float:
        return self._collection.get_length(self._index)

    @property
    def max_depth(self) -> float:
        return self._collection.get_max_depth(self._index)

    @property
    def flow(self) -> float:
        return self._collection.get_flow(self._index)

    @property
    def depth(self) -> float:
        return self._collection.get_depth(self._index)

    @property
    def velocity(self) -> float:
        return self._collection.get_velocity(self._index)

    @property
    def capacity(self) -> float:
        return self._collection.get_capacity(self._index)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _LegacyLink):
            return NotImplemented
        return self._index == other._index and self._collection is other._collection

    def __hash__(self) -> int:
        return hash((id(self._collection), self._index))

    def __repr__(self) -> str:
        try:
            return f"<LegacyLink id={self.id!r} index={self._index}>"
        except Exception:
            return f"<LegacyLink index={self._index}>"


class _LegacyLinks:
    """Indexed-getter shim over the legacy ``Solver`` link API.

    Exposes both v0 (``get_*(idx)``) and v1 container protocol returning
    :class:`_LegacyLink` wrappers.
    """

    def __init__(self, solver: LegacySolver, backend: "LegacyBackend" | None = None) -> None:
        self._solver = solver
        self._backend = backend

    # -- v1 container protocol ----------------------------------------------

    def __len__(self) -> int:
        return self._solver.get_object_count(SWMMObjects.LINK)

    def __iter__(self) -> Any:
        for i in range(len(self)):
            yield _LegacyLink(self, i)

    def __getitem__(self, key: Any) -> _LegacyLink:
        if isinstance(key, int):
            if key < 0 or key >= len(self):
                raise IndexError(f"Link index {key} out of range.")
            return _LegacyLink(self, key)
        if isinstance(key, str):
            idx = self.get_index(key)
            if idx < 0:
                raise KeyError(f"Link id {key!r} not found.")
            return _LegacyLink(self, idx)
        raise TypeError(f"Link lookup expects int or str, got {type(key).__name__}.")

    def __contains__(self, key: object) -> bool:
        if isinstance(key, int):
            return 0 <= key < len(self)
        if isinstance(key, str):
            return self.get_index(key) >= 0
        return False

    # -- v0-named scalar accessors (implementation backing for _LegacyLink) --

    def get_id(self, index: int) -> str:
        return self._solver.get_object_name(SWMMObjects.LINK, index)

    def get_index(self, link_id: str) -> int:
        try:
            return self._solver.get_object_index(SWMMObjects.LINK, link_id)
        except Exception:
            return -1

    def get_type(self, index: int) -> int:
        return int(self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.TYPE, index))

    def get_from_node(self, index: int) -> int:
        return int(self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.START_NODE, index))

    def get_to_node(self, index: int) -> int:
        return int(self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.END_NODE, index))

    def get_length(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.LENGTH, index)

    def get_roughness(self, index: int) -> float:
        # Legacy doesn't expose roughness via the toolkit get_value API on
        # the LINK object — roughness lives on the cross-section.  Return
        # NaN-ish None equivalent so the tool surfaces it as missing.
        raise NotImplementedError(
            "roughness is not exposed by the legacy toolkit; query the .inp file."
        )

    def get_max_depth(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.FULL_DEPTH, index)

    def get_flow(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.FLOW, index)

    def get_depth(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.DEPTH, index)

    def get_velocity(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.VELOCITY, index)

    def get_capacity(self, index: int) -> float:
        return self._solver.get_value(SWMMObjects.LINK, SWMMLinkProperties.CAPACITY, index)

    def set_setting(self, index: int, value: float) -> None:
        self._solver.set_value(SWMMObjects.LINK, SWMMLinkProperties.SETTING, index, value)


class _LegacySubcatchment:
    """v1-shape proxy for a single legacy subcatchment.

    Sub-views (``.stats``, ``.infiltration``, ``.coverage``) and pollutant
    methods are NOT exposed — callers must guard with
    :func:`require_new_engine`.
    """

    __slots__ = ("_collection", "_index")

    def __init__(self, collection: "_LegacySubcatchments", index: int) -> None:
        self._collection = collection
        self._index = index

    @property
    def id(self) -> str:
        return self._collection.get_id(self._index)

    @property
    def index(self) -> int:
        return self._index

    @property
    def area(self) -> float:
        return self._collection.get_area(self._index)

    @property
    def imperv_pct(self) -> float | None:
        return self._collection.get_imperv_pct(self._index)

    @property
    def slope(self) -> float:
        return self._collection.get_slope(self._index)

    @property
    def width(self) -> float:
        return self._collection.get_width(self._index)

    @property
    def rainfall(self) -> float:
        return self._collection.get_rainfall(self._index)

    @property
    def runoff(self) -> float:
        return self._collection.get_runoff(self._index)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _LegacySubcatchment):
            return NotImplemented
        return self._index == other._index and self._collection is other._collection

    def __hash__(self) -> int:
        return hash((id(self._collection), self._index))

    def __repr__(self) -> str:
        try:
            return f"<LegacySubcatchment id={self.id!r} index={self._index}>"
        except Exception:
            return f"<LegacySubcatchment index={self._index}>"


class _LegacySubcatchments:
    """Indexed-getter shim over the legacy ``Solver`` subcatchment API.

    Exposes both v0 (``get_*(idx)``) and v1 container protocol returning
    :class:`_LegacySubcatchment` wrappers.
    """

    def __init__(self, solver: LegacySolver, backend: "LegacyBackend" | None = None) -> None:
        self._solver = solver
        self._backend = backend

    # -- v1 container protocol ----------------------------------------------

    def __len__(self) -> int:
        return self._solver.get_object_count(SWMMObjects.SUBCATCHMENT)

    def __iter__(self) -> Any:
        for i in range(len(self)):
            yield _LegacySubcatchment(self, i)

    def __getitem__(self, key: Any) -> _LegacySubcatchment:
        if isinstance(key, int):
            if key < 0 or key >= len(self):
                raise IndexError(f"Subcatchment index {key} out of range.")
            return _LegacySubcatchment(self, key)
        if isinstance(key, str):
            idx = self.get_index(key)
            if idx < 0:
                raise KeyError(f"Subcatchment id {key!r} not found.")
            return _LegacySubcatchment(self, idx)
        raise TypeError(
            f"Subcatchment lookup expects int or str, got {type(key).__name__}."
        )

    def __contains__(self, key: object) -> bool:
        if isinstance(key, int):
            return 0 <= key < len(self)
        if isinstance(key, str):
            return self.get_index(key) >= 0
        return False

    # -- v0-named scalar accessors (implementation backing for _LegacySubcatchment) --

    def get_id(self, index: int) -> str:
        return self._solver.get_object_name(SWMMObjects.SUBCATCHMENT, index)

    def get_index(self, sc_id: str) -> int:
        try:
            return self._solver.get_object_index(SWMMObjects.SUBCATCHMENT, sc_id)
        except Exception:
            return -1

    def get_area(self, index: int) -> float:
        return self._solver.get_value(
            SWMMObjects.SUBCATCHMENT, SWMMSubcatchmentProperties.AREA, index
        )

    def get_imperv_pct(self, index: int) -> float | None:
        # FRACTION_IMPERVIOUS is in some legacy builds but not exposed via
        # SWMMSubcatchmentProperties uniformly — return None when absent so
        # the tool degrades cleanly.
        prop = getattr(SWMMSubcatchmentProperties, "FRACTION_IMPERVIOUS", None)
        if prop is None:
            return None
        try:
            return self._solver.get_value(SWMMObjects.SUBCATCHMENT, prop, index) * 100.0
        except Exception:
            return None

    def get_slope(self, index: int) -> float:
        return self._solver.get_value(
            SWMMObjects.SUBCATCHMENT, SWMMSubcatchmentProperties.SLOPE, index
        )

    def get_width(self, index: int) -> float:
        return self._solver.get_value(
            SWMMObjects.SUBCATCHMENT, SWMMSubcatchmentProperties.WIDTH, index
        )

    def get_rainfall(self, index: int) -> float:
        return self._solver.get_value(
            SWMMObjects.SUBCATCHMENT, SWMMSubcatchmentProperties.RAINFALL, index
        )

    def get_runoff(self, index: int) -> float:
        return self._solver.get_value(
            SWMMObjects.SUBCATCHMENT, SWMMSubcatchmentProperties.RUNOFF, index
        )

    def set_rainfall_override(self, index: int, value: float) -> None:
        self._solver.set_value(
            SWMMObjects.SUBCATCHMENT,
            SWMMSubcatchmentProperties.API_RAINFALL,
            index,
            value,
        )


_GAGE_DATA_SOURCE = {0: 0, 1: 1}  # legacy doesn't surface this distinctly
_GAGE_RAIN_TYPE = {0: 0, 1: 1, 2: 2}


class _LegacyGage:
    """v1-shape proxy for a single legacy rain gage."""

    __slots__ = ("_collection", "_index")

    def __init__(self, collection: "_LegacyGages", index: int) -> None:
        self._collection = collection
        self._index = index

    @property
    def id(self) -> str:
        return self._collection.get_id(self._index)

    @property
    def index(self) -> int:
        return self._index

    @property
    def rainfall(self) -> float:
        return self._collection.get_rainfall(self._index)

    @rainfall.setter
    def rainfall(self, value: float) -> None:
        self._collection.set_rainfall(self._index, value)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _LegacyGage):
            return NotImplemented
        return self._index == other._index and self._collection is other._collection

    def __hash__(self) -> int:
        return hash((id(self._collection), self._index))

    def __repr__(self) -> str:
        try:
            return f"<LegacyGage id={self.id!r} index={self._index}>"
        except Exception:
            return f"<LegacyGage index={self._index}>"


class _LegacyGages:
    """Indexed-getter shim over the legacy ``Solver`` rain-gage API.

    Exposes both v0 and v1 surfaces.
    """

    def __init__(self, solver: LegacySolver, backend: "LegacyBackend" | None = None) -> None:
        self._solver = solver
        self._backend = backend

    # -- v1 container protocol ----------------------------------------------

    def __len__(self) -> int:
        return self._solver.get_object_count(SWMMObjects.RAIN_GAGE)

    def __iter__(self) -> Any:
        for i in range(len(self)):
            yield _LegacyGage(self, i)

    def __getitem__(self, key: Any) -> _LegacyGage:
        if isinstance(key, int):
            if key < 0 or key >= len(self):
                raise IndexError(f"Gage index {key} out of range.")
            return _LegacyGage(self, key)
        if isinstance(key, str):
            idx = self.get_index(key)
            if idx < 0:
                raise KeyError(f"Gage id {key!r} not found.")
            return _LegacyGage(self, idx)
        raise TypeError(f"Gage lookup expects int or str, got {type(key).__name__}.")

    def __contains__(self, key: object) -> bool:
        if isinstance(key, int):
            return 0 <= key < len(self)
        if isinstance(key, str):
            return self.get_index(key) >= 0
        return False

    # -- v0-named scalar accessors (implementation backing for _LegacyGage) --

    def get_id(self, index: int) -> str:
        return self._solver.get_object_name(SWMMObjects.RAIN_GAGE, index)

    def get_index(self, gage_id: str) -> int:
        try:
            return self._solver.get_object_index(SWMMObjects.RAIN_GAGE, gage_id)
        except Exception:
            return -1

    def get_data_source(self, index: int) -> int:
        # Not exposed through the toolkit get_value path; surface 0 as a
        # stable fallback (the tool maps ints to "TIMESERIES"/"FILE").
        return 0

    def get_rain_type(self, index: int) -> int:
        return 0

    def get_rainfall(self, index: int) -> float:
        return self._solver.get_value(
            SWMMObjects.RAIN_GAGE, SWMMRainGageProperties.GAGE_RAINFALL, index
        )

    def set_rainfall(self, index: int, value: float) -> None:
        self._solver.set_value(
            SWMMObjects.RAIN_GAGE, SWMMRainGageProperties.GAGE_RAINFALL, index, value
        )


class _LegacyPollutant:
    """v1-shape proxy for a single legacy pollutant.

    Legacy exposes only ``id`` / ``index``; every other v1 attribute
    (``.units``, ``.kdecay``, ``.init_conc`` …) raises
    :class:`AttributeError`.
    """

    __slots__ = ("_collection", "_index")

    def __init__(self, collection: "_LegacyPollutants", index: int) -> None:
        self._collection = collection
        self._index = index

    @property
    def id(self) -> str:
        return self._collection.get_id(self._index)

    @property
    def index(self) -> int:
        return self._index

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, _LegacyPollutant):
            return NotImplemented
        return self._index == other._index and self._collection is other._collection

    def __hash__(self) -> int:
        return hash((id(self._collection), self._index))

    def __repr__(self) -> str:
        try:
            return f"<LegacyPollutant id={self.id!r} index={self._index}>"
        except Exception:
            return f"<LegacyPollutant index={self._index}>"


class _LegacyPollutants:
    """Pollutant accessor — count + name only, no per-element data getters in legacy.

    Exposes both v0 and v1 surfaces.
    """

    def __init__(self, solver: LegacySolver, backend: "LegacyBackend" | None = None) -> None:
        self._solver = solver
        self._backend = backend

    # -- v1 container protocol ----------------------------------------------

    def __len__(self) -> int:
        return self._solver.get_object_count(SWMMObjects.POLLUTANT)

    def __iter__(self) -> Any:
        for i in range(len(self)):
            yield _LegacyPollutant(self, i)

    def __getitem__(self, key: Any) -> _LegacyPollutant:
        if isinstance(key, int):
            if key < 0 or key >= len(self):
                raise IndexError(f"Pollutant index {key} out of range.")
            return _LegacyPollutant(self, key)
        if isinstance(key, str):
            idx = self.get_index(key)
            if idx < 0:
                raise KeyError(f"Pollutant id {key!r} not found.")
            return _LegacyPollutant(self, idx)
        raise TypeError(
            f"Pollutant lookup expects int or str, got {type(key).__name__}."
        )

    def __contains__(self, key: object) -> bool:
        if isinstance(key, int):
            return 0 <= key < len(self)
        if isinstance(key, str):
            return self.get_index(key) >= 0
        return False

    def get_index(self, pollutant_id: str) -> int:
        try:
            return self._solver.get_object_index(SWMMObjects.POLLUTANT, pollutant_id)
        except Exception:
            return -1

    # -- v0-named scalar accessors (implementation backing for _LegacyPollutant) --

    def get_id(self, index: int) -> str:
        return self._solver.get_object_name(SWMMObjects.POLLUTANT, index)


# ---------------------------------------------------------------------------
# Mass balance, forcing, hotstart adapters
# ---------------------------------------------------------------------------


class _LegacyMassBalance:
    """Continuity-error retrieval that matches the new-engine fraction units.

    Legacy reports errors in **percent**; the new engine in **fraction**
    (0.001 == 0.1 %).  This adapter divides by 100 so callers always see
    fractions regardless of which engine is underneath.
    """

    def __init__(self, solver: LegacySolver) -> None:
        self._solver = solver

    @property
    def runoff_continuity_error(self) -> float:
        runoff_pct, _flow_pct, _qual_pct = self._solver.get_mass_balance_error()
        return runoff_pct / 100.0

    @property
    def routing_continuity_error(self) -> float:
        _runoff_pct, flow_pct, _qual_pct = self._solver.get_mass_balance_error()
        return flow_pct / 100.0

    def quality_continuity_error(self, pollutant: Any = 0) -> float:
        """Per-pollutant continuity-error query (v1-shape).

        Legacy reports one aggregate quality error, so the ``pollutant``
        argument is accepted for signature parity but ignored.
        """
        _runoff, _flow, qual_pct = self._solver.get_mass_balance_error()
        return qual_pct / 100.0


class _LegacyForcing:
    """Subset of new-engine ``Forcing`` methods that legacy can implement.

    Only the most commonly used overrides translate cleanly:

    - ``node_lat_inflow(idx, value, mode, target)`` -> set node lateral inflow
    - ``link_setting(idx, value, mode, target)``   -> set link control setting
    - ``subcatch_rainfall(idx, value, mode, target)`` -> set API rainfall
    - ``gage_rainfall(idx, value, mode, target)`` -> set gage rainfall
    - ``clear_all()`` is a no-op (legacy doesn't track an override registry —
      values reset on the next step automatically).

    Methods that have no legacy equivalent (``node_head_boundary``,
    ``node_quality``, ``link_flow``, ``subcatch_evap``, ``clear`` for a
    specific element) raise :class:`NotImplementedError`, which the
    forcing tool maps to a ``NOT_SUPPORTED`` ``ToolError``.

    The ``mode`` and ``target`` arguments are accepted for signature
    compatibility but ignored on legacy: every override behaves as
    ``REPLACE`` and is reset on the next step (i.e. equivalent to
    ``persist=False``).
    """

    def __init__(
        self,
        nodes: _LegacyNodes,
        links: _LegacyLinks,
        subcatchments: _LegacySubcatchments,
        gages: _LegacyGages,
    ) -> None:
        self._nodes = nodes
        self._links = links
        self._subcatchments = subcatchments
        self._gages = gages

    @staticmethod
    def _resolve_index(accessor: Any, target: Any) -> int:
        if isinstance(target, int):
            return target
        return accessor.get_index(target)

    def node_lat_inflow(self, target: Any, value: float, mode: int = 0, persist: int = 0) -> None:
        idx = self._resolve_index(self._nodes, target)
        self._nodes.set_lateral_inflow(idx, value)

    def link_setting(self, target: Any, value: float, mode: int = 0, persist: int = 0) -> None:
        idx = self._resolve_index(self._links, target)
        self._links.set_setting(idx, value)

    def subcatchment_rainfall(
        self, target: Any, value: float, mode: int = 0, persist: int = 0
    ) -> None:
        idx = self._resolve_index(self._subcatchments, target)
        self._subcatchments.set_rainfall_override(idx, value)

    def gage_rainfall(self, target: Any, value: float, mode: int = 0, persist: int = 0) -> None:
        idx = self._resolve_index(self._gages, target)
        self._gages.set_rainfall(idx, value)

    def node_head_boundary(self, *args: Any, **kw: Any) -> None:
        raise NotImplementedError(
            "Forcing 'node head boundary' is not supported by the legacy engine."
        )

    def node_quality(self, *args: Any, **kw: Any) -> None:
        raise NotImplementedError("Forcing 'node quality' is not supported by the legacy engine.")

    def link_flow(self, *args: Any, **kw: Any) -> None:
        raise NotImplementedError(
            "Forcing 'link flow override' is not supported by the legacy engine."
        )

    def subcatchment_evap(self, *args: Any, **kw: Any) -> None:
        raise NotImplementedError(
            "Forcing 'subcatchment evaporation override' is not supported by the legacy engine."
        )

    def clear_all(self) -> None:
        # Legacy resets API/forcing values on each step automatically; nothing
        # to do here.
        pass

    def clear(self, *args: Any, **kw: Any) -> None:
        raise NotImplementedError(
            "Per-element forcing clear is not supported by the legacy engine; "
            "values reset automatically on each timestep."
        )


class _LegacyHotstartHandle:
    """Object returned by :meth:`_LegacyHotstart.open` — has ``apply``."""

    def __init__(self, path: str) -> None:
        self._path = path

    def apply(self, solver: _LegacySolverAdapter) -> None:
        solver.raw.use_hotstart(self._path)


class _LegacyHotstart:
    """Mirrors the new-engine ``HotStart`` save/open/apply triplet."""

    def __init__(self, _solver: LegacySolver) -> None:
        # Constructor signature matches the new engine (which takes a Solver).
        # The actual call goes through whichever solver the tool passes to
        # save() / apply(), so we don't need to retain it here.
        pass

    def save(self, solver: _LegacySolverAdapter, path: str) -> None:
        solver.raw.save_hotstart(path)

    def open(self, path: str) -> _LegacyHotstartHandle:
        return _LegacyHotstartHandle(path)


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class LegacyBackend:
    """Backend that wraps :mod:`openswmm.legacy.engine`."""

    engine_kind = "legacy"

    def __init__(self, inp_path: str, rpt_path: str, out_path: str) -> None:
        self._adapter = _LegacySolverAdapter(inp_path, rpt_path, out_path)
        self._cache: dict[str, Any] = {}

    @property
    def solver(self) -> _LegacySolverAdapter:
        return self._adapter

    def __getattr__(self, name: str) -> Any:
        # Only invoked when normal attribute lookup fails, so this never
        # interferes with `solver` / `_adapter` / `_cache`.
        cache = object.__getattribute__(self, "_cache")
        if name in cache:
            return cache[name]

        adapter = object.__getattribute__(self, "_adapter")
        raw = adapter.raw

        if name == "nodes":
            cache[name] = _LegacyNodes(raw, self)
        elif name == "links":
            cache[name] = _LegacyLinks(raw, self)
        elif name == "subcatchments":
            cache[name] = _LegacySubcatchments(raw, self)
        elif name == "gages":
            cache[name] = _LegacyGages(raw, self)
        elif name == "pollutants":
            cache[name] = _LegacyPollutants(raw, self)
        elif name == "mass_balance":
            cache[name] = _LegacyMassBalance(raw)
        elif name == "forcing":
            cache[name] = _LegacyForcing(self.nodes, self.links, self.subcatchments, self.gages)
        elif name == "hotstart":
            cache[name] = _LegacyHotstart(raw)
        else:
            raise AttributeError(
                f"LegacyBackend has no attribute '{name}'. This feature requires engine='openswmm'."
            )
        return cache[name]
