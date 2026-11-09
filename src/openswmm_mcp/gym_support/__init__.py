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

The environment spec (config models, kind registry, env manager) lives in
L{openswmm_gymnasium.spec}; this package adds what only the server needs:

  - L{store<openswmm_mcp.gym_support.store>} -- JSON-on-disk persistence
    of named configs.
  - L{jobs<openswmm_mcp.gym_support.jobs>} -- background optimisation jobs.
  - C{config_tools}, C{run_tools}, C{score_tools} -- the bodies of the
    C{gym_*} tools.

Imported only when the gym tool set is enabled, which needs
C{openswmm.gymnasium[spec]}.

@author: Caleb Buahin
@copyright: Copyright (c) 2026 Caleb Buahin
@license: Apache-2.0
"""

import functools

from openswmm_gymnasium.spec import (
    ActionFactorySpec,
    EnvConfig,
    KindSpec,
    ObservationSpec,
    RewardTermSpec,
    SpecError,
    WrapperSpec,
    build_env,
    get_kind,
    list_kinds,
    resolve_kind,
)

from openswmm_mcp.errors import ToolError
from openswmm_mcp.gym_support.store import GymStore


def tool_errors(fn):
    """Surface spec errors as MCP tool errors; their messages already carry the code."""

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except SpecError as exc:
            raise ToolError(str(exc)) from exc

    return wrapper


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
    "tool_errors",
]
