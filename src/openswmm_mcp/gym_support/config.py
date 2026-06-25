"""Declarative environment config models and C{build_env} (plan §3.3).

The L{EnvConfig} tree is the core artifact the LLM composes through the
C{gym_*} tools: pure-JSON, validated by Pydantic against the kind
registry, persisted by
L{GymStore<openswmm_mcp.gym_support.store.GymStore>}, and turned into a
live C{gymnasium.Env} by L{build_env}.

Schema validation (kinds, params, env-type constraints) works without
the C{gym} extra; only L{build_env} imports C{openswmm_gymnasium}.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: MIT
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.gym_support import registry

# Observation feature name -> ObservationBuilder method name.
_OBS_METHODS: dict[str, str] = {
    "node_depths": "add_node_depths",
    "node_heads": "add_node_heads",
    "node_inflows": "add_node_inflows",
    "node_overflows": "add_node_overflows",
    "node_volumes": "add_node_volumes",
    "node_lateral_inflows": "add_node_lateral_inflows",
    "link_flows": "add_link_flows",
    "link_depths": "add_link_depths",
    "link_settings": "add_link_settings",
    "link_velocities": "add_link_velocities",
    "link_capacities": "add_link_capacities",
    "link_volumes": "add_link_volumes",
    "subcatch_runoff": "add_subcatch_runoff",
    "subcatch_groundwater": "add_subcatch_groundwater",
    "rainfall_gages": "add_rainfall",
}


class ObservationSpec(BaseModel):
    """Declarative observation feature set.

    Each list field names the element IDs whose state enters the flat
    observation vector, in the field order below (the same order the
    underlying C{ObservationBuilder} concatenates collectors).

    @ivar node_depths: Node IDs contributing depth features.
    @ivar node_heads: Node IDs contributing hydraulic-head features.
    @ivar node_inflows: Node IDs contributing total-inflow features.
    @ivar node_overflows: Node IDs contributing overflow features.
    @ivar node_volumes: Node IDs contributing stored-volume features.
    @ivar node_lateral_inflows: Node IDs contributing lateral-inflow features.
    @ivar link_flows: Link IDs contributing flow features.
    @ivar link_depths: Link IDs contributing depth features.
    @ivar link_settings: Link IDs contributing control-setting features.
    @ivar link_velocities: Link IDs contributing velocity features.
    @ivar link_capacities: Link IDs contributing capacity features.
    @ivar link_volumes: Link IDs contributing volume features.
    @ivar subcatch_runoff: Subcatchment IDs contributing runoff features.
    @ivar subcatch_groundwater: Subcatchment IDs contributing groundwater
        (baseflow) features.
    @ivar rainfall_gages: Rain gage IDs contributing rainfall features.
    @ivar include_clock: Whether to append simulation-clock features.
    """

    model_config = ConfigDict(extra="forbid")

    node_depths: list[str] = []
    node_heads: list[str] = []
    node_inflows: list[str] = []
    node_overflows: list[str] = []
    node_volumes: list[str] = []
    node_lateral_inflows: list[str] = []
    link_flows: list[str] = []
    link_depths: list[str] = []
    link_settings: list[str] = []
    link_velocities: list[str] = []
    link_capacities: list[str] = []
    link_volumes: list[str] = []
    subcatch_runoff: list[str] = []
    subcatch_groundwater: list[str] = []
    rainfall_gages: list[str] = []
    include_clock: bool = False

    def is_empty(self) -> bool:
        """Return whether no feature at all is configured.

        @return: C{True} when every list is empty and the clock is off.
        @rtype: bool
        """
        return not self.include_clock and not any(
            getattr(self, field) for field in _OBS_METHODS
        )

    def build(self) -> Any:
        """Construct the C{ObservationBuilder} this spec describes.

        Lazy-imports C{openswmm_gymnasium}.

        @return: A populated C{ObservationBuilder}.
        @rtype: C{openswmm_gymnasium.observations.ObservationBuilder}
        @raise ToolError: C{DEPENDENCY_MISSING} without the gym extra.
        """
        try:
            from openswmm_gymnasium.observations import ObservationBuilder
        except ImportError as exc:
            raise ToolError(
                f"[{ErrorCode.DEPENDENCY_MISSING}] Building observations "
                "requires the optional openswmm.gymnasium package. Install "
                "it with: pip install 'openswmm.mcp[gym]'"
            ) from exc

        builder = ObservationBuilder()
        for field, method in _OBS_METHODS.items():
            ids = getattr(self, field)
            if ids:
                getattr(builder, method)(ids)
        if self.include_clock:
            builder.add_clock()
        return builder


class _KindSpecModel(BaseModel):
    """Base for C{kind}+C{params} spec models, registry-validated.

    Subclasses set L{_category}; the validator checks the kind exists
    in that category and that params satisfy its schema.

    @ivar kind: Registry name (see C{gym_list_capabilities}).
    @ivar params: Kind-specific constructor params.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str
    params: dict[str, Any] = {}

    _category: ClassVar[str] = ""  # overridden by subclasses

    @model_validator(mode="after")
    def _check_against_registry(self) -> _KindSpecModel:
        """Validate C{kind}/C{params} against the registry.

        @return: C{self}, unchanged, when valid.
        @rtype: L{_KindSpecModel}
        @raise ToolError: C{VALIDATION_ERROR} on unknown kind or bad params.
        """
        registry.validate_params(self._category, self.kind, self.params)
        return self

    def construct(self) -> Any:
        """Construct the gym object this spec describes (lazy import).

        @return: The constructed gym object.
        @rtype: object
        """
        return registry.construct_kind(self._category, self.kind, self.params)


