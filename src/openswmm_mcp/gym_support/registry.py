# SPDX-License-Identifier: Apache-2.0
#
# Copyright 2026 Caleb Buahin
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Kind registry: maps config strings to C{openswmm.gymnasium} classes.

Single source of truth for the vocabulary the C{gym_*} tools accept
(plan §3.3). Each registered *kind* carries the import path of the gym
class it constructs and a Pydantic params model, so:

  - C{gym_list_capabilities} derives its output (names + JSON schemas)
    from this table, and
  - L{build_env<openswmm_mcp.gym_support.config.build_env>} validates
    and constructs from the same table —

guaranteeing the two can never drift apart.

Gym classes are imported lazily by L{resolve_kind}; everything else in
this module works without the C{gym} extra installed.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: Apache-2.0
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from openswmm_mcp.errors import ErrorCode, ToolError

# ---------------------------------------------------------------------------
# Param models (one per kind; extra params are rejected)
# ---------------------------------------------------------------------------


class _Params(BaseModel):
    """Base for per-kind param models: forbid unknown keys.

    @cvar model_config: Pydantic config rejecting extra fields so a
        typo'd param fails fast with a clear validation error.
    """

    model_config = ConfigDict(extra="forbid")


# -- reward terms -----------------------------------------------------------


class FloodingVolumeParams(_Params):
    """Params for the C{flooding_volume} reward term.

    @ivar node_ids: Nodes to sum over, or C{None} for all nodes.
    @ivar name: Term identifier used in C{info["reward_components"]}.
    """

    node_ids: list[str] | None = None
    name: str = "flooding_volume"


class CSOVolumeParams(_Params):
    """Params for the C{cso_volume} reward term.

    @ivar node_ids: Tagged CSO / overflow node IDs (required).
    @ivar name: Term identifier.
    """

    node_ids: list[str] = Field(min_length=1)
    name: str = "cso_volume"


class PeakOutflowParams(_Params):
    """Params for the C{peak_outflow} reward term.

    @ivar link_ids: Links whose flow magnitudes to track (required).
    @ivar name: Term identifier.
    """

    link_ids: list[str] = Field(min_length=1)
    name: str = "peak_outflow"


class ReliabilityMarginParams(_Params):
    """Params for the C{reliability_margin} reward term.

    @ivar node_ids: Nodes to evaluate, or C{None} for all nodes.
    @ivar name: Term identifier.
    """

    node_ids: list[str] | None = None
    name: str = "reliability_margin"


class SetpointSmoothnessParams(_Params):
    """Params for the C{setpoint_smoothness} reward term.

    @ivar link_ids: Links whose setting trajectories to penalise (required).
    @ivar name: Term identifier.
    """

    link_ids: list[str] = Field(min_length=1)
    name: str = "setpoint_smoothness"


class UncontrolledDischargeParams(_Params):
    """Params for the C{uncontrolled_discharge} reward term.

    @ivar link_ids: Links discharging to untreated outfalls (required).
    @ivar name: Term identifier.
    """

    link_ids: list[str] = Field(min_length=1)
    name: str = "uncontrolled_discharge"


class StorageUnderUtilizationParams(_Params):
    """Params for the C{storage_underutilization} reward term.

    @ivar node_ids: Storage node IDs (required).
    @ivar name: Term identifier.
    """

    node_ids: list[str] = Field(min_length=1)
    name: str = "storage_underutilization"


class PumpEnergyParams(_Params):
    """Params for the C{pump_energy} reward term.

    @ivar link_ids: Pump link IDs (required).
    @ivar rated_power: Optional C{{link_id: power}} (default 1.0 each).
    @ivar name: Term identifier.
    """

    link_ids: list[str] = Field(min_length=1)
    rated_power: dict[str, float] | None = None
    name: str = "pump_energy"


# -- runtime action factories ----------------------------------------------


