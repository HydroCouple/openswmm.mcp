"""Comprehensive mock classes replicating the ``openswmm.engine`` API surface.

These mocks are used in unit tests so that no compiled C extensions (or the
real ``openswmm`` package) are required.  Every mock records calls in a
``_calls`` list for later assertion in tests.  Return values use sensible
defaults that match a small 12-node / 11-link / 8-subcatchment reference
network.

MockSolver: 100-step simulation, Julian times ~45000.0
MockNodes: 12 nodes J1-J11 + O1 (last is OUTFALL)
MockLinks: 11 links C1-C11 (all CONDUIT)
MockSubcatchments: 8 subcatchments S1-S8
MockGages: 2 gages RG1, RG2
MockMassBalance: -0.01 runoff error, -0.02 routing error
MockStatistics: nodes 0-2 have flooding (vol_flooded=100)
"""

from __future__ import annotations

from enum import IntEnum
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Lightweight enum stubs that mirror openswmm.engine enums used in analysis.py
# ---------------------------------------------------------------------------


class OutNodeVar(IntEnum):
    DEPTH = 0
    HEAD = 1
    VOLUME = 2
    LATERAL_INFLOW = 3
    TOTAL_INFLOW = 4
    OVERFLOW = 5
    POLLUT_BASE = 6


class OutLinkVar(IntEnum):
    FLOW = 0
    DEPTH = 1
    VELOCITY = 2
    VOLUME = 3
    CAPACITY = 4
    POLLUT_BASE = 5


class OutSubcatchVar(IntEnum):
    RAINFALL = 0
    SNOW_DEPTH = 1
    EVAP = 2
    INFIL = 3
    RUNOFF = 4
    GW_FLOW = 5
    GW_ELEV = 6
    SOIL_MOIST = 7


class OutSystemVar(IntEnum):
    TEMPERATURE = 0
    RAINFALL = 1
    SNOW_DEPTH = 2
    EVAP = 3
    INFIL = 4
    RUNOFF = 5
    DW_INFLOW = 6
    GW_INFLOW = 7
    LAT_INFLOW = 8
    FLOODING = 9
    OUTFLOW = 10
    STORAGE = 11
    EVAP_TOTAL = 12
    PET = 13


class RunoffTotal(IntEnum):
    RAINFALL = 0
    EVAP = 1
    INFIL = 2
    RUNOFF = 3
    DRAINS = 4
    INITIAL = 5
    FINAL = 6


class RoutingTotal(IntEnum):
    DW_INFLOW = 0
    GW_INFLOW = 1
    II_INFLOW = 2
    EXT_INFLOW = 3
    FLOODING = 4
    OUTFLOW = 5
    EVAP = 6
    SEEPAGE = 7
    REACTED = 8
    INITIAL = 9
    FINAL = 10


# ---------------------------------------------------------------------------
# Shared reference-network element IDs
# ---------------------------------------------------------------------------

_NODE_IDS = ["J1", "J2", "J3", "J4", "J5", "J6", "J7", "J8", "J9", "J10", "J11", "O1"]
_NODE_TYPES = [0] * 11 + [1]  # J1..J11 = JUNCTION, O1 = OUTFALL

_LINK_IDS = [f"C{i}" for i in range(1, 12)]  # C1..C11
_LINK_TYPES = [0] * 11  # all CONDUIT

_SUBCATCH_IDS = [f"S{i}" for i in range(1, 9)]  # S1..S8
_GAGE_IDS = ["RG1", "RG2"]


def _id_to_index(ids: list[str], element_id: str) -> int:
    """Return the index of *element_id* in *ids*, or -1 if not found."""
    try:
        return ids.index(element_id)
    except ValueError:
        return -1


# ---------------------------------------------------------------------------
# MockSolver
# ---------------------------------------------------------------------------


