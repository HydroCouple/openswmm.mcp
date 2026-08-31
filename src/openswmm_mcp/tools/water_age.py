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

"""Water-age source-table tools — the age carried by each inflow pathway.

Wraps the engine's ``WaterAge`` view (``session.water_age``, C API
``openswmm_water_age.h``): the ``[WATER_AGE_SOURCES]`` table, which assigns an
age to the water entering the network by each pathway, plus per-node overrides
for the two pathways that take them.

**Ages are HOURS** — the config file's own unit, not seconds and not days.

**Negative hours are legal, and mean something.** A negative source age
EXTRACTS age-volume rather than adding it, letting a source pull the mixed age
down; the result is clamped so a computed age never goes below zero. This is a
modelling device, not an error to guard against, so these tools pass negatives
straight through.

**Edits are LIVE.** The loaders re-read the table every routing step, so these
tools work in ``opened``, ``initialized``, ``running`` and ``ended`` states and
a mid-simulation edit takes effect on the next step.

**Only ``dwf`` and ``external_inflow`` take per-node overrides.** Every other
pathway refuses NODE scope, exactly as the parser does — refused rather than
silently collapsed to the global value.

Whether water age is computed at all is the ``[OPTIONS] WATER_AGE`` switch,
reported as ``enabled`` by ``water_age_get_config``. Initial per-element ages
are seeded through ``[INITIAL_QUALITY]``'s reserved ``__WATER_AGE__``
constituent — see ``initial_quality_set_entry``. Requires the ``openswmm``
backend.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP

from openswmm_mcp._util.transport_enums import (
    coerce_enum,
    name_for,
    water_age_source_codes,
)
from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

logger = logging.getLogger(__name__)

water_age_mcp = FastMCP("water_age")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Water-age edits are documented as LIVE (the loaders re-read the table every
# routing step), so writes are not narrowed to "opened".
_AGE_STATES = ("opened", "initialized", "running", "ended")


async def _editable_water_age(ctx: Context, session_id: str) -> tuple[SimSession, object]:
    """Return ``(session, water_age_view)`` for a read or a live edit."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Water-age source configuration")
    require_state(session, *_AGE_STATES)
    return session, session.water_age


def _apply(fn):
    """Run a blocking water-age closure, mapping engine refusals to ``ToolError``."""
    try:
        return fn()
    except ToolError:
        raise
    except Exception as exc:  # EngineError subclasses + ValueError from the C layer
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}") from exc


# ---------------------------------------------------------------------------
# Global source table
# ---------------------------------------------------------------------------


@water_age_mcp.tool()
async def get_config(ctx: Context, session_id: str = "default") -> dict:
    """Read the water-age switch and the global age of every source pathway.

    ``enabled`` is the ``[OPTIONS] WATER_AGE`` switch — when it is false the
    table is still editable but nothing consumes it.

    ``sources`` carries one row per pathway with ``hours``: ``rainfall``,
    ``dwf``, ``gw``, ``rdii``, ``external_inflow``, ``iface`` (interface file)
    and ``initial_state`` (water present at t=0). Values are HOURS and may be
    negative (age extraction, clamped at zero age).

    Per-node overrides are listed separately by ``water_age_list_node_overrides``.
    """
    # wraps: swmm_water_age_get_enabled swmm_water_age_get_global_source
    _, age = await _editable_water_age(ctx, session_id)
    codes = water_age_source_codes()

    def _read() -> dict:
        globals_view = age.globals
        rows = [
            {
                "source": name,
                "source_code": code,
                "hours": float(globals_view[code]),
            }
            for name, code in sorted(codes.items(), key=lambda kv: kv[1])
        ]
        return {"enabled": bool(age.enabled), "count": len(rows), "sources": rows}

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@water_age_mcp.tool()
async def set_global_source(
    ctx: Context,
    session_id: str = "default",
    source: str | int = "",
    hours: float = 0.0,
) -> dict:
    """Set the GLOBAL age (hours) carried by one inflow pathway.

    ``source`` is a name or int code (see ``water_age_get_config``). ``hours``
    is in HOURS and MAY BE NEGATIVE: a negative age extracts age-volume instead
    of adding it, with the result clamped so age never goes below zero.

    Writing marks the configuration present so the engine consumes it. The edit
    is live and takes effect on the next routing step. Per-node values override
    this global one for the ``dwf`` and ``external_inflow`` pathways — see
    ``water_age_set_node_override``.
    """
    # wraps: swmm_water_age_set_global_source
    _, age = await _editable_water_age(ctx, session_id)
    codes = water_age_source_codes()
    code = coerce_enum(source, codes, "water age source")
    val = float(hours)

    def _set() -> None:
        age.globals[code] = val

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "source": name_for(code, codes),
        "source_code": code,
        "hours": val,
    }


