"""Pydantic response models for all OpenSWMM-MCP tool responses."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

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
# Geometry sub-models
# ---------------------------------------------------------------------------


class CrossSectionInfo(BaseModel):
    """Cross-section shape and geometry parameters for a conduit or weir."""

    model_config = ConfigDict(from_attributes=True)

    shape: int
    """Integer shape code (see XSectShape enum)."""
    shape_name: str
    """Human-readable shape name, e.g. ``CIRCULAR``."""
    geom1: float
    geom2: float = 0.0
    geom3: float = 0.0
    geom4: float = 0.0
    geom_labels: dict[str, float] = Field(default_factory=dict)
    """Mapping of semantic parameter name to value, e.g. ``{"diameter": 1.2}``."""


class ConduitGeometry(BaseModel):
    """Full geometry descriptor for a CONDUIT link."""

    model_config = ConfigDict(from_attributes=True)

    xsect: CrossSectionInfo | None = None
    slope: float | None = None
    offset_up: float = 0.0
    offset_dn: float = 0.0
    initial_flow: float = 0.0
    max_flow: float = 0.0
    loss_coeff_inlet: float = 0.0
    loss_coeff_outlet: float = 0.0
    loss_coeff_avg: float = 0.0
    flap_gate: bool = False
    seep_rate: float = 0.0
    culvert_code: int = 0
    barrels: int = 1


class WeirGeometry(BaseModel):
    """Geometry descriptor for a WEIR link."""

    model_config = ConfigDict(from_attributes=True)

    xsect: CrossSectionInfo | None = None
    crest_height: float = 0.0
    discharge_coeff: float = 0.0
    end_contractions: float = 0.0
    offset_up: float = 0.0
    offset_dn: float = 0.0


class OrificeGeometry(BaseModel):
    """Geometry descriptor for an ORIFICE link."""

    model_config = ConfigDict(from_attributes=True)

    xsect: CrossSectionInfo | None = None
    offset_up: float = 0.0
    offset_dn: float = 0.0


class PumpGeometry(BaseModel):
    """Geometry descriptor for a PUMP link."""

    model_config = ConfigDict(from_attributes=True)

    pump_curve_idx: int = -1
    init_state_on: bool = False
    offset_up: float = 0.0
    offset_dn: float = 0.0


class StorageGeometry(BaseModel):
    """Storage-unit geometry for a STORAGE node."""

    model_config = ConfigDict(from_attributes=True)

    storage_type: str = "functional"
    """``curve`` if a stage-area curve is used, ``functional`` otherwise."""
    curve_idx: int | None = None
    functional_a: float | None = None
    """Coefficient A in Area = A * Depth^B + C."""
    functional_b: float | None = None
    functional_c: float | None = None
    seep_rate: float = 0.0
    exfil_suction: float | None = None
    exfil_ksat: float | None = None
    exfil_imd: float | None = None


class OutfallGeometry(BaseModel):
    """Boundary-condition geometry for an OUTFALL node."""

    model_config = ConfigDict(from_attributes=True)

    outfall_type: int = 0
    outfall_type_name: str = "FREE"
    """One of FREE, NORMAL, FIXED, TIDAL, TIMESERIES."""
    param: float = 0.0
    """Stage value for FIXED outfalls; 0 for others."""
    flap_gate: bool = False


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
    # Extended geometry
    crown_elev: float | None = None
    full_volume: float | None = None
    surcharge_depth: float | None = None
    ponded_area: float | None = None
    degree: int | None = None
    initial_depth: float | None = None
    losses: float | None = None
    outflow: float | None = None
    # Type-specific geometry (populated only for the matching node type)
    storage: StorageGeometry | None = None
    outfall: OutfallGeometry | None = None
    # Runtime hydraulic state
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
    # Extended geometry
    slope: float | None = None
    offset_up: float | None = None
    offset_dn: float | None = None
    initial_flow: float | None = None
    max_flow: float | None = None
    xsect: CrossSectionInfo | None = None
    # Type-specific geometry (populated only for the matching link type)
    conduit: ConduitGeometry | None = None
    weir: WeirGeometry | None = None
    orifice: OrificeGeometry | None = None
    pump: PumpGeometry | None = None
    # Runtime hydraulic state
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
    vol_flow: float = 0.0


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


# ---------------------------------------------------------------------------
# Property updates
# ---------------------------------------------------------------------------


class PropertyUpdateResult(BaseModel):
    """Returned after updating element properties in-place."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    element_type: str
    """``node``, ``link``, or ``subcatchment``."""
    element_id: str
    updated_fields: dict[str, float | int | str]
    """Mapping of field name to the new value that was applied."""