class MockSolver:
    """Mock of :class:`openswmm.engine.Solver`.

    Simulates a 100-step run over a 6-hour (0.25-day) duration starting at
    Julian day 45000.0.  Tracks lifecycle calls in ``_calls``.
    """

    def __init__(self, inp: str = "", rpt: str = "", out: str = "") -> None:
        self.inp_path = inp
        self.rpt_path = rpt
        self.out_path = out
        self._is_open = False
        self._is_initialized = False
        self._is_started = False
        self._is_ended = False
        self._step_count = 0
        self._max_steps = 100
        self._state = 0
        self._elapsed = 0.0
        self._options: dict[str, str] = {}
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    # -- Properties ----------------------------------------------------------

    @property
    def handle(self) -> int:
        """Return a non-zero handle when the solver is open, 0 otherwise."""
        return 12345 if self._is_open else 0

    @property
    def state(self) -> int:
        return self._state

    @state.setter
    def state(self, value: int) -> None:
        self._state = value

    @property
    def elapsed(self) -> float:
        return self._elapsed

    @elapsed.setter
    def elapsed(self, value: float) -> None:
        self._elapsed = value

    # -- Lifecycle -----------------------------------------------------------

    def create(self) -> None:
        self._calls.append(("create", ()))

    def open(self) -> None:
        self._calls.append(("open", ()))
        self._is_open = True
        self._state = 1

    def initialize(self) -> None:
        self._calls.append(("initialize", ()))
        self._is_initialized = True
        self._state = 2

    def start(self, save_results: bool = True) -> None:
        self._calls.append(("start", (save_results,)))
        self._is_started = True
        self._state = 3

    def step(self) -> bool:
        self._calls.append(("step", ()))
        self._step_count += 1
        total_duration = self.get_end_time() - self.get_start_time()
        self._elapsed = (self._step_count / self._max_steps) * total_duration
        if self._step_count >= self._max_steps:
            return False
        return True

    def end(self) -> None:
        self._calls.append(("end", ()))
        self._is_ended = True
        self._is_started = False
        self._state = 4

    def report(self) -> None:
        self._calls.append(("report", ()))

    def close(self) -> None:
        self._calls.append(("close", ()))
        self._is_open = False
        self._state = 5

    def destroy(self) -> None:
        self._calls.append(("destroy", ()))
        self._state = 0

    # -- Time queries --------------------------------------------------------

    def get_start_time(self) -> float:
        """Julian day number for simulation start."""
        return 45000.0

    def get_end_time(self) -> float:
        """Julian day number for simulation end (6-hour run)."""
        return 45000.25

    def get_current_time(self) -> float:
        return self.get_start_time() + self._elapsed

    def get_routing_step(self) -> float:
        """Routing timestep in seconds."""
        return 30.0

    # -- Options -------------------------------------------------------------

    def get_option(self, name: str) -> str:
        self._calls.append(("get_option", (name,)))
        defaults: dict[str, str] = {
            "FLOW_UNITS": "CFS",
            "ROUTING_MODEL": "DYNWAVE",
        }
        return self._options.get(name, defaults.get(name, "UNKNOWN"))

    def set_option(self, name: str, value: str) -> None:
        self._calls.append(("set_option", (name, value)))
        self._options[name] = value

    # -- Model I/O -----------------------------------------------------------

    def model_write(self, path: str) -> None:
        self._calls.append(("model_write", (path,)))
        Path(path).write_text("[TITLE]\nMock SWMM Model\n")

    # -- Context manager -----------------------------------------------------

    def __enter__(self) -> MockSolver:
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# MockNodes
# ---------------------------------------------------------------------------