# ---------------------------------------------------------------------------
# Per-node overrides
# ---------------------------------------------------------------------------


@water_age_mcp.tool()
async def list_node_overrides(ctx: Context, session_id: str = "default") -> dict:
    """List every per-node water-age override row.

    Each row carries ``source`` / ``source_code``, ``node_index`` and ``hours``.
    Row order is stable across edits within a session. Unlike the heat table,
    removal here is keyed on (source, node) rather than row position — see
    ``water_age_remove_node_override``.
    """
    # wraps: swmm_water_age_override_count swmm_water_age_get_override
    _, age = await _editable_water_age(ctx, session_id)
    codes = water_age_source_codes()

    def _read() -> dict:
        overrides = age.node_overrides
        rows = []
        for row_index in range(len(overrides)):
            row = overrides[row_index]
            rows.append(
                {
                    "row_index": row_index,
                    "source": name_for(int(row.source), codes),
                    "source_code": int(row.source),
                    "node_index": int(row.node_index),
                    "hours": float(row.hours),
                }
            )
        return {"count": len(rows), "overrides": rows}

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@water_age_mcp.tool()
async def set_node_override(
    ctx: Context,
    session_id: str = "default",
    source: str | int = "",
    node_id: str | int = "",
    hours: float = 0.0,
) -> dict:
    """Add or update the water age (hours) for one pathway at one node.

    Only ``dwf`` and ``external_inflow`` take NODE scope; every other pathway
    is refused outright, exactly as the parser refuses it. ``node_id`` may be a
    node id string or an index.

    ``hours`` is in HOURS and may be negative (age extraction, clamped at zero
    age). An existing (source, node) pair is updated rather than duplicated.
    The edit is live and takes effect on the next routing step.
    """
    # wraps: swmm_water_age_set_override
    _, age = await _editable_water_age(ctx, session_id)
    codes = water_age_source_codes()
    code = coerce_enum(source, codes, "water age source")
    if node_id == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a node_id.")
    val = float(hours)

    def _set() -> None:
        age.node_overrides.set(code, node_id, val)

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "source": name_for(code, codes),
        "source_code": code,
        "node_id": node_id,
        "hours": val,
    }


@water_age_mcp.tool()
async def remove_node_override(
    ctx: Context,
    session_id: str = "default",
    source: str | int = "",
    node_id: str | int = "",
) -> dict:
    """Remove the water-age override for one (source, node) pair.

    Keyed on the pair, not on a row position — pass the same ``source`` and
    ``node_id`` that ``water_age_set_node_override`` was called with. Removing
    a pair that has no override row is refused rather than silently ignored.
    The pathway falls back to its global age afterwards.
    """
    # wraps: swmm_water_age_remove_override
    _, age = await _editable_water_age(ctx, session_id)
    codes = water_age_source_codes()
    code = coerce_enum(source, codes, "water age source")
    if node_id == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a node_id.")

    def _remove() -> None:
        age.node_overrides.remove(code, node_id)

    await asyncio.to_thread(_apply, _remove)
    return {
        "status": "ok",
        "session_id": session_id,
        "source": name_for(code, codes),
        "source_code": code,
        "node_id": node_id,
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


@water_age_mcp.tool()
async def save_sources(
    ctx: Context,
    session_id: str = "default",
    path: str = "",
) -> dict:
    """Write the current table to a ``[WATER_AGE_SOURCES]`` component file.

    Produces the component-file format the water-age component parses back, so
    the file can be referenced from ``[PROCESS_COMPONENTS]`` (see
    ``process_components_register_component``). This writes the source table
    only — it is not a model save, and the parent ``.inp`` is untouched.
    """
    # wraps: swmm_water_age_save
    if not path:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide an output path.")
    _, age = await _editable_water_age(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: age.save(path))
    return {"status": "ok", "session_id": session_id, "path": path}
