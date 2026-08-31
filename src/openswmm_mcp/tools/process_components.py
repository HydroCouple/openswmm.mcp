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

"""``[PROCESS_COMPONENTS]`` tools — which process components a model loads, and
from which config file.

Wraps the engine's ``ProcessComponents`` view (``session.process_components``,
C API ``openswmm_process_components.h``). A process component is a named
sub-model (heat, water age, reactions, …) that the model registers and points
at a config file; this namespace is the file-binding surface for those
registrations.

Each registration reports three strings, and the difference between the last
two is the point of the surface:

* ``id`` — the component id, unique within the model.
* ``config`` — the ``config="…"`` argument exactly as written, which may be
  empty and may be relative.
* ``resolved`` — the effective path the config was actually READ from at the
  last open, or empty until resolution has happened. When a component is not
  behaving as expected, ``resolved`` is what says which file it really loaded.

**A config path need not exist yet.** Registering a component that points at a
file you have not written is legal and is the intended order for the
"create component, then write its config" flow: register first, write the file
second, and the path is resolved at the next open. So an empty ``resolved`` on
a freshly registered component is expected, not a failure.

Registration and removal are BUILDING/OPENED only, and a duplicate id is
refused. Removal shifts later indices down, so re-enumerate with
``process_components_list_components`` between removals. Requires the
``openswmm`` backend.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP

from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

logger = logging.getLogger(__name__)

process_components_mcp = FastMCP("process_components")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_READ_STATES = ("building", "opened", "initialized", "running", "ended")
_EDIT_STATES = ("building", "opened")


async def _readable_components(ctx: Context, session_id: str) -> tuple[SimSession, object]:
    """Return ``(session, process_components_view)`` for a read."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Process component registrations")
    require_state(session, *_READ_STATES)
    return session, session.process_components


async def _editable_components(ctx: Context, session_id: str) -> tuple[SimSession, object]:
    """Return ``(session, process_components_view)`` for an edit (BUILDING/OPENED)."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Process component registration editing")
    require_state(session, *_EDIT_STATES)
    return session, session.process_components


def _apply(fn):
    """Run a blocking closure, mapping engine refusals to ``ToolError``."""
    try:
        return fn()
    except ToolError:
        raise
    except Exception as exc:  # EngineError subclasses + ValueError from the C layer
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}") from exc


def _row(component) -> dict:
    return {
        "index": int(component.component_index),
        "id": str(component.id),
        "config": str(component.config),
        "resolved": str(component.resolved),
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@process_components_mcp.tool()
async def list_components(ctx: Context, session_id: str = "default") -> dict:
    """List every ``[PROCESS_COMPONENTS]`` registration.

    Each row carries ``index``, ``id``, ``config`` (the argument as written,
    possibly empty or relative) and ``resolved`` (the path the config was
    actually read from at the last open, empty until resolution). Compare the
    two when a component is not loading what you expect.

    Indices are positional and shift when a registration is removed.
    """
    # wraps: swmm_process_component_count swmm_process_component_get
    _, components = await _readable_components(ctx, session_id)

    def _read() -> dict:
        rows = [_row(components[i]) for i in range(len(components))]
        return {"count": len(rows), "components": rows}

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@process_components_mcp.tool()
async def find_component(
    ctx: Context,
    session_id: str = "default",
    component_id: str = "",
) -> dict:
    """Look up one registration by component id.

    Returns ``found``, and when found the same ``index`` / ``id`` / ``config``
    / ``resolved`` fields ``process_components_list_components`` reports. A
    missing id returns ``found: false`` with ``index: -1`` rather than raising,
    so this is safe to call as an existence check before registering.
    """
    # wraps: swmm_process_component_find
    if not component_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a component_id.")
    _, components = await _readable_components(ctx, session_id)

    def _read() -> dict:
        if component_id not in components:
            return {"found": False, "index": -1}
        out = _row(components[component_id])
        out["found"] = True
        return out

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    out["component_id"] = component_id
    return out


@process_components_mcp.tool()
async def register_component(
    ctx: Context,
    session_id: str = "default",
    component_id: str = "",
    config_path: str = "",
) -> dict:
    """Register a process component and bind it to a config file.

    ``config_path`` NEED NOT EXIST yet — that is the intended order for the
    "create component, then write its config" flow: register first, write the
    file second, and the path is resolved at the next open. The returned
    ``resolved`` is therefore normally empty on a fresh registration.

    A duplicate ``component_id`` is refused; check first with
    ``process_components_find_component``. Allowed in ``building`` and
    ``opened`` only.
    """
    # wraps: swmm_process_component_register
    if not component_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a component_id.")
    _, components = await _editable_components(ctx, session_id)

    def _register() -> dict:
        return _row(components.register(component_id, config_path))

    out = await asyncio.to_thread(_apply, _register)
    out["status"] = "ok"
    out["session_id"] = session_id
    return out


@process_components_mcp.tool()
async def remove_component(
    ctx: Context,
    session_id: str = "default",
    component_id: str | int = "",
) -> dict:
    """Remove one ``[PROCESS_COMPONENTS]`` registration.

    ``component_id`` may be the component id string or its integer index.
    Later registrations shift DOWN by one index, so re-enumerate with
    ``process_components_list_components`` between removals rather than
    working through cached indices.

    Removing the registration unbinds the config file; it does not delete the
    file. Allowed in ``building`` and ``opened`` only.
    """
    # wraps: swmm_process_component_remove
    if component_id == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a component_id.")
    _, components = await _editable_components(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: components.remove(component_id))
    return {"status": "ok", "session_id": session_id, "component_id": component_id}