class MockNodes:
    """Mock of :class:`openswmm.engine.Nodes`.

    12 nodes: J1..J11 (JUNCTION) + O1 (OUTFALL).
    """

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []
        self._depths: dict[int, float] = {}
        self._lateral_inflows: dict[int, float] = {}

    def count(self) -> int:
        return len(_NODE_IDS)

    def get_id(self, idx: int) -> str:
        self._calls.append(("get_id", (idx,)))
        return _NODE_IDS[idx]

    def get_index(self, node_id: str) -> int:
        self._calls.append(("get_index", (node_id,)))
        return _id_to_index(_NODE_IDS, node_id)

    def get_type(self, idx: int) -> int:
        self._calls.append(("get_type", (idx,)))
        return _NODE_TYPES[idx]

    def get_invert_elev(self, idx: int) -> float:
        self._calls.append(("get_invert_elev", (idx,)))
        return 100.0 - idx

    def get_max_depth(self, idx: int) -> float:
        self._calls.append(("get_max_depth", (idx,)))
        return 10.0

    def get_depth(self, idx: int) -> float:
        self._calls.append(("get_depth", (idx,)))
        return self._depths.get(idx, 1.5)

    def get_head(self, idx: int) -> float:
        self._calls.append(("get_head", (idx,)))
        return self.get_invert_elev(idx) + self.get_depth(idx)

    def get_volume(self, idx: int) -> float:
        self._calls.append(("get_volume", (idx,)))
        return 50.0

    def get_lateral_inflow(self, idx: int) -> float:
        self._calls.append(("get_lateral_inflow", (idx,)))
        return self._lateral_inflows.get(idx, 0.1)

    def get_overflow(self, idx: int) -> float:
        self._calls.append(("get_overflow", (idx,)))
        return 0.0

    def set_depth(self, idx: int, val: float) -> None:
        self._calls.append(("set_depth", (idx, val)))
        self._depths[idx] = val

    def set_lateral_inflow(self, idx: int, val: float) -> None:
        self._calls.append(("set_lateral_inflow", (idx, val)))
        self._lateral_inflows[idx] = val

    def get_depths_bulk(self) -> np.ndarray:
        self._calls.append(("get_depths_bulk", ()))
        return np.ones(len(_NODE_IDS))

    def get_heads_bulk(self) -> np.ndarray:
        self._calls.append(("get_heads_bulk", ()))
        return np.full(len(_NODE_IDS), 100.0)


# ---------------------------------------------------------------------------
# MockLinks
# ---------------------------------------------------------------------------


class MockLinks:
    """Mock of :class:`openswmm.engine.Links`.

    11 links: C1..C11 (all CONDUIT).
    """

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def count(self) -> int:
        return len(_LINK_IDS)

    def get_id(self, idx: int) -> str:
        self._calls.append(("get_id", (idx,)))
        return _LINK_IDS[idx]

    def get_index(self, link_id: str) -> int:
        self._calls.append(("get_index", (link_id,)))
        return _id_to_index(_LINK_IDS, link_id)

    def get_type(self, idx: int) -> int:
        self._calls.append(("get_type", (idx,)))
        return _LINK_TYPES[idx]

    def get_from_node(self, idx: int) -> int:
        self._calls.append(("get_from_node", (idx,)))
        return idx

    def get_to_node(self, idx: int) -> int:
        self._calls.append(("get_to_node", (idx,)))
        return idx + 1

    def get_length(self, idx: int) -> float:
        self._calls.append(("get_length", (idx,)))
        return 400.0

    def get_roughness(self, idx: int) -> float:
        self._calls.append(("get_roughness", (idx,)))
        return 0.013

    def get_flow(self, idx: int) -> float:
        self._calls.append(("get_flow", (idx,)))
        return 2.5

    def get_depth(self, idx: int) -> float:
        self._calls.append(("get_depth", (idx,)))
        return 0.8

    def get_velocity(self, idx: int) -> float:
        self._calls.append(("get_velocity", (idx,)))
        return 3.0

    def get_capacity(self, idx: int) -> float:
        self._calls.append(("get_capacity", (idx,)))
        return 0.6

    def get_max_depth(self, idx: int) -> float:
        self._calls.append(("get_max_depth", (idx,)))
        return 3.0


# ---------------------------------------------------------------------------
# MockSubcatchments
# ---------------------------------------------------------------------------


