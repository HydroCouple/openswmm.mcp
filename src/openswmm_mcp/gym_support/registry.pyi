"""Typed stub for L{openswmm_mcp.gym_support.registry}.

@author: Caleb Buahin
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

class _Params(BaseModel):
    """Base for per-kind param models; unknown keys are rejected."""

class FloodingVolumeParams(_Params):
    node_ids: list[str] | None
    name: str

class CSOVolumeParams(_Params):
    node_ids: list[str]
    name: str

class PeakOutflowParams(_Params):
    link_ids: list[str]
    name: str

class ReliabilityMarginParams(_Params):
    node_ids: list[str] | None
    name: str

class SetpointSmoothnessParams(_Params):
    link_ids: list[str]
    name: str

class OrificeSettingParams(_Params):
    link_ids: list[str]
    name: str

class NodeLateralInflowParams(_Params):
    node_ids: list[str]
    max_inflow: float
    name: str

class _BoundedLinkDesignParams(_Params):
    link_ids: list[str]
    low: float
    high: float

class LinkRoughnessParams(_BoundedLinkDesignParams):
    name: str

class LinkLengthParams(_BoundedLinkDesignParams):
    name: str

class LinkDiameterParams(_BoundedLinkDesignParams):
    name: str

class NodeMaxDepthParams(_Params):
    node_ids: list[str]
    low: float
    high: float
    name: str

class RecordTrajectoryParams(_Params):
    output_dir: str

class RescaleBoxActionsParams(_Params):
    src_low: float
    src_high: float

class LinearScalarizeParams(_Params):
    weights: list[float]

class TchebycheffScalarizeParams(_Params):
    weights: list[float]
    utopia: list[float]

class NoParams(_Params):
    """Empty params model for kinds that take no configuration."""

@dataclass(frozen=True)
class KindSpec:
    """One registered kind: config string -> gym class + param schema.

    @ivar kind: Registry name used in config JSON.
    @ivar category: C{"reward_term"} | C{"runtime_factory"} |
        C{"design_factory"} | C{"wrapper"}.
    @ivar import_path: C{"module:ClassName"} of the gym class.
    @ivar params_model: Pydantic model validating this kind's params.
    @ivar description: One-line summary.
    """

    kind: str
    category: str
    import_path: str
    params_model: type[_Params]
    description: str

def list_kinds(category: str | None = ...) -> list[KindSpec]:
    """Return registered kinds, optionally filtered by category.

    @param category: Category filter or C{None} for all.
    @type category: str or C{None}
    @return: Matching specs sorted by (category, kind).
    @rtype: list of L{KindSpec}
    """

def get_kind(category: str, kind: str) -> KindSpec:
    """Look up one registered kind.

    @raise ToolError: C{VALIDATION_ERROR} when unknown.
    @rtype: L{KindSpec}
    """

def validate_params(category: str, kind: str, params: dict[str, Any]) -> _Params:
    """Validate *params* against the kind's params model.

    @raise ToolError: C{VALIDATION_ERROR} on unknown kind or bad params.
    @rtype: L{_Params}
    """

def resolve_kind(category: str, kind: str) -> type:
    """Import and return the gym class behind a registered kind (lazy).

    @raise ToolError: C{VALIDATION_ERROR} or C{DEPENDENCY_MISSING}.
    @rtype: type
    """

def construct_kind(category: str, kind: str, params: dict[str, Any]) -> Any:
    """Validate params and construct the kind's gym object.

    @raise ToolError: C{VALIDATION_ERROR} (unknown kind, bad params, or
        wrapper category) or C{DEPENDENCY_MISSING}.
    @rtype: object
    """
