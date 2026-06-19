"""Typed stub for L{openswmm_mcp.gym_support.config}.

@author: Caleb Buahin
"""

from typing import Any, ClassVar, Literal

from pydantic import BaseModel

class ObservationSpec(BaseModel):
    """Declarative observation feature set.

    @ivar include_clock: Whether to append simulation-clock features.
    """

    node_depths: list[str]
    node_heads: list[str]
    node_inflows: list[str]
    node_overflows: list[str]
    node_volumes: list[str]
    node_lateral_inflows: list[str]
    link_flows: list[str]
    link_depths: list[str]
    link_settings: list[str]
    link_velocities: list[str]
    link_capacities: list[str]
    link_volumes: list[str]
    subcatch_runoff: list[str]
    rainfall_gages: list[str]
    include_clock: bool

    def is_empty(self) -> bool:
        """Return C{True} when no feature at all is configured.

        @rtype: bool
        """

    def build(self) -> Any:
        """Construct the C{ObservationBuilder} this spec describes.

        @raise ToolError: C{DEPENDENCY_MISSING} without the gym extra.
        @rtype: C{openswmm_gymnasium.observations.ObservationBuilder}
        """

class _KindSpecModel(BaseModel):
    """C{kind}+C{params} pair validated against the kind registry.

    @ivar kind: Registry name (see C{gym_list_capabilities}).
    @ivar params: Kind-specific constructor params.
    """

    kind: str
    params: dict[str, Any]
    _category: ClassVar[str]

    def construct(self) -> Any:
        """Construct the gym object this spec describes (lazy import).

        @rtype: object
        """

class RewardTermSpec(_KindSpecModel): ...
class RuntimeFactorySpec(_KindSpecModel): ...
class DesignFactorySpec(_KindSpecModel): ...
class WrapperSpec(_KindSpecModel): ...

ActionFactorySpec = RuntimeFactorySpec | DesignFactorySpec

class EnvConfig(BaseModel):
    """Declarative, JSON-serializable environment configuration.

    @ivar env_type: C{"rtc"} | C{"cip"} | C{"joint"} | C{"mo_rtc"}.
    @ivar inp_path: Path to the SWMM C{.inp} driving each episode.
    """

    env_type: Literal["rtc", "cip", "joint", "mo_rtc"]
    inp_path: str
    runtime_factories: list[RuntimeFactorySpec]
    design_factories: list[DesignFactorySpec]
    observations: ObservationSpec
    reward_terms: list[RewardTermSpec]
    control_interval_steps: int
    max_episode_steps: int | None
    ideal_point: list[float] | None
    reference_point: list[float] | None
    wrappers: list[WrapperSpec]
    rpt_path: str | None
    out_path: str | None

def build_env(config: EnvConfig) -> Any:
    """Construct a live, wrapped C{gymnasium.Env} from *config*.

    The returned env is unreset; callers own its lifecycle and must
    C{close()} it.

    @param config: A validated environment config.
    @type config: L{EnvConfig}
    @raise ToolError: C{DEPENDENCY_MISSING} without the gym extra;
        C{ENGINE_ERROR} when construction fails;
        C{VALIDATION_ERROR} when a wrapper cannot be applied.
    @return: The constructed env with wrappers applied in list order.
    @rtype: C{gymnasium.Env}
    """