class OrificeSettingParams(_Params):
    """Params for the C{orifice_setting} runtime factory.

    @ivar link_ids: Symbolic IDs of links to control (required).
    @ivar name: Action-space key for this factory.
    """

    link_ids: list[str] = Field(min_length=1)
    name: str = "orifice_setting"


class NodeLateralInflowParams(_Params):
    """Params for the C{node_lateral_inflow} runtime factory.

    @ivar node_ids: Symbolic IDs of nodes to drive (required).
    @ivar max_inflow: Upper bound of the per-node inflow action
        (project flow units). Must be positive.
    @ivar name: Action-space key for this factory.
    """

    node_ids: list[str] = Field(min_length=1)
    max_inflow: float = Field(gt=0.0)
    name: str = "node_lateral_inflow"


# -- design action factories -------------------------------------------------


class _BoundedLinkDesignParams(_Params):
    """Shared shape for link design factories (roughness/length/diameter).

    @ivar link_ids: Symbolic IDs of links the design dimension spans.
    @ivar low: Lower bound of the design range.
    @ivar high: Upper bound of the design range.
    """

    link_ids: list[str] = Field(min_length=1)
    low: float
    high: float


class LinkRoughnessParams(_BoundedLinkDesignParams):
    """Params for the C{link_roughness} design factory.

    @ivar name: Action-space key for this factory.
    """

    name: str = "link_roughness"


class LinkLengthParams(_BoundedLinkDesignParams):
    """Params for the C{link_length} design factory.

    @ivar name: Action-space key for this factory.
    """

    name: str = "link_length"


class LinkDiameterParams(_BoundedLinkDesignParams):
    """Params for the C{link_diameter} design factory.

    @ivar name: Action-space key for this factory.
    """

    name: str = "link_diameter"


class NodeMaxDepthParams(_Params):
    """Params for the C{node_max_depth} design factory.

    @ivar node_ids: Symbolic IDs of nodes the design dimension spans.
    @ivar low: Lower bound of the design range.
    @ivar high: Upper bound of the design range.
    @ivar name: Action-space key for this factory.
    """

    node_ids: list[str] = Field(min_length=1)
    low: float
    high: float
    name: str = "node_max_depth"


class SubcatchGWOutflowCoeffParams(_Params):
    """Params for the C{subcatch_gw_outflow_coeff} design factory.

    @ivar subcatch_ids: Symbolic IDs of subcatchments the design dimension
        spans (each must have an aquifer assigned).
    @ivar low: Lower bound of the groundwater outflow coefficient C{a1}.
    @ivar high: Upper bound of C{a1}.
    @ivar name: Action-space key for this factory.
    """

    subcatch_ids: list[str] = Field(min_length=1)
    low: float
    high: float
    name: str = "subcatch_gw_outflow_coeff"


class StorageVolumeParams(_Params):
    """Params for the C{storage_volume} design factory.

    Sizes FUNCTIONAL storage assets. In C{mode="scalar"} (default) C{low}/C{high}
    are scalar footprint multipliers; in C{mode="coeffs"} they are length-3
    C{(a, b, c)} bound sequences for the raw functional relation.

    @ivar node_ids: STORAGE node IDs to size (each must be FUNCTIONAL shape).
    @ivar low: Lower bound(s): a scalar (scalar mode) or C{[a, b, c]} (coeffs).
    @ivar high: Upper bound(s), matching C{low}'s shape.
    @ivar mode: C{"scalar"} or C{"coeffs"}.
    @ivar name: Action-space key for this factory.
    """

    node_ids: list[str] = Field(min_length=1)
    low: float | list[float]
    high: float | list[float]
    mode: Literal["scalar", "coeffs"] = "scalar"
    name: str = "storage_volume"

    @model_validator(mode="after")
    def _check_mode_bounds(self) -> StorageVolumeParams:
        scalar_low = isinstance(self.low, (int, float))
        scalar_high = isinstance(self.high, (int, float))
        if self.mode == "scalar":
            if not (scalar_low and scalar_high):
                raise ValueError("mode 'scalar' requires scalar low/high")
            if self.high <= self.low:
                raise ValueError("high must be strictly greater than low")
        else:  # coeffs
            if scalar_low or scalar_high or len(self.low) != 3 or len(self.high) != 3:
                raise ValueError("mode 'coeffs' requires length-3 (a, b, c) low/high")
        return self


