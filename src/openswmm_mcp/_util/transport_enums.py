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

"""Transport-configuration enum name -> engine code maps.

Covers the heat, water-age and reaction enums the ``heat_*``, ``water_age_*``
and ``reactions_*`` tools accept as tokens: ``HeatFluxModule``,
``HeatShortwaveMode``, ``HeatRadiativeParam``, ``HeatSolarParam``,
``HeatCloudParam``, ``HeatSourceKind``, ``WaterAgeSource``, ``ReactionScope``
and ``ReactionExprForm``.

Every map is **derived** from the engine's own ``IntEnum`` rather than
transcribed, for the reason ``_util.xsect_shapes`` documents: a hand-written
copy of an engine enum is a second source of truth that drifts silently, and
the drift shows up as a value written to the wrong parameter rather than as
an error. Each of these enums is pinned to its C counterpart by the engine's
``test_enum_parity``, so deriving here makes the MCP tokens transitively
pinned too.

The maps are built lazily -- ``openswmm.engine`` is imported inside
:func:`_build`, not at module scope -- so importing a tool module does not
require the compiled extension.
"""

from __future__ import annotations

from openswmm_mcp.errors import ErrorCode, ToolError


def _build() -> dict[str, dict[str, int]]:
    """Build every name -> code map from the engine's enums."""
    from openswmm.engine import (
        HeatCloudParam,
        HeatFluxModule,
        HeatRadiativeParam,
        HeatShortwaveMode,
        HeatSolarParam,
        HeatSourceKind,
        ReactionExprForm,
        ReactionScope,
        WaterAgeSource,
    )

    return {
        "heat_flux_module": {m.name.lower(): int(m) for m in HeatFluxModule},
        "heat_shortwave_mode": {m.name.lower(): int(m) for m in HeatShortwaveMode},
        "heat_radiative_param": {m.name.lower(): int(m) for m in HeatRadiativeParam},
        "heat_solar_param": {m.name.lower(): int(m) for m in HeatSolarParam},
        "heat_cloud_param": {m.name.lower(): int(m) for m in HeatCloudParam},
        "heat_source_kind": {m.name.lower(): int(m) for m in HeatSourceKind},
        "water_age_source": {m.name.lower(): int(m) for m in WaterAgeSource},
        "reaction_scope": {m.name.lower(): int(m) for m in ReactionScope},
        "reaction_expr_form": {m.name.lower(): int(m) for m in ReactionExprForm},
    }


_MAPS: dict[str, dict[str, int]] | None = None


def _codes(key: str) -> dict[str, int]:
    global _MAPS
    if _MAPS is None:
        _MAPS = _build()
    return _MAPS[key]


# ---------------------------------------------------------------------------
# Public accessors -- one per engine enum
# ---------------------------------------------------------------------------


def heat_flux_module_codes() -> dict[str, int]:
    """``HeatFluxModule`` name -> code (surface_exchange, radiative_exchange, ...)."""
    return _codes("heat_flux_module")


def heat_shortwave_mode_codes() -> dict[str, int]:
    """``HeatShortwaveMode`` name -> code (constant, timeseries, computed)."""
    return _codes("heat_shortwave_mode")


def heat_radiative_param_codes() -> dict[str, int]:
    """``HeatRadiativeParam`` name -> code (shortwave, albedo, shade_factor, ...)."""
    return _codes("heat_radiative_param")


def heat_solar_param_codes() -> dict[str, int]:
    """``HeatSolarParam`` name -> code (latitude, longitude, timezone, ...)."""
    return _codes("heat_solar_param")


def heat_cloud_param_codes() -> dict[str, int]:
    """``HeatCloudParam`` name -> code (fraction, sw_atten_k, sw_atten_n, lw_cloud_k)."""
    return _codes("heat_cloud_param")


def heat_source_kind_codes() -> dict[str, int]:
    """``HeatSourceKind`` name -> code (rainfall, dwf, gw, rdii, ...)."""
    return _codes("heat_source_kind")


def water_age_source_codes() -> dict[str, int]:
    """``WaterAgeSource`` name -> code (rainfall, dwf, gw, rdii, ...)."""
    return _codes("water_age_source")


def reaction_scope_codes() -> dict[str, int]:
    """``ReactionScope`` name -> code (term, pipe, tank)."""
    return _codes("reaction_scope")


def reaction_expr_form_codes() -> dict[str, int]:
    """``ReactionExprForm`` name -> code (none, rate, equil, formula)."""
    return _codes("reaction_expr_form")


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


def _resolve(name: str, codes: dict[str, int], label: str) -> int:
    key = str(name).strip().lower()
    if key not in codes:
        valid = ", ".join(sorted(codes))
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Unknown {label} '{name}'. Valid: {valid}.")
    return codes[key]


def resolve_heat_flux_module(name: str) -> int:
    """Resolve a heat flux-module name to its engine code."""
    return _resolve(name, heat_flux_module_codes(), "heat flux module")


def resolve_heat_shortwave_mode(name: str) -> int:
    """Resolve a shortwave-mode name to its engine code."""
    return _resolve(name, heat_shortwave_mode_codes(), "heat shortwave mode")


def resolve_heat_radiative_param(name: str) -> int:
    """Resolve a ``[RADIATIVE_FLUXES]`` parameter name to its engine code."""
    return _resolve(name, heat_radiative_param_codes(), "heat radiative parameter")


def resolve_heat_solar_param(name: str) -> int:
    """Resolve a ``[SOLAR_RADIATION]`` parameter name to its engine code."""
    return _resolve(name, heat_solar_param_codes(), "heat solar parameter")


def resolve_heat_cloud_param(name: str) -> int:
    """Resolve a ``[CLOUD_COVER]`` parameter name to its engine code."""
    return _resolve(name, heat_cloud_param_codes(), "heat cloud parameter")


def resolve_heat_source_kind(name: str) -> int:
    """Resolve a ``[HEAT_SOURCES]`` source name to its engine code."""
    return _resolve(name, heat_source_kind_codes(), "heat source kind")


def resolve_water_age_source(name: str) -> int:
    """Resolve a ``[WATER_AGE_SOURCES]`` pathway name to its engine code."""
    return _resolve(name, water_age_source_codes(), "water age source")


def resolve_reaction_scope(name: str) -> int:
    """Resolve a reaction-expression scope name to its engine code."""
    return _resolve(name, reaction_scope_codes(), "reaction scope")


def resolve_reaction_expr_form(name: str) -> int:
    """Resolve a reaction-expression form name to its engine code."""
    return _resolve(name, reaction_expr_form_codes(), "reaction expression form")


# ---------------------------------------------------------------------------
# Shared coercion (mirrors ``tools/climate.py``'s ``_coerce_enum``)
# ---------------------------------------------------------------------------


def coerce_enum(value, codes: dict[str, int], label: str) -> int:
    """Accept an int code or a case-insensitive name; return the int code."""
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {label} must be a name or int code.")
    if isinstance(value, int):
        if value not in codes.values():
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] {label} code {value} out of range. "
                f"Valid: {sorted(codes.values())}."
            )
        return value
    return _resolve(value, codes, label)


def name_for(code: int, codes: dict[str, int]) -> str:
    """Reverse-lookup an engine code to its token name (``"unknown"`` if absent)."""
    for name, value in codes.items():
        if value == code:
            return name
    return "unknown"
