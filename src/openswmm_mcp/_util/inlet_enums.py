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
"""Street-inlet enum name -> engine code maps.

Covers the enums the inlet-design, inlet-usage and inlet-junction tools
accept as tokens: ``InletType``, ``GrateType``, ``ThroatType``,
``InletCurveKind``, ``InletHostKind`` and ``InletPlacement``.

Every map is **derived** from the engine's own ``IntEnum`` (see
``_util.transport_enums`` for why a transcribed copy is worse), and built
lazily so importing a tool module does not require the compiled extension.
Token names are the enum member names lower-cased (``"curved_vane"``,
``"on_grade"``, ``"drop_curb"``, ...); an int code is accepted as well.
"""

from __future__ import annotations

from openswmm_mcp._util.transport_enums import coerce_enum, name_for

__all__ = [
    "coerce_enum",
    "name_for",
    "inlet_type_codes",
    "grate_type_codes",
    "throat_type_codes",
    "inlet_curve_kind_codes",
    "inlet_host_kind_codes",
    "inlet_placement_codes",
]


def _build() -> dict[str, dict[str, int]]:
    """Build every name -> code map from the engine's enums."""
    from openswmm.engine import (
        GrateType,
        InletCurveKind,
        InletHostKind,
        InletPlacement,
        InletType,
        ThroatType,
    )

    return {
        "inlet_type": {m.name.lower(): int(m) for m in InletType},
        "grate_type": {m.name.lower(): int(m) for m in GrateType},
        "throat_type": {m.name.lower(): int(m) for m in ThroatType},
        "inlet_curve_kind": {m.name.lower(): int(m) for m in InletCurveKind},
        "inlet_host_kind": {m.name.lower(): int(m) for m in InletHostKind},
        "inlet_placement": {m.name.lower(): int(m) for m in InletPlacement},
    }


_MAPS: dict[str, dict[str, int]] | None = None


def _codes(key: str) -> dict[str, int]:
    global _MAPS
    if _MAPS is None:
        _MAPS = _build()
    return dict(_MAPS[key])


def inlet_type_codes() -> dict[str, int]:
    """``InletType`` tokens: grate, curb, combo, slotted, drop_grate, drop_curb, custom."""
    return _codes("inlet_type")


def grate_type_codes() -> dict[str, int]:
    """``GrateType`` tokens (p_bar_50, curved_vane, reticuline, generic, ...)."""
    return _codes("grate_type")


def throat_type_codes() -> dict[str, int]:
    """``ThroatType`` tokens: horizontal, inclined, vertical."""
    return _codes("throat_type")


def inlet_curve_kind_codes() -> dict[str, int]:
    """``InletCurveKind`` tokens: none, diversion, rating."""
    return _codes("inlet_curve_kind")


def inlet_host_kind_codes() -> dict[str, int]:
    """``InletHostKind`` tokens: link (a conduit's ``[INLET_USAGE]`` row), node (inlet junction)."""
    return _codes("inlet_host_kind")


def inlet_placement_codes() -> dict[str, int]:
    """``InletPlacement`` tokens: automatic, on_grade, on_sag."""
    return _codes("inlet_placement")