class LIDPlacementParams(_Params):
    """Params for the C{lid_placement} design factory.

    Sizes green-infrastructure / nature-based solutions and selects among
    candidate LID control types per subcatchment.

    @ivar subcatch_ids: Subcatchments to place GI on (required).
    @ivar lid_controls: Existing LID-control IDs to choose among — the
        selectable "types"; must be defined in the model (required).
    @ivar area_low: Lower bound on placed LID area.
    @ivar area_high: Upper bound on placed LID area.
    @ivar number: Replicate LID units per placement.
    @ivar width: Overland-flow width per unit (0 = engine default).
    @ivar init_sat: Initial saturation fraction (0-100).
    @ivar from_imperv: Percent of upstream impervious runoff routed onto the LID.
    @ivar name: Action-space key for this factory.
    """

    subcatch_ids: list[str] = Field(min_length=1)
    lid_controls: list[str] = Field(min_length=1)
    area_low: float
    area_high: float
    number: int = 1
    width: float = 0.0
    init_sat: float = 0.0
    from_imperv: float = 0.0
    name: str = "lid_placement"

    @model_validator(mode="after")
    def _check_area(self) -> LIDPlacementParams:
        if self.area_high <= self.area_low:
            raise ValueError("area_high must be strictly greater than area_low")
        return self


class RDIIUnitHydrographParams(_Params):
    """Params for the C{rdii_unit_hydrograph} design factory.

    Sizes RDII response by editing the R fraction (and optionally the
    initial-abstraction terms) of existing unit-hydrograph entries,
    preserving T and K.

    @ivar targets: C{(uh_name, month, response)} triples to size (required).
    @ivar r_low: Lower bound on the R fraction.
    @ivar r_high: Upper bound on the R fraction.
    @ivar include_ia: Also search initial abstraction (dmax, drecov, dinit).
    @ivar ia_low: Length-3 lower bounds C{(dmax, drecov, dinit)}.
    @ivar ia_high: Length-3 upper bounds C{(dmax, drecov, dinit)}.
    @ivar name: Action-space key for this factory.
    """

    targets: list[tuple[str, int, int]] = Field(min_length=1)
    r_low: float
    r_high: float
    include_ia: bool = False
    ia_low: tuple[float, float, float] = (0.0, 0.0, 0.0)
    ia_high: tuple[float, float, float] = (0.0, 0.0, 0.0)
    name: str = "rdii_unit_hydrograph"

    @model_validator(mode="after")
    def _check_bounds(self) -> RDIIUnitHydrographParams:
        if self.r_high <= self.r_low:
            raise ValueError("r_high must be strictly greater than r_low")
        if self.include_ia and not any(hi > lo for lo, hi in zip(self.ia_low, self.ia_high)):
            raise ValueError(
                "include_ia requires at least one of (dmax, drecov, dinit) to have ia_high > ia_low"
            )
        return self


# -- policy factories (searchable static control policies) --------------------