class MockSubcatchments:
    """Mock of :class:`openswmm.engine.Subcatchments`.

    8 subcatchments: S1..S8.
    """

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def count(self) -> int:
        return len(_SUBCATCH_IDS)

    def get_id(self, idx: int) -> str:
        self._calls.append(("get_id", (idx,)))
        return _SUBCATCH_IDS[idx]

    def get_index(self, sc_id: str) -> int:
        self._calls.append(("get_index", (sc_id,)))
        return _id_to_index(_SUBCATCH_IDS, sc_id)

    def get_area(self, idx: int) -> float:
        self._calls.append(("get_area", (idx,)))
        return 5.0

    def get_imperv_pct(self, idx: int) -> float:
        self._calls.append(("get_imperv_pct", (idx,)))
        return 50.0

    def get_slope(self, idx: int) -> float:
        self._calls.append(("get_slope", (idx,)))
        return 0.5

    def get_width(self, idx: int) -> float:
        self._calls.append(("get_width", (idx,)))
        return 100.0

    def get_rainfall(self, idx: int) -> float:
        self._calls.append(("get_rainfall", (idx,)))
        return 0.5

    def get_runoff(self, idx: int) -> float:
        self._calls.append(("get_runoff", (idx,)))
        return 0.3

    def get_depth(self, idx: int) -> float:
        self._calls.append(("get_depth", (idx,)))
        return 0.1


# ---------------------------------------------------------------------------
# MockGages
# ---------------------------------------------------------------------------


class MockGages:
    """Mock of :class:`openswmm.engine.Gages`.

    2 gages: RG1, RG2 (TIMESERIES source, INTENSITY rain type).
    """

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []
        self._rainfall: dict[int, float] = {}

    def count(self) -> int:
        return len(_GAGE_IDS)

    def get_id(self, idx: int) -> str:
        self._calls.append(("get_id", (idx,)))
        return _GAGE_IDS[idx]

    def get_index(self, gage_id: str) -> int:
        self._calls.append(("get_index", (gage_id,)))
        return _id_to_index(_GAGE_IDS, gage_id)

    def get_data_source(self, idx: int) -> int:
        self._calls.append(("get_data_source", (idx,)))
        return 0  # TIMESERIES

    def get_rain_type(self, idx: int) -> int:
        self._calls.append(("get_rain_type", (idx,)))
        return 0  # INTENSITY

    def get_rainfall(self, idx: int) -> float:
        self._calls.append(("get_rainfall", (idx,)))
        return self._rainfall.get(idx, 1.0)

    def set_rainfall(self, idx: int, val: float) -> None:
        self._calls.append(("set_rainfall", (idx, val)))
        self._rainfall[idx] = val


# ---------------------------------------------------------------------------
# MockMassBalance
# ---------------------------------------------------------------------------


class MockMassBalance:
    """Mock of :class:`openswmm.engine.MassBalance`.

    Returns -0.01 runoff error, -0.02 routing error, 0.0 quality error.
    """

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def get_runoff_continuity_error(self) -> float:
        self._calls.append(("get_runoff_continuity_error", ()))
        return -0.01

    def get_routing_continuity_error(self) -> float:
        self._calls.append(("get_routing_continuity_error", ()))
        return -0.02

    def get_quality_continuity_error(self, pollutant_idx: int = 0) -> float:
        self._calls.append(("get_quality_continuity_error", (pollutant_idx,)))
        return 0.0

    def get_runoff_total(self, enum_val: Any) -> float:
        self._calls.append(("get_runoff_total", (enum_val,)))
        return 100.0

    def get_routing_total(self, enum_val: Any) -> float:
        self._calls.append(("get_routing_total", (enum_val,)))
        return 200.0


# ---------------------------------------------------------------------------
# MockStatistics
# ---------------------------------------------------------------------------