class RewardTermSpec(_KindSpecModel):
    """A reward term by registry kind (category C{reward_term})."""

    _category: ClassVar[str] = "reward_term"


class RuntimeFactorySpec(_KindSpecModel):
    """A runtime action factory by registry kind (category C{runtime_factory})."""

    _category: ClassVar[str] = "runtime_factory"


class DesignFactorySpec(_KindSpecModel):
    """A design action factory by registry kind (category C{design_factory})."""

    _category: ClassVar[str] = "design_factory"


class PolicyFactorySpec(_KindSpecModel):
    """A searchable static control policy by registry kind (C{policy_factory}).

    Like a design factory, it contributes static dimensions to the searchable
    decision vector — but instead of mutating the model geometry it installs a
    closed-loop controller evaluated each control step. Not constructed via
    L{registry.construct_kind}; L{build_env} builds the policy space directly
    from C{params}.
    """

    _category: ClassVar[str] = "policy_factory"


# Public alias used by tools that accept either factory flavour.
ActionFactorySpec = RuntimeFactorySpec | DesignFactorySpec


class WrapperSpec(_KindSpecModel):
    """A wrapper by registry kind (category C{wrapper}), applied in list order."""

    _category: ClassVar[str] = "wrapper"


class EnvConfig(BaseModel):
    """Declarative, JSON-serializable environment configuration.

    Maps one-to-one onto the constructors of the four
    C{openswmm.gymnasium} env classes (plan §3.3):

      - C{"rtc"} -> C{SwmmRTCEnv}
      - C{"cip"} -> C{SwmmCIPEnv}
      - C{"joint"} -> C{SwmmJointCIPRTCEnv}
      - C{"mo_rtc"} -> C{SwmmMORTCEnv}
      - C{"market"} -> C{SwmmControlEnv} (tune a reactive market controller's
        cost-curve + PID params; decision vector is the market policy space)
      - C{"schedule"} -> C{SwmmControlEnv} (open-loop full-event optimal control;
        decision vector is per-structure settings over the event)
      - C{"control_curve"} -> C{SwmmControlEnv} (tune a reactive PWL control
        policy; decision vector is the per-knot breakpoint settings of the
        C{policy_factory})

    @ivar env_type: Which env class to construct.
    @ivar inp_path: Path to the SWMM C{.inp} driving each episode.
    @ivar market_config: Market controller config dict (C{"market"} only,
        required); see C{openswmm_gymnasium.config.MarketConfig}.
    @ivar policy_bounds: Optional per-field search-bound overrides for the
        market policy space (C{"market"} only).
    @ivar tune_full: Also tune piecewise-linear C{full} knees (C{"market"} only).
    @ivar control_interval_seconds: Control interval; required for C{"schedule"},
        else overrides the market control interval.
    @ivar structure_ids: Controllable link IDs (C{"schedule"} only, required).
    @ivar n_points: Scheduled settings per structure (C{"schedule"} only, required).
    @ivar schedule_bounds: Optional C{[low, high]} setting bounds (C{"schedule"}
        only; default C{[0, 1]}).
    @ivar runtime_factories: Runtime (RTC) action factories.
    @ivar design_factories: Design (CIP) action factories.
    @ivar policy_factory: Searchable static control policy (C{"control_curve"}
        only, required); decision vector is its policy parameters.
    @ivar observations: Observation feature spec (must be non-empty).
    @ivar reward_terms: Reward terms; empty list means the env default
        (a single all-nodes C{FloodingVolume}).
    @ivar control_interval_steps: Routing steps per env step (rtc/joint/mo_rtc).
    @ivar max_episode_steps: Optional truncation horizon (rtc/joint/mo_rtc).
    @ivar ideal_point: Per-objective best-case values (mo_rtc only, required).
    @ivar reference_point: Per-objective worst-case values (mo_rtc only, required).
    @ivar wrappers: Wrappers applied in list order around the bare env.
    @ivar rpt_path: Optional fixed C{.rpt} path.
    @ivar out_path: Optional fixed C{.out} path.
    """

    model_config = ConfigDict(extra="forbid")

    env_type: Literal[
        "rtc", "cip", "joint", "mo_rtc", "market", "schedule", "control_curve"
    ]
    inp_path: str
    runtime_factories: list[RuntimeFactorySpec] = []
    design_factories: list[DesignFactorySpec] = []
    policy_factory: PolicyFactorySpec | None = None
    observations: ObservationSpec
    reward_terms: list[RewardTermSpec] = []
    control_interval_steps: int = 1
    max_episode_steps: int | None = None
    ideal_point: list[float] | None = None
    reference_point: list[float] | None = None
    wrappers: list[WrapperSpec] = []
    rpt_path: str | None = None
    out_path: str | None = None
    market_config: dict[str, Any] | None = None
    policy_bounds: dict[str, list[float]] | None = None
    tune_full: bool = False
    control_interval_seconds: float | None = None
    structure_ids: list[str] | None = None
    n_points: int | None = None
    schedule_bounds: list[float] | None = None

    @model_validator(mode="after")
    def _check_env_type_constraints(self) -> EnvConfig:
        """Enforce per-env-type structural constraints at schema time.

        Mirrors the C{ValueError}s the env constructors raise, so a bad
        config fails at C{gym_create_env_config} rather than mid-run.

        @return: C{self}, unchanged, when valid.
        @rtype: L{EnvConfig}
        @raise ValueError: On any constraint violation (Pydantic wraps
            this into a validation error).
        """
        if self.observations.is_empty():
            raise ValueError("observations must declare at least one feature")
        if self.control_interval_steps < 1:
            raise ValueError("control_interval_steps must be >= 1")

        if self.env_type == "market":
            if self.market_config is None:
                raise ValueError("env_type 'market' requires market_config")
            if self.design_factories or self.runtime_factories:
                raise ValueError(
                    "env_type 'market' does not accept design_factories or "
                    "runtime_factories; the decision vector is the market policy"
                )
        elif self.market_config is not None:
            raise ValueError("market_config is only valid for env_type 'market'")

        if self.env_type == "schedule":
            if not self.structure_ids or self.n_points is None:
                raise ValueError("env_type 'schedule' requires structure_ids and n_points")
            if self.n_points < 1:
                raise ValueError("n_points must be >= 1")
            if self.control_interval_seconds is None:
                raise ValueError("env_type 'schedule' requires control_interval_seconds")
            if self.design_factories or self.runtime_factories:
                raise ValueError(
                    "env_type 'schedule' does not accept design_factories or "
                    "runtime_factories; the decision vector is the control schedule"
                )
        elif self.structure_ids is not None or self.n_points is not None:
            raise ValueError("structure_ids/n_points are only valid for env_type 'schedule'")

        if self.env_type == "control_curve":
            if self.policy_factory is None:
                raise ValueError("env_type 'control_curve' requires a policy_factory")
            if self.control_interval_seconds is None:
                raise ValueError(
                    "env_type 'control_curve' requires control_interval_seconds"
                )
            if self.design_factories or self.runtime_factories:
                raise ValueError(
                    "env_type 'control_curve' does not accept design_factories or "
                    "runtime_factories; the decision vector is the control policy"
                )
        elif self.policy_factory is not None:
            raise ValueError(
                "policy_factory is only valid for env_type 'control_curve'"
            )

        if self.env_type in ("cip", "joint") and not self.design_factories:
            raise ValueError(f"env_type '{self.env_type}' requires design_factories")
        if self.env_type in ("rtc", "mo_rtc") and self.design_factories:
            raise ValueError(
                f"env_type '{self.env_type}' does not accept design_factories; "
                "use 'joint' for combined CIP+RTC"
            )
        if self.env_type == "cip" and self.runtime_factories:
            raise ValueError(
                "env_type 'cip' does not accept runtime_factories; "
                "use 'joint' for combined CIP+RTC"
            )

        if self.env_type == "mo_rtc":
            if not self.reward_terms:
                raise ValueError("env_type 'mo_rtc' requires at least one reward term")
            if self.ideal_point is None or self.reference_point is None:
                raise ValueError(
                    "env_type 'mo_rtc' requires both ideal_point and reference_point"
                )
            n = len(self.reward_terms)
            if len(self.ideal_point) != n or len(self.reference_point) != n:
                raise ValueError(
                    f"ideal_point and reference_point must each have one value "
                    f"per reward term ({n})"
                )
        elif self.ideal_point is not None or self.reference_point is not None:
            raise ValueError(
                "ideal_point/reference_point are only valid for env_type 'mo_rtc'"
            )
        return self