class ControlCurveAssetParams(_Params):
    """One controlled link and its PWL breakpoint curve.

    @ivar link_id: Controlled link ID (orifice/weir/pump). Must exist.
    @ivar obs_node: Observed node feeding the curve's C{x} axis. Must exist.
    @ivar obs_attr: Observation attribute (C{depthN}, C{headN}, C{volumeN},
        C{inflowN}).
    @ivar x_knots: Strictly increasing knot positions (length >= 2).
    @ivar y_low: Lower search bound for every knot setting.
    @ivar y_high: Upper search bound for every knot setting.
    @ivar y_init: Optional seed/neutral curve (defaults to all C{y_high}).
    @ivar monotonic: Curve constraint, projected at decode time.
    """

    link_id: str = Field(min_length=1)
    obs_node: str = Field(min_length=1)
    obs_attr: Literal["depthN", "depth", "headN", "volumeN", "inflowN"] = "depthN"
    x_knots: list[float] = Field(min_length=2)
    y_low: float = 0.0
    y_high: float = 1.0
    y_init: list[float] | None = None
    monotonic: Literal["none", "nonincreasing", "nondecreasing"] = "none"

    @model_validator(mode="after")
    def _check_asset(self) -> ControlCurveAssetParams:
        if any(self.x_knots[i + 1] <= self.x_knots[i] for i in range(len(self.x_knots) - 1)):
            raise ValueError(f"asset {self.link_id!r}: x_knots must be strictly increasing")
        if self.y_low > self.y_high:
            raise ValueError(
                f"asset {self.link_id!r}: y_low ({self.y_low}) must be <= y_high ({self.y_high})"
            )
        if self.y_init is not None and len(self.y_init) != len(self.x_knots):
            raise ValueError(
                f"asset {self.link_id!r}: y_init must have one value per knot ({len(self.x_knots)})"
            )
        return self


class ControlCurveParams(_Params):
    """Params for the C{control_curve} policy factory.

    The decision vector is the C{y} setting at each knot of each asset
    (asset-major, knot order); the C{x_knots} are fixed. Bounds come from each
    asset's C{[y_low, y_high]}. A flat C{y = y_high} curve reproduces the
    uncontrolled baseline for passive-open structures.

    @ivar assets: One entry per controllable asset (required, non-empty).
    @ivar x_normalized: Index curves by C{depth / full_depth} (recommended).
    @ivar control_interval_steps: Recompute the curve every N control steps.
    @ivar rate_limit: Optional max C{|Δsetting|} per control step; C{None} = off.
    @ivar name: Action-space / decision key.
    """

    assets: list[ControlCurveAssetParams] = Field(min_length=1)
    x_normalized: bool = True
    control_interval_steps: int = Field(default=1, ge=1)
    rate_limit: float | None = Field(default=None, gt=0.0)
    name: str = "control_curve"


# -- wrappers -----------------------------------------------------------------


class RecordTrajectoryParams(_Params):
    """Params for the C{record_trajectory} wrapper.

    @ivar output_dir: Directory to write JSONL trajectory files into.
        Must be user-visible (CLAUDE.md §4.1) — never a temp dir.
    """

    output_dir: str


class RescaleBoxActionsParams(_Params):
    """Params for the C{rescale_box_actions} wrapper.

    @ivar src_low: Lower bound the wrapper exposes for Box leaves.
    @ivar src_high: Upper bound the wrapper exposes for Box leaves.
    """

    src_low: float = 0.0
    src_high: float = 1.0


class LinearScalarizeParams(_Params):
    """Params for the C{linear_scalarize} wrapper.

    @ivar weights: Weight per objective (required, non-empty).
    """

    weights: list[float] = Field(min_length=1)


class TchebycheffScalarizeParams(_Params):
    """Params for the C{tchebycheff_scalarize} wrapper.

    @ivar weights: Weight per objective (required, non-empty).
    @ivar utopia: Per-objective utopia point (required, non-empty).
    """

    weights: list[float] = Field(min_length=1)
    utopia: list[float] = Field(min_length=1)


class NoParams(_Params):
    """Empty params model for kinds that take no configuration."""


# ---------------------------------------------------------------------------
# Registry table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KindSpec:
    """One registered kind: config string -> gym class + param schema.

    @ivar kind: Registry name used in config JSON (e.g. C{"flooding_volume"}).
    @ivar category: One of C{"reward_term"}, C{"runtime_factory"},
        C{"design_factory"}, C{"wrapper"}.
    @ivar import_path: C{"module:ClassName"} of the gym class.
    @ivar params_model: Pydantic model validating this kind's params.
    @ivar description: One-line human/LLM-readable summary.
    """

    kind: str
    category: str
    import_path: str
    params_model: type[_Params]
    description: str


