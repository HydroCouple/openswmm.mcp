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

"""``[INITIAL_QUALITY]`` tools — per-element starting concentrations, ages and
temperatures.

Wraps the engine's ``InitialQuality`` view (``session.initial_quality``, C API
``openswmm_initial_quality.h``): a row-based table that seeds individual nodes
and links with a starting value, in contrast to ``pollutants_set_init_conc``
which sets one network-wide default per pollutant.

**The table is an UPSERT keyed on (element kind, element, constituent).**
Writing the same triple twice edits the existing row rather than adding a
second one, so a caller can revise a value it just wrote without first removing
anything.

**Two constituent names are reserved.** ``__WATER_AGE__`` seeds water age in
HOURS and ``__TEMPERATURE__`` seeds temperature in degrees Celsius; both accept
NEGATIVE values. Every other constituent name must be a ``[POLLUTANTS]``
pollutant, whose value is a concentration in that pollutant's own declared
units and must be NON-NEGATIVE. An unknown constituent name is refused.

**Rows only seed state at initialize().** Mutation is therefore restricted to
the editable states — ``building`` and ``opened`` — the same contract
``pollutants_set_init_conc`` carries. Writing after the engine has initialized
would store a row that nothing reads. Reads are allowed in the later states so
a caller can inspect what a running model was seeded with.

**Removal shifts indices.** ``initial_quality_remove_entry`` deletes by row
position and every later row moves down by one, so re-enumerate with
``initial_quality_list_entries`` between removals rather than working through
cached positions. Requires the ``openswmm`` backend.
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

initial_quality_mcp = FastMCP("initial_quality")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_READ_STATES = ("building", "opened", "initialized", "running", "ended")
# Rows only seed state at initialize(), so mutation is BUILDING/OPENED only.
_EDIT_STATES = ("building", "opened")


async def _readable_initial_quality(ctx: Context, session_id: str) -> tuple[SimSession, object]:
    """Return ``(session, initial_quality_view)`` for a read."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Initial quality table")
    require_state(session, *_READ_STATES)
    return session, session.initial_quality


async def _editable_initial_quality(ctx: Context, session_id: str) -> tuple[SimSession, object]:
    """Return ``(session, initial_quality_view)`` for an edit (BUILDING/OPENED)."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Initial quality editing")
    require_state(session, *_EDIT_STATES)
    return session, session.initial_quality


def _apply(fn):
    """Run a blocking closure, mapping engine refusals to ``ToolError``."""
    try:
        return fn()
    except ToolError:
        raise
    except Exception as exc:  # EngineError subclasses + ValueError from the C layer
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}") from exc


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@initial_quality_mcp.tool()
async def list_entries(ctx: Context, session_id: str = "default") -> dict:
    """List every ``[INITIAL_QUALITY]`` row in file order.

    Each row carries ``row_index`` (its 0-based position, the handle
    ``initial_quality_remove_entry`` takes), ``is_link`` (false = node row,
    true = link row), ``elem_index`` (the resolved node/link index, ``-1`` when
    the row names an element that could not be resolved), ``constituent`` and
    ``value``.

    ``value`` is raw and its unit depends on the constituent: hours for
    ``__WATER_AGE__``, degrees Celsius for ``__TEMPERATURE__``, and the
    pollutant's own declared concentration units otherwise. Also returns the
    two reserved constituent names as ``reserved_constituents`` so a caller
    need not hard-code the spellings.
    """
    # wraps: swmm_init_quality_count swmm_init_quality_get
    _, iq = await _readable_initial_quality(ctx, session_id)

    def _read() -> dict:
        rows = []
        for row_index in range(len(iq)):
            entry = iq[row_index]
            rows.append(
                {
                    "row_index": row_index,
                    "is_link": bool(entry.is_link),
                    "elem_index": int(entry.elem_index),
                    "constituent": str(entry.constituent),
                    "value": float(entry.value),
                }
            )
        return {
            "count": len(rows),
            "entries": rows,
            "reserved_constituents": {
                "water_age": str(type(iq).WATER_AGE),
                "temperature": str(type(iq).TEMPERATURE),
            },
        }

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@initial_quality_mcp.tool()
async def set_entry(
    ctx: Context,
    session_id: str = "default",
    constituent: str = "",
    value: float = 0.0,
    node_id: str | int = "",
    link_id: str | int = "",
) -> dict:
    """Seed one node or one link with a starting concentration, age or temperature.

    Provide EXACTLY ONE of ``node_id`` or ``link_id`` (either an id string or
    an index). ``constituent`` is a ``[POLLUTANTS]`` pollutant name, or one of
    the two reserved species: ``__WATER_AGE__`` (``value`` in HOURS, may be
    negative) and ``__TEMPERATURE__`` (``value`` in degrees Celsius, may be
    negative). A pollutant value is a concentration in the pollutant's own
    units and must be NON-NEGATIVE; an unknown constituent name, an
    out-of-range element, or a negative pollutant value is refused.

    This is an UPSERT on (element kind, element, constituent): writing the same
    triple again edits the existing row. Allowed in ``building`` and ``opened``
    only, because rows are consumed once at initialize().

    For a network-wide default instead of a per-element seed, use
    ``pollutants_set_init_conc``.
    """
    # wraps: swmm_init_quality_set
    if not constituent:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Provide a constituent (a pollutant name, "
            f"'__WATER_AGE__' or '__TEMPERATURE__')."
        )
    has_node = node_id != ""
    has_link = link_id != ""
    if has_node == has_link:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Provide exactly one of node_id or link_id."
        )

    _, iq = await _editable_initial_quality(ctx, session_id)
    val = float(value)

    def _set() -> None:
        if has_node:
            iq.set(constituent, val, node=node_id)
        else:
            iq.set(constituent, val, link=link_id)

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "constituent": constituent,
        "value": val,
        "is_link": has_link,
        "node_id": node_id if has_node else None,
        "link_id": link_id if has_link else None,
    }


@initial_quality_mcp.tool()
async def remove_entry(
    ctx: Context,
    session_id: str = "default",
    row_index: int = -1,
) -> dict:
    """Remove one ``[INITIAL_QUALITY]`` row by its position.

    ``row_index`` is the 0-based position reported by
    ``initial_quality_list_entries``. Subsequent rows shift DOWN by one, so
    re-enumerate between removals instead of reusing cached positions.

    Allowed in ``building`` and ``opened`` only.
    """
    # wraps: swmm_init_quality_remove
    if row_index < 0:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a non-negative row_index.")
    _, iq = await _editable_initial_quality(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: iq.remove(int(row_index)))
    return {"status": "ok", "session_id": session_id, "row_index": int(row_index)}