def build_env(config: EnvConfig) -> Any:
    """Construct a live, wrapped C{gymnasium.Env} from *config*.

    The only function in this module that imports C{openswmm_gymnasium}.
    The returned env is unreset; callers own its lifecycle and must
    C{close()} it.

    @param config: A validated environment config.
    @type config: L{EnvConfig}
    @return: The constructed env, with C{config.wrappers} applied in
        list order.
    @rtype: C{gymnasium.Env}
    @raise ToolError: C{DEPENDENCY_MISSING} without the gym extra;
        C{ENGINE_ERROR} when env construction fails (e.g. unknown
        element IDs in the model).
    """
    try:
        from openswmm_gymnasium import (
            SwmmCIPEnv,
            SwmmControlEnv,
            SwmmJointCIPRTCEnv,
            SwmmMORTCEnv,
            SwmmRTCEnv,
        )
    except ImportError as exc:
        raise ToolError(
            f"[{ErrorCode.DEPENDENCY_MISSING}] build_env requires the optional "
            "openswmm.gymnasium package. Install it with: "
            "pip install 'openswmm.mcp[gym]'"
        ) from exc

    observation_builder = config.observations.build()
    reward_terms = [spec.construct() for spec in config.reward_terms] or None
    runtime_factories = [spec.construct() for spec in config.runtime_factories] or None
    design_factories = [spec.construct() for spec in config.design_factories]

    common: dict[str, Any] = {
        "observation_builder": observation_builder,
        "reward_terms": reward_terms,
        "rpt_path": config.rpt_path,
        "out_path": config.out_path,
    }
    stepped: dict[str, Any] = {
        "control_interval_steps": config.control_interval_steps,
        "max_episode_steps": config.max_episode_steps,
    }

    try:
        if config.env_type == "rtc":
            env = SwmmRTCEnv(
                config.inp_path,
                runtime_factories=runtime_factories,
                **common,
                **stepped,
            )
        elif config.env_type == "cip":
            env = SwmmCIPEnv(
                config.inp_path,
                design_factories=design_factories,
                **common,
            )
        elif config.env_type == "joint":
            env = SwmmJointCIPRTCEnv(
                config.inp_path,
                design_factories=design_factories,
                runtime_factories=runtime_factories,
                **common,
                **stepped,
            )
        elif config.env_type == "market":
            from openswmm_gymnasium.config import MarketConfig
            from openswmm_gymnasium.spaces import MarketPolicySpace

            market = MarketConfig.from_dict(config.market_config)
            market.validate()
            bounds = (
                {k: tuple(v) for k, v in config.policy_bounds.items()}
                if config.policy_bounds
                else None
            )
            policy_space = MarketPolicySpace.from_config(
                market, bounds=bounds, tune_full=config.tune_full
            )
            env = SwmmControlEnv(
                config.inp_path,
                market_config=market,
                policy_space=policy_space,
                control_interval_seconds=config.control_interval_seconds,
                **common,
            )
        elif config.env_type == "schedule":
            from openswmm_gymnasium.control import ScheduleController
            from openswmm_gymnasium.spaces import SchedulePolicySpace

            structures = list(config.structure_ids)
            lo, hi = (config.schedule_bounds or [0.0, 1.0])
            policy_space = SchedulePolicySpace(
                structures, config.n_points, low=float(lo), high=float(hi)
            )
            env = SwmmControlEnv(
                config.inp_path,
                policy_space=policy_space,
                controller_factory=lambda sched: ScheduleController(structures, sched),
                metric_reader_factory=None,
                control_interval_seconds=config.control_interval_seconds,
                **common,
            )
        elif config.env_type == "control_curve":
            from openswmm_gymnasium.control import (
                ControlCurveController,
                ControlCurveMetricReader,
            )
            from openswmm_gymnasium.spaces import ControlCurvePolicySpace

            policy_space = ControlCurvePolicySpace.from_params(
                config.policy_factory.params
            )
            env = SwmmControlEnv(
                config.inp_path,
                policy_space=policy_space,
                controller_factory=lambda policy: ControlCurveController(policy),
                metric_reader_factory=lambda policy: ControlCurveMetricReader(policy),
                control_interval_seconds=config.control_interval_seconds,
                **common,
            )
        else:  # "mo_rtc"
            env = SwmmMORTCEnv(
                config.inp_path,
                runtime_factories=runtime_factories,
                ideal_point=config.ideal_point,
                reference_point=config.reference_point,
                **common,
                **stepped,
            )
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            f"[{ErrorCode.ENGINE_ERROR}] Failed to construct '{config.env_type}' "
            f"env from '{config.inp_path}': {exc}"
        ) from exc

    for wrapper_spec in config.wrappers:
        validated = registry.validate_params("wrapper", wrapper_spec.kind, wrapper_spec.params)
        wrapper_cls = registry.resolve_kind("wrapper", wrapper_spec.kind)
        try:
            env = wrapper_cls(env, **validated.model_dump())
        except Exception as exc:
            env.close()
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Failed to apply wrapper "
                f"'{wrapper_spec.kind}': {exc}"
            ) from exc
    return env