class GageConfigResult(BaseModel):
    """Returned after configuring a rain gage."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str
    gage_id: str
    updated_fields: dict[str, str | float | int]
    """Mapping of field name to the new value that was applied."""


# ---------------------------------------------------------------------------
# Pollutant info
# ---------------------------------------------------------------------------


class PollutantInfo(BaseModel):
    """Definition and properties of a single pollutant."""

    model_config = ConfigDict(from_attributes=True)

    pollutant_id: str
    index: int
    units: int
    """Concentration units code: 0=MG/L, 1=UG/L, 2=#/L."""
    units_name: str
    kdecay: float
    rain_conc: float
    gw_conc: float
    init_conc: float
    rdii_conc: float
    mwt: float
    snow_only: bool
    co_pollutant_idx: int
    co_pollutant_frac: float


# ---------------------------------------------------------------------------
# Report snapshot models (mirror engine._report dataclasses)
# ---------------------------------------------------------------------------


class RoutingDiagnosticsModel(BaseModel):
    """Routing Time Step Summary — convergence and time-step statistics."""

    model_config = ConfigDict(from_attributes=True)

    avg_time_step: float
    min_time_step: float
    max_time_step: float
    n_steps: int
    pct_not_converged: float
    n_steps_not_converged: int
    avg_iterations: float
    max_courant: float


class RunoffContinuityModel(BaseModel):
    """Runoff Quantity Continuity table."""

    model_config = ConfigDict(from_attributes=True)

    continuity_error_pct: float
    total_rainfall: float
    total_evaporation: float
    total_infiltration: float
    total_runoff: float
    total_snow_removal: float
    initial_storage: float
    final_storage: float


class RoutingContinuityModel(BaseModel):
    """Flow Routing Continuity table."""

    model_config = ConfigDict(from_attributes=True)

    continuity_error_pct: float
    dry_weather_inflow: float
    wet_weather_inflow: float
    groundwater_inflow: float
    rdii_inflow: float
    external_inflow: float
    total_flooding: float
    total_outflow: float
    evaporation_loss: float
    seepage_loss: float
    initial_storage: float
    final_storage: float


class QualityContinuityModel(BaseModel):
    """Quality Routing Continuity entry for a single pollutant."""

    model_config = ConfigDict(from_attributes=True)

    pollutant_id: str
    continuity_error_pct: float
    seep_loss: float
    evap_loss: float


class NodeFloodingEntryModel(BaseModel):
    """One row of the Node Flooding Summary table."""

    model_config = ConfigDict(from_attributes=True)

    node_id: str
    node_type: str
    max_depth: float
    max_overflow_rate: float
    total_flood_volume: float
    time_flooded: float


class LinkFlowEntryModel(BaseModel):
    """One row of the Link Flow Summary table."""

    model_config = ConfigDict(from_attributes=True)

    link_id: str
    link_type: str
    max_flow: float
    max_velocity: float
    max_filling: float
    total_volume: float
    surcharge_time: float


class PumpEntryModel(BaseModel):
    """One row of the Pump Summary table."""

    model_config = ConfigDict(from_attributes=True)

    link_id: str
    pump_curve_idx: int
    num_startups: int
    total_on_time: float
    total_volume: float
    pct_time_on: float


class SubcatchmentEntryModel(BaseModel):
    """One row of the Subcatchment Runoff Summary table."""

    model_config = ConfigDict(from_attributes=True)

    subcatch_id: str
    total_precip: float
    total_runoff_vol: float
    max_runoff_rate: float
    runoff_coefficient: float


class ReportSnapshotModel(BaseModel):
    """Full programmatic equivalent of the SWMM .rpt report file."""

    model_config = ConfigDict(from_attributes=True)

    routing_diagnostics: RoutingDiagnosticsModel
    runoff_continuity: RunoffContinuityModel
    routing_continuity: RoutingContinuityModel
    quality_continuity: list[QualityContinuityModel]
    node_flooding: list[NodeFloodingEntryModel]
    storage_summary: list[NodeFloodingEntryModel]
    link_flow_summary: list[LinkFlowEntryModel]
    pump_summary: list[PumpEntryModel]
    subcatchment_summary: list[SubcatchmentEntryModel]
