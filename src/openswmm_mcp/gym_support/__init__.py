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

"""Support package for the C{gym} MCP tool domain.

Bridges declarative JSON configs (composed by an LLM through the
C{gym_*} tools) to live C{openswmm.gymnasium} objects. Submodules:

  - L{registry<openswmm_mcp.gym_support.registry>} — kind-name registry
    mapping config strings to gym classes, with per-kind param schemas.
  - L{config<openswmm_mcp.gym_support.config>} — Pydantic config models
    (L{EnvConfig<openswmm_mcp.gym_support.config.EnvConfig>}) and
    L{build_env<openswmm_mcp.gym_support.config.build_env>}.
  - L{store<openswmm_mcp.gym_support.store>} — JSON-on-disk persistence
    of named configs.

C{openswmm_gymnasium} is imported lazily inside functions only, so this
package (and the server) imports cleanly without the C{gym} extra.
See C{docs/developer/GYMNASIUM_INTEGRATION_PLAN.md}.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: Apache-2.0
"""

from openswmm_mcp.gym_support.config import (
    ActionFactorySpec,
    EnvConfig,
    ObservationSpec,
    RewardTermSpec,
    WrapperSpec,
    build_env,
)
from openswmm_mcp.gym_support.registry import (
    KindSpec,
    get_kind,
    list_kinds,
    resolve_kind,
)
from openswmm_mcp.gym_support.store import GymStore

__all__ = [
    "ActionFactorySpec",
    "EnvConfig",
    "GymStore",
    "KindSpec",
    "ObservationSpec",
    "RewardTermSpec",
    "WrapperSpec",
    "build_env",
    "get_kind",
    "list_kinds",
    "resolve_kind",
]