_REGISTRY: dict[tuple[str, str], KindSpec] = {}


def _register(spec: KindSpec) -> None:
    """Insert *spec* into the module registry (internal).

    @param spec: The kind spec to register.
    @type spec: L{KindSpec}
    @raise ValueError: If C{(category, kind)} is already registered.
    """
    key = (spec.category, spec.kind)
    if key in _REGISTRY:
        raise ValueError(f"duplicate registry entry: {key}")
    _REGISTRY[key] = spec


for _spec in [
    # reward terms (openswmm_gymnasium.rewards)
    KindSpec(
        "flooding_volume",
        "reward_term",
        "openswmm_gymnasium.rewards:FloodingVolume",
        FloodingVolumeParams,
        "Cumulative node flooding volume (cost to minimize).",
    ),
    KindSpec(
        "cso_volume",
        "reward_term",
        "openswmm_gymnasium.rewards:CSOVolume",
        CSOVolumeParams,
        "Cumulative overflow volume at tagged CSO nodes (cost to minimize).",
    ),
    KindSpec(
        "peak_outflow",
        "reward_term",
        "openswmm_gymnasium.rewards:PeakOutflow",
        PeakOutflowParams,
        "Peak flow magnitude across tracked links (cost to minimize).",
    ),
    KindSpec(
        "reliability_margin",
        "reward_term",
        "openswmm_gymnasium.rewards:ReliabilityMargin",
        ReliabilityMarginParams,
        "Freeboard margin at nodes (benefit; sign-flipped internally).",
    ),
    KindSpec(
        "setpoint_smoothness",
        "reward_term",
        "openswmm_gymnasium.rewards:SetpointSmoothness",
        SetpointSmoothnessParams,
        "Penalty on rapid link-setting changes (cost to minimize).",
    ),
    KindSpec(
        "uncontrolled_discharge",
        "reward_term",
        "openswmm_gymnasium.rewards:UncontrolledDischarge",
        UncontrolledDischargeParams,
        "Discharge through links feeding untreated outfalls (cost to minimize).",
    ),
    KindSpec(
        "storage_underutilization",
        "reward_term",
        "openswmm_gymnasium.rewards:StorageUnderUtilization",
        StorageUnderUtilizationParams,
        "Time-integrated unused storage headroom (cost to minimize).",
    ),
    KindSpec(
        "pump_energy",
        "reward_term",
        "openswmm_gymnasium.rewards:PumpEnergy",
        PumpEnergyParams,
        "Pumping effort = setting x rated_power x dt (cost to minimize).",
    ),
    # runtime factories (openswmm_gymnasium.spaces.runtime)
    KindSpec(
        "orifice_setting",
        "runtime_factory",
        "openswmm_gymnasium.spaces.runtime:OrificeSetting",
        OrificeSettingParams,
        "Per-step [0,1] control setting on links (orifice/weir/pump).",
    ),
    KindSpec(
        "node_lateral_inflow",
        "runtime_factory",
        "openswmm_gymnasium.spaces.runtime:NodeLateralInflow",
        NodeLateralInflowParams,
        "Per-step controllable lateral inflow [0,max_inflow] at nodes.",
    ),
    # design factories (openswmm_gymnasium.spaces.design)
    KindSpec(
        "link_roughness",
        "design_factory",
        "openswmm_gymnasium.spaces.design:LinkRoughness",
        LinkRoughnessParams,
        "Design-time Manning roughness per link in [low,high].",
    ),
    KindSpec(
        "link_length",
        "design_factory",
        "openswmm_gymnasium.spaces.design:LinkLength",
        LinkLengthParams,
        "Design-time conduit length per link in [low,high].",
    ),
    KindSpec(
        "link_diameter",
        "design_factory",
        "openswmm_gymnasium.spaces.design:LinkDiameter",
        LinkDiameterParams,
        "Design-time conduit diameter per link in [low,high].",
    ),
    KindSpec(
        "node_max_depth",
        "design_factory",
        "openswmm_gymnasium.spaces.design:NodeMaxDepth",
        NodeMaxDepthParams,
        "Design-time maximum node depth per node in [low,high].",
    ),
    KindSpec(
        "subcatch_gw_outflow_coeff",
        "design_factory",
        "openswmm_gymnasium.spaces.design:SubcatchGWOutflowCoeff",
        SubcatchGWOutflowCoeffParams,
        "Design-time groundwater outflow coefficient (a1) per subcatchment "
        "in [low,high]; other [GROUNDWATER] params preserved.",
    ),
    KindSpec(
        "storage_volume",
        "design_factory",
        "openswmm_gymnasium.spaces.design:StorageVolume",
        StorageVolumeParams,
        "Design-time storage sizing per FUNCTIONAL storage node: a scalar "
        "footprint multiplier (mode='scalar') or the raw (a,b,c) surface-area "
        "coefficients (mode='coeffs').",
    ),
    KindSpec(
        "lid_placement",
        "design_factory",
        "openswmm_gymnasium.spaces.design:LIDPlacement",
        LIDPlacementParams,
        "Design-time green-infrastructure sizing + type selection: place a "
        "sized LID usage per subcatchment, choosing among candidate LID "
        "control types defined in the model.",
    ),
    KindSpec(
        "rdii_unit_hydrograph",
        "design_factory",
        "openswmm_gymnasium.spaces.design:RDIIUnitHydrograph",
        RDIIUnitHydrographParams,
        "Design-time RDII sizing: edit the R fraction (and optionally initial "
        "abstraction) of existing unit-hydrograph entries, preserving T and K.",
    ),
    # policy factories (openswmm_gymnasium.spaces.control_curve)
    KindSpec(
        "control_curve",
        "policy_factory",
        "openswmm_gymnasium.spaces.control_curve:ControlCurvePolicySpace",
        ControlCurveParams,
        "Searchable reactive PWL control curves: tune the [0,1] setting at each "
        "fixed knot per controlled link, indexed by an observed node's state.",
    ),
    # wrappers (openswmm_gymnasium.wrappers)
    KindSpec(
        "record_trajectory",
        "wrapper",
        "openswmm_gymnasium.wrappers:RecordTrajectory",
        RecordTrajectoryParams,
        "Record per-step observations/actions/rewards to JSONL files.",
    ),
    KindSpec(
        "rescale_box_actions",
        "wrapper",
        "openswmm_gymnasium.wrappers:RescaleBoxActions",
        RescaleBoxActionsParams,
        "Expose Box action leaves rescaled to [src_low,src_high].",
    ),
    KindSpec(
        "linear_scalarize",
        "wrapper",
        "openswmm_gymnasium.wrappers:LinearScalarize",
        LinearScalarizeParams,
        "Weighted-sum scalarization of vector rewards.",
    ),
    KindSpec(
        "tchebycheff_scalarize",
        "wrapper",
        "openswmm_gymnasium.wrappers:TchebycheffScalarize",
        TchebycheffScalarizeParams,
        "Tchebycheff scalarization of vector rewards against a utopia point.",
    ),
    KindSpec(
        "mask_runtime",
        "wrapper",
        "openswmm_gymnasium.wrappers:MaskRuntimeAction",
        NoParams,
        "Hide the runtime half: agent acts on design only.",
    ),
    KindSpec(
        "mask_design",
        "wrapper",
        "openswmm_gymnasium.wrappers:MaskDesignAction",
        NoParams,
        "Hide the design half: agent acts on runtime only.",
    ),
]:
    _register(_spec)