class MockStatistics:
    """Mock of :class:`openswmm.engine.Statistics`.

    Nodes 0-2 (J1-J3) have flooding: vol_flooded=100, time_flooded=2.0.
    """

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    # -- Node statistics -----------------------------------------------------

    def node_max_depth(self, id_or_idx: str | int) -> float:
        self._calls.append(("node_max_depth", (id_or_idx,)))
        return 5.0

    def node_max_head(self, id_or_idx: str | int) -> float:
        self._calls.append(("node_max_head", (id_or_idx,)))
        return 105.0

    def node_max_lat_inflow(self, id_or_idx: str | int) -> float:
        self._calls.append(("node_max_lat_inflow", (id_or_idx,)))
        return 10.0

    def node_max_overflow(self, id_or_idx: str | int) -> float:
        self._calls.append(("node_max_overflow", (id_or_idx,)))
        return 0.5

    def node_vol_flooded(self, id_or_idx: str | int) -> float:
        self._calls.append(("node_vol_flooded", (id_or_idx,)))
        idx = id_or_idx
        if isinstance(idx, str):
            idx = _id_to_index(_NODE_IDS, idx)
        return 100.0 if idx < 3 else 0.0

    def node_time_flooded(self, id_or_idx: str | int) -> float:
        self._calls.append(("node_time_flooded", (id_or_idx,)))
        idx = id_or_idx
        if isinstance(idx, str):
            idx = _id_to_index(_NODE_IDS, idx)
        return 2.0 if idx < 3 else 0.0

    # -- Link statistics -----------------------------------------------------

    def link_max_flow(self, id_or_idx: str | int) -> float:
        self._calls.append(("link_max_flow", (id_or_idx,)))
        return 15.0

    def link_max_velocity(self, id_or_idx: str | int) -> float:
        self._calls.append(("link_max_velocity", (id_or_idx,)))
        return 5.0

    def link_max_depth(self, id_or_idx: str | int) -> float:
        self._calls.append(("link_max_depth", (id_or_idx,)))
        return 3.0

    def link_time_above_normal(self, id_or_idx: str | int) -> float:
        self._calls.append(("link_time_above_normal", (id_or_idx,)))
        return 1.0

    # -- Subcatchment statistics ---------------------------------------------

    def subcatch_max_runoff(self, id_or_idx: str | int) -> float:
        self._calls.append(("subcatch_max_runoff", (id_or_idx,)))
        return 8.0


# ---------------------------------------------------------------------------
# MockForcing
# ---------------------------------------------------------------------------


class MockForcing:
    """Mock of :class:`openswmm.engine.Forcing`.

    All methods record their arguments in ``_calls`` for later assertion.
    """

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def node_lat_inflow(self, id: str, val: float, mode: int = 0, persist: int = 0) -> None:
        self._calls.append(("node_lat_inflow", (id, val, mode, persist)))

    def node_head_boundary(self, id: str, val: float, mode: int = 0, persist: int = 0) -> None:
        self._calls.append(("node_head_boundary", (id, val, mode, persist)))

    def node_quality(
        self, id: str, pollutant_idx: int, val: float, mode: int = 0, persist: int = 0
    ) -> None:
        self._calls.append(("node_quality", (id, pollutant_idx, val, mode, persist)))

    def link_flow(self, id: str, val: float, mode: int = 0, persist: int = 0) -> None:
        self._calls.append(("link_flow", (id, val, mode, persist)))

    def link_setting(self, id: str, val: float, mode: int = 0, persist: int = 0) -> None:
        self._calls.append(("link_setting", (id, val, mode, persist)))

    def subcatch_rainfall(self, id: str, val: float, mode: int = 0, persist: int = 0) -> None:
        self._calls.append(("subcatch_rainfall", (id, val, mode, persist)))

    def subcatch_evap(self, id: str, val: float, mode: int = 0, persist: int = 0) -> None:
        self._calls.append(("subcatch_evap", (id, val, mode, persist)))

    def gage_rainfall(self, id: str, val: float, mode: int = 0, persist: int = 0) -> None:
        self._calls.append(("gage_rainfall", (id, val, mode, persist)))

    def clear(self, target_type: str, element_id: str) -> None:
        self._calls.append(("clear", (target_type, element_id)))

    def clear_all(self) -> None:
        self._calls.append(("clear_all", ()))


