"""Pydantic response models for all OpenSWMM-MCP tool responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Session / Model metadata
# ---------------------------------------------------------------------------


class ModelSummary(BaseModel):
    """Returned after opening or inspecting a SWMM model."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    state: str
    engine: str = "openswmm"
    node_count: int
    link_count: int
    subcatchment_count: int
    gage_count: int
    pollutant_count: int
    flow_units: str
    route_model: str
    start_time: float
    end_time: float
    routing_step: float


class SessionListItem(BaseModel):
    """Compact session descriptor used in list-sessions responses."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    state: str
    engine: str = "openswmm"
    node_count: int
    link_count: int
    subcatchment_count: int


class SystemSummary(BaseModel):
    """Full system-level summary including optional runtime state."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    state: str
    engine: str = "openswmm"
    node_count: int
    link_count: int
    subcatchment_count: int
    gage_count: int
    pollutant_count: int
    flow_units: str
    route_model: str
    start_time: float
    end_time: float
    routing_step: float
    current_time: float | None = None
    # Extended fields (from refactored engine)
    surcharge_method: str | None = None
    dps_celerity: float | None = None
    dps_alpha: float | None = None
    dps_decay_time: float | None = None
    event_count: int | None = None
    steady_state_skip: bool | None = None


# ---------------------------------------------------------------------------
# Simulation progress
# ---------------------------------------------------------------------------


class SimulationResult(BaseModel):
    """Returned when a full simulation run completes."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    elapsed_wall_time: float
    steps_completed: int
    runoff_continuity_error: float
    routing_continuity_error: float
    quality_continuity_error: float | None = None


class StepResult(BaseModel):
    """Returned after executing one or more simulation steps."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    elapsed: float
    current_time: float
    completed: bool
    steps_taken: int


# ---------------------------------------------------------------------------
# Element info
# ---------------------------------------------------------------------------


class NodeInfo(BaseModel):
    """Properties and current state of a single node."""

    model_config = ConfigDict(from_attributes=True)

    node_id: str
    index: int
    node_type: str
    invert_elev: float | None = None
    max_depth: float | None = None
    depth: float | None = None
    head: float | None = None
    volume: float | None = None
    lateral_inflow: float | None = None
    overflow: float | None = None
    outfall_route_to: int | None = None
    ponded_quality: dict[str, float] | None = None


class LinkInfo(BaseModel):
    """Properties and current state of a single link."""

    model_config = ConfigDict(from_attributes=True)

    link_id: str
    index: int
    link_type: str
    from_node: str
    to_node: str
    length: float | None = None
    roughness: float | None = None
    flow: float | None = None
    depth: float | None = None
    velocity: float | None = None
    capacity: float | None = None
    hydraulic_power: float | None = None
    pump_cycles: int | None = None
    pump_on_time: float | None = None
    pump_volume: float | None = None


class SubcatchmentInfo(BaseModel):
    """Properties and current state of a single subcatchment."""

    model_config = ConfigDict(from_attributes=True)

    subcatch_id: str
    index: int
    area: float | None = None
    imperv_pct: float | None = None
    slope: float | None = None
    width: float | None = None
    rainfall: float | None = None
    runoff: float | None = None
    depth: float | None = None


class GageInfo(BaseModel):
    """Properties and current state of a rain gage."""

    model_config = ConfigDict(from_attributes=True)

    gage_id: str
    index: int
    data_source: str
    rain_type: str
    rainfall: float | None = None


# ---------------------------------------------------------------------------
# Mass balance
# ---------------------------------------------------------------------------


class MassBalanceResult(BaseModel):
    """Continuity errors and volumetric totals."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    runoff_continuity_error: float
    routing_continuity_error: float
    quality_continuity_error: float | None = None
    quality_continuity_errors: dict[str, float] | None = None
    runoff_total: dict[str, float]
    routing_total: dict[str, float]
    routing_stats: dict[str, float] | None = None
    max_courant: float | None = None


# ---------------------------------------------------------------------------
# Time-series
# ---------------------------------------------------------------------------


class TimeSeries(BaseModel):
    """Variable time-series for a single element."""

    element_type: str
    element_id: str
    variable: str
    timestamps: list[float]
    values: list[float]
    units: str


# ---------------------------------------------------------------------------
# Post-simulation summaries
# ---------------------------------------------------------------------------


class FloodingSummaryItem(BaseModel):
    """Per-node flooding statistics."""

    node_id: str
    max_overflow_rate: float
    total_flood_volume: float
    time_flooded: float
    max_depth: float


class CapacitySummaryItem(BaseModel):
    """Per-link capacity / flow statistics."""

    link_id: str
    max_filling: float
    max_flow: float
    max_velocity: float
    time_above_threshold: float


# ---------------------------------------------------------------------------
# Forcing / scenario-editing
# ---------------------------------------------------------------------------


class ForcingResult(BaseModel):
    """Acknowledgement after applying a forcing override."""

    status: str
    target_type: str
    element_id: str
    variable: str
    value: float
    mode: str
    persist: bool


class BuildingResult(BaseModel):
    """Acknowledgement after creating or modifying a model element."""

    status: str
    element_type: str
    element_id: str
    index: int
    message: str


# ---------------------------------------------------------------------------
# Hot-start / spatial / export / search
# ---------------------------------------------------------------------------


class HotStartResult(BaseModel):
    """Result of a hot-start save or load operation."""

    status: str
    path: str
    message: str


class SpatialResult(BaseModel):
    """Coordinates and vertex geometry for a model element."""

    element_type: str
    element_id: str
    x: float | None = None
    y: float | None = None
    vertices: list[tuple[float, float]] | None = None


class ExportResult(BaseModel):
    """Acknowledgement after an export operation."""

    status: str
    path: str
    format: str
    record_count: int


class ElementSearchResult(BaseModel):
    """Single match returned by an element search."""

    element_type: str
    element_id: str
    index: int


# ---------------------------------------------------------------------------
# Editing — deletion and type conversion
# ---------------------------------------------------------------------------


class ImpactEntryModel(BaseModel):
    """One object that is affected by a deletion (cascade or nullification)."""

    model_config = ConfigDict(from_attributes=True)

    obj_type: int
    """Integer code: 0=node, 1=link, 2=subcatchment, 3=gage, 4=table, 5=transect, 6=inlet_usage."""
    obj_type_name: str
    """Human-readable object type name."""
    obj_idx: int
    """Zero-based index of the affected object."""
    field: str
    """Name of the cross-reference field that was affected."""
    cascaded: bool
    """True if the object was deleted; False if only the reference was nullified."""


class ImpactReportModel(BaseModel):
    """Result of a deletion impact analysis or a deletion operation."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    object_type: str
    """Type of the deleted / analysed object (``node``, ``link``, etc.)."""
    object_id: str
    """Identifier of the deleted / analysed object."""
    dry_run: bool
    """True when the analysis was non-destructive (no objects were deleted)."""
    node_count: int
    """Number of nodes remaining after deletion (same as before for dry_run)."""
    link_count: int
    """Number of links remaining after deletion (same as before for dry_run)."""
    impacts: list[ImpactEntryModel]
    """Objects that were (or would be) affected."""


class ConversionResultModel(BaseModel):
    """Result of an in-place type conversion."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    object_type: str
    """``node`` or ``link``."""
    object_id: str
    new_type: str
    """Human-readable new type name."""
    cleared_fields: list[str]
    """Type-specific fields that were cleared during conversion."""
    warnings: list[str]
    """Non-fatal topology warnings (e.g. "model has no outfall")."""