# NB: ForecastObservation is deliberately not registered — it requires a
# Python callable (forecast_fn) and cannot be expressed declaratively.


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_kinds(category: str | None = None) -> list[KindSpec]:
    """Return registered kinds, optionally filtered by category.

    @param category: Restrict to one category (C{"reward_term"},
        C{"runtime_factory"}, C{"design_factory"}, C{"wrapper"}), or
        C{None} for all.
    @type category: str or C{None}
    @return: Matching specs, sorted by (category, kind).
    @rtype: list of L{KindSpec}
    """
    specs = [s for s in _REGISTRY.values() if category is None or s.category == category]
    return sorted(specs, key=lambda s: (s.category, s.kind))


def get_kind(category: str, kind: str) -> KindSpec:
    """Look up one registered kind.

    @param category: The kind's category.
    @type category: str
    @param kind: The registry name.
    @type kind: str
    @return: The matching spec.
    @rtype: L{KindSpec}
    @raise ToolError: With code C{VALIDATION_ERROR} when the kind is
        unknown; the message lists the valid names for *category*.
    """
    spec = _REGISTRY.get((category, kind))
    if spec is None:
        valid = ", ".join(s.kind for s in list_kinds(category)) or "<none>"
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown {category} kind '{kind}'. "
            f"Valid kinds: {valid}."
        )
    return spec