# ---------------------------------------------------------------------------
# MockControls
# ---------------------------------------------------------------------------


class MockControls:
    """Mock of :class:`openswmm.engine.Controls`."""

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []
        self._rule_count = 0

    def add_rule(self, text: str) -> int:
        self._calls.append(("add_rule", (text,)))
        self._rule_count += 1
        return self._rule_count

    def set_link_setting(self, link_id: str, setting: float) -> None:
        self._calls.append(("set_link_setting", (link_id, setting)))

    def count(self) -> int:
        self._calls.append(("count", ()))
        return self._rule_count


# ---------------------------------------------------------------------------
# MockHotStart
# ---------------------------------------------------------------------------


class MockHotStart:
    """Mock of :class:`openswmm.engine.HotStart`."""

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def save(self, solver: Any, path: str) -> None:
        self._calls.append(("save", (solver, path)))
        Path(path).touch()

    def open(self, path: str) -> MockHotStart:
        self._calls.append(("open", (path,)))
        return self

    def apply(self, solver: Any) -> None:
        self._calls.append(("apply", (solver,)))

    def close(self) -> None:
        self._calls.append(("close", ()))


# ---------------------------------------------------------------------------
# MockSpatial
# ---------------------------------------------------------------------------


class MockSpatial:
    """Mock of :class:`openswmm.engine.Spatial`."""

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []
        self._node_coords: dict[str, tuple[float, float]] = {}
        self._link_coords: dict[str, tuple[float, float]] = {}
        self._subcatch_coords: dict[str, tuple[float, float]] = {}

    def get_node_coord(self, id: str) -> tuple[float, float]:
        self._calls.append(("get_node_coord", (id,)))
        return self._node_coords.get(id, (1.0, 2.0))

    def get_link_coord(self, id: str) -> tuple[float, float]:
        self._calls.append(("get_link_coord", (id,)))
        return self._link_coords.get(id, (3.0, 4.0))

    def get_subcatch_coord(self, id: str) -> tuple[float, float]:
        self._calls.append(("get_subcatch_coord", (id,)))
        return self._subcatch_coords.get(id, (5.0, 6.0))

    def set_node_coord(self, id: str, x: float, y: float) -> None:
        self._calls.append(("set_node_coord", (id, x, y)))
        self._node_coords[id] = (x, y)

    def set_link_coord(self, id: str, x: float, y: float) -> None:
        self._calls.append(("set_link_coord", (id, x, y)))
        self._link_coords[id] = (x, y)

    def set_subcatch_coord(self, id: str, x: float, y: float) -> None:
        self._calls.append(("set_subcatch_coord", (id, x, y)))
        self._subcatch_coords[id] = (x, y)


# ---------------------------------------------------------------------------
# MockModelBuilder
# ---------------------------------------------------------------------------


