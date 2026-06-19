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
@license: MIT
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
            f"[{ErrorCode.VALIDATION_ERROR}] Invalid params for {category} "
            f"'{kind}': {exc}"
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
    if category == "wrapper":
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Wrappers are applied by "
            "build_env, not constructed directly."
        )
    validated = validate_params(category, kind, params)
    cls = resolve_kind(category, kind)
    return cls(**validated.model_dump())