def validate_params(category: str, kind: str, params: dict[str, Any]) -> _Params:
    """Validate *params* against the kind's params model.

    @param category: The kind's category.
    @type category: str
    @param kind: The registry name.
    @type kind: str
    @param params: Raw params dict from config JSON.
    @type params: dict
    @return: The validated params model instance.
    @rtype: L{_Params}
    @raise ToolError: With code C{VALIDATION_ERROR} when the kind is
        unknown or the params do not match its schema.
    """
    spec = get_kind(category, kind)
    try:
        return spec.params_model(**params)
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid params for {category} '{kind}': {exc}"
        ) from exc


def resolve_kind(category: str, kind: str) -> type:
    """Import and return the gym class behind a registered kind.

    Lazy: C{openswmm_gymnasium} is only imported here, so every other
    registry function works without the C{gym} extra.

    @param category: The kind's category.
    @type category: str
    @param kind: The registry name.
    @type kind: str
    @return: The gym class (e.g. C{FloodingVolume}).
    @rtype: type
    @raise ToolError: With code C{VALIDATION_ERROR} for unknown kinds, or
        C{DEPENDENCY_MISSING} when C{openswmm_gymnasium} is not installed.
    """
    spec = get_kind(category, kind)
    module_name, _, class_name = spec.import_path.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.DEPENDENCY_MISSING}] Resolving {category} '{kind}' "
            "requires the optional openswmm.gymnasium package. Install it "
            "with: pip install 'openswmm.mcp[gym]'"
        ) from exc
    return getattr(module, class_name)


def construct_kind(category: str, kind: str, params: dict[str, Any]) -> Any:
    """Validate params and construct an instance of the kind's gym class.

    Wrappers are not constructible here (they need a live env first);
    use L{resolve_kind} and apply them in
    L{build_env<openswmm_mcp.gym_support.config.build_env>} instead.

    @param category: One of C{"reward_term"}, C{"runtime_factory"},
        C{"design_factory"}.
    @type category: str
    @param kind: The registry name.
    @type kind: str
    @param params: Raw params dict from config JSON.
    @type params: dict
    @return: A constructed gym object.
    @rtype: object
    @raise ToolError: C{VALIDATION_ERROR} for unknown kind/bad params or
        wrapper categories; C{DEPENDENCY_MISSING} without the gym extra.
    """
    if category in ("wrapper", "policy_factory"):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] {category} kinds are applied by "
            "build_env, not constructed directly."
        )
    validated = validate_params(category, kind, params)
    cls = resolve_kind(category, kind)
    return cls(**validated.model_dump())