class MockModelBuilder:
    """Mock of :class:`openswmm.engine.ModelBuilder`."""

    def __init__(self) -> None:
        self._calls: list[tuple[str, tuple[Any, ...]]] = []
        self._nodes: list[str] = []
        self._links: list[str] = []
        self._subcatchments: list[str] = []
        self._gages: list[str] = []
        self._options: dict[str, str] = {}

    # -- Node operations -----------------------------------------------------

    def add_node(self, id: str, type: int = 0) -> int:
        self._calls.append(("add_node", (id, type)))
        self._nodes.append(id)
        return len(self._nodes) - 1

    def set_node_invert(self, id: str, elev: float) -> None:
        self._calls.append(("set_node_invert", (id, elev)))

    def set_node_max_depth(self, id: str, depth: float) -> None:
        self._calls.append(("set_node_max_depth", (id, depth)))

    # -- Link operations -----------------------------------------------------

    def add_link(self, id: str, type: int = 0) -> int:
        self._calls.append(("add_link", (id, type)))
        self._links.append(id)
        return len(self._links) - 1

    def set_link_nodes(self, id: str, from_n: str, to_n: str) -> None:
        self._calls.append(("set_link_nodes", (id, from_n, to_n)))

    def set_link_length(self, id: str, length: float) -> None:
        self._calls.append(("set_link_length", (id, length)))

    def set_link_roughness(self, id: str, roughness: float) -> None:
        self._calls.append(("set_link_roughness", (id, roughness)))

    def set_link_xsect(
        self, id: str, shape: int, g1: float, g2: float, g3: float, g4: float
    ) -> None:
        self._calls.append(("set_link_xsect", (id, shape, g1, g2, g3, g4)))

    # -- Subcatchment operations ---------------------------------------------

    def add_subcatchment(self, id: str) -> int:
        self._calls.append(("add_subcatchment", (id,)))
        self._subcatchments.append(id)
        return len(self._subcatchments) - 1

    def set_subcatch_area(self, id: str, area: float) -> None:
        self._calls.append(("set_subcatch_area", (id, area)))

    def set_subcatch_slope(self, id: str, slope: float) -> None:
        self._calls.append(("set_subcatch_slope", (id, slope)))

    def set_subcatch_width(self, id: str, width: float) -> None:
        self._calls.append(("set_subcatch_width", (id, width)))

    def set_subcatch_imperv(self, id: str, pct: float) -> None:
        self._calls.append(("set_subcatch_imperv", (id, pct)))

    def set_subcatch_outlet(self, id: str, outlet: str) -> None:
        self._calls.append(("set_subcatch_outlet", (id, outlet)))

    # -- Gage operations -----------------------------------------------------

    def add_gage(self, id: str) -> int:
        self._calls.append(("add_gage", (id,)))
        self._gages.append(id)
        return len(self._gages) - 1

    # -- Options -------------------------------------------------------------

    def set_option(self, option: str, value: str) -> None:
        self._calls.append(("set_option", (option, value)))
        self._options[option] = value

    # -- Validation / Finalization -------------------------------------------

    def validate(self) -> list[str]:
        self._calls.append(("validate", ()))
        return []  # empty = valid

    def finalize(self) -> MockSolver:
        self._calls.append(("finalize", ()))
        return MockSolver()

    def to_solver(self) -> MockSolver:
        self._calls.append(("to_solver", ()))
        return MockSolver()


# ---------------------------------------------------------------------------
# MockOutputReader
# ---------------------------------------------------------------------------


class MockOutputReader:
    """Mock of :class:`openswmm.engine.OutputReader`.

    Returns deterministic random data using a fixed seed for reproducibility.
    """

    def __init__(self, path: str = "") -> None:
        self._path = path
        self._is_open = False
        self._calls: list[tuple[str, tuple[Any, ...]]] = []
        self._rng = np.random.RandomState(42)

    def open(self, path: str) -> None:
        self._calls.append(("open", (path,)))
        self._path = path
        self._is_open = True

    def close(self) -> None:
        self._calls.append(("close", ()))
        self._is_open = False

    # -- Counts --------------------------------------------------------------

    def get_node_count(self) -> int:
        return len(_NODE_IDS)

    def get_node_id(self, idx: int) -> str:
        return _NODE_IDS[idx]

    def get_link_count(self) -> int:
        return len(_LINK_IDS)

    def get_link_id(self, idx: int) -> str:
        return _LINK_IDS[idx]

    def get_subcatch_count(self) -> int:
        return len(_SUBCATCH_IDS)

    def get_subcatch_id(self, idx: int) -> str:
        return _SUBCATCH_IDS[idx]

    def get_period_count(self) -> int:
        return 100

    # -- Series --------------------------------------------------------------

    def get_node_series(self, id: str, var: Any, start: int, end: int) -> np.ndarray:
        self._calls.append(("get_node_series", (id, var, start, end)))
        return self._rng.rand(end - start)

    def get_link_series(self, id: str, var: Any, start: int, end: int) -> np.ndarray:
        self._calls.append(("get_link_series", (id, var, start, end)))
        return self._rng.rand(end - start)

    def get_subcatch_series(self, id: str, var: Any, start: int, end: int) -> np.ndarray:
        self._calls.append(("get_subcatch_series", (id, var, start, end)))
        return self._rng.rand(end - start)

    def get_system_series(self, var: Any, start: int, end: int) -> np.ndarray:
        self._calls.append(("get_system_series", (var, start, end)))
        return self._rng.rand(end - start)

    # -- Time metadata -------------------------------------------------------

    def get_start_time(self) -> float:
        return 45000.0

    def get_report_step(self) -> float:
        return 300.0


# ---------------------------------------------------------------------------
# MockPollutants
# ---------------------------------------------------------------------------


class MockPollutants:
    """Mock of :class:`openswmm.engine.Pollutants`."""

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def count(self) -> int:
        return 0

    def get_id(self, idx: int) -> str:
        self._calls.append(("get_id", (idx,)))
        return f"P{idx + 1}"

    def get_index(self, pollutant_id: str) -> int:
        self._calls.append(("get_index", (pollutant_id,)))
        return -1

    def get_concentration(self, idx: int) -> float:
        self._calls.append(("get_concentration", (idx,)))
        return 0.0


# ---------------------------------------------------------------------------
# MockTables
# ---------------------------------------------------------------------------


class MockTables:
    """Mock of :class:`openswmm.engine.Tables`."""

    def __init__(self, solver_or_builder: Any = None) -> None:
        self._solver = solver_or_builder
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def add_timeseries(self, name: str, times: list[float], values: list[float]) -> None:
        self._calls.append(("add_timeseries", (name, times, values)))

    def add_curve(
        self,
        name: str,
        curve_type: str,
        x_values: list[float],
        y_values: list[float],
    ) -> None:
        self._calls.append(("add_curve", (name, curve_type, x_values, y_values)))

    def count(self) -> int:
        return 0

    def get_id(self, idx: int) -> str:
        self._calls.append(("get_id", (idx,)))
        return f"T{idx + 1}"


# ---------------------------------------------------------------------------
# MockInflows
# ---------------------------------------------------------------------------


class MockInflows:
    """Mock of :class:`openswmm.engine.Inflows`."""

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def count(self) -> int:
        return 0

    def set_inflow(self, node_id: str, value: float) -> None:
        self._calls.append(("set_inflow", (node_id, value)))

    def get_inflow(self, node_id: str) -> float:
        self._calls.append(("get_inflow", (node_id,)))
        return 0.0


# ---------------------------------------------------------------------------
# MockInfrastructure
# ---------------------------------------------------------------------------


class MockInfrastructure:
    """Mock of :class:`openswmm.engine.Infrastructure`."""

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def add_lid(self, subcatch_id: str, lid_type: str, area: float) -> None:
        self._calls.append(("add_lid", (subcatch_id, lid_type, area)))

    def count(self) -> int:
        return 0


# ---------------------------------------------------------------------------
# MockQuality
# ---------------------------------------------------------------------------


class MockQuality:
    """Mock of :class:`openswmm.engine.Quality`."""

    def __init__(self, solver: MockSolver | Any = None) -> None:
        self._solver = solver
        self._calls: list[tuple[str, tuple[Any, ...]]] = []

    def get_node_quality(self, node_id: str) -> dict[str, float]:
        self._calls.append(("get_node_quality", (node_id,)))
        return {"TSS": 10.0, "BOD": 5.0}

    def get_link_quality(self, link_id: str) -> dict[str, float]:
        self._calls.append(("get_link_quality", (link_id,)))
        return {"TSS": 8.0, "BOD": 3.0}

    def get_subcatch_quality(self, subcatch_id: str) -> dict[str, float]:
        self._calls.append(("get_subcatch_quality", (subcatch_id,)))
        return {"TSS": 12.0, "BOD": 6.0}

    def set_treatment(self, node_id: str, pollutant: str, expression: str) -> None:
        self._calls.append(("set_treatment", (node_id, pollutant, expression)))

    def count(self) -> int:
        return 2
