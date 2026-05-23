"""Nodes tools: fine-grained accessors beyond ``query.get_node_info``.

The :func:`query.get_node_info` tool already returns a batched property
dict for any node. This module adds individual tools for use cases the
aggregate getter doesn't serve well:

* **Statistics** — post-simulation peak / duration metrics (4 tools).
* **Bulk array accessors** — read/write all-node arrays in a single call
  (7 tools). Critical for visualization and batched edits.
* **Storage-node subtype** — curve / functional / seep / exfiltration
  (8 tools, getter+setter pairs).
* **Outfall-node subtype** — type / stage / tidal / timeseries / flap_gate
  / route_to (8 tools).
* **Divider-node subtype** — type get/set (2 tools).
* **Quality** — point-wise pollutant getter + mass-flux injection setter
  (2 tools).
* **Conversion** — depth-from-volume inverse lookup (1 tool).

Tools accept either an integer node index or a string node ID; resolution
is done via ``session.nodes.get_index`` so unknown IDs surface as
``ELEMENT_NOT_FOUND``.

The basic property pairs (invert_elev / max_depth / depth / head /
volume / lateral_inflow / inflow / outflow / overflow / losses / degree
/ full_volume / crown_elev / initial_depth / surcharge_depth /
ponded_area / type) are intentionally NOT duplicated here — those are
already covered by ``query.get_node_info`` (read) and
``editing.set_node_properties`` (write).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastmcp import Context, FastMCP

from openswmm_mcp._util.formatting import ndarray_to_list
from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

nodes_mcp = FastMCP("nodes")


# ---------------------------------------------------------------------------
# Enums (string -> engine integer code)
# ---------------------------------------------------------------------------

_OUTFALL_TYPES: dict[str, int] = {
    "free": 0,
    "normal": 1,
    "fixed": 2,
    "tidal": 3,
    "timeseries": 4,
}

# DividerType enum codes per the engine (CUTOFF, OVERFLOW, TABULAR, WEIR).
_DIVIDER_TYPES: dict[str, int] = {
    "cutoff": 0,
    "overflow": 1,
    "tabular": 2,
    "weir": 3,
}


def _resolve_outfall_type(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower()
    if key not in _OUTFALL_TYPES:
        valid = ", ".join(sorted(_OUTFALL_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown outfall_type '{name}'. Valid types: {valid}."
        )
    return _OUTFALL_TYPES[key]


def _resolve_divider_type(name: str | int) -> int:
    if isinstance(name, int):
        return name
    key = name.strip().lower()
    if key not in _DIVIDER_TYPES:
        valid = ", ".join(sorted(_DIVIDER_TYPES))
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown divider_type '{name}'. Valid types: {valid}."
        )
    return _DIVIDER_TYPES[key]


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Nodes (fine-grained accessors)")
    return session


async def _resolve_node(session: SimSession, node_id: str | int) -> int:
    if isinstance(node_id, int):
        return node_id
    if not node_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] node_id must not be empty.")
    idx = await asyncio.to_thread(session.nodes.get_index, node_id)
    if idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Node '{node_id}' not found.")
    return idx


# ===========================================================================
# Statistics (post-simulation peaks / durations)
# ===========================================================================


@nodes_mcp.tool()
async def stat_max_depth(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the peak depth recorded for a node over the simulation."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    value = await asyncio.to_thread(session.nodes.get_stat_max_depth, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "max_depth": value,
    }


@nodes_mcp.tool()
async def stat_max_overflow(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the peak overflow rate for a node over the simulation."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    value = await asyncio.to_thread(session.nodes.get_stat_max_overflow, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "max_overflow": value,
    }


@nodes_mcp.tool()
async def stat_vol_flooded(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the total flooded volume for a node over the simulation."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    value = await asyncio.to_thread(session.nodes.get_stat_vol_flooded, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "vol_flooded": value,
    }


@nodes_mcp.tool()
async def stat_time_flooded(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the total flooded duration (hours) for a node."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    value = await asyncio.to_thread(session.nodes.get_stat_time_flooded, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "time_flooded_hours": value,
    }


# ===========================================================================
# Bulk array readers (all nodes, one variable)
# ===========================================================================


def _zip_node_results(session: SimSession, values: list[float]) -> list[dict[str, Any]]:
    """Build [{id, index, value}, ...] records from a bulk array."""
    n = session.nodes.count()
    return [
        {
            "id": session.nodes.get_id(i),
            "index": i,
            "value": values[i],
        }
        for i in range(min(n, len(values)))
    ]


@nodes_mcp.tool()
async def get_depths_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current depths for all nodes as a list of {id, index, value}."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.nodes.get_depths_bulk)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_node_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@nodes_mcp.tool()
async def get_heads_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current heads for all nodes as a list of {id, index, value}."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.nodes.get_heads_bulk)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_node_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@nodes_mcp.tool()
async def get_inflows_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current total inflows for all nodes."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.nodes.get_inflows_bulk)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_node_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@nodes_mcp.tool()
async def get_overflows_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current overflow rates for all nodes."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.nodes.get_overflows_bulk)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_node_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@nodes_mcp.tool()
async def get_quality_bulk(
    ctx: Context, session_id: str = "default", pollutant_index: int = 0
) -> dict:
    """Return pollutant concentrations at all nodes for one pollutant.

    ``pollutant_index`` is a 0-based pollutant index (see
    ``query.get_pollutant_info`` or ``analysis.output_pollutant_count``).
    """
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.nodes.get_quality_bulk, pollutant_index)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_node_results, session, values)
    return {
        "session_id": session_id,
        "pollutant_index": pollutant_index,
        "count": len(records),
        "results": records,
    }


@nodes_mcp.tool()
async def set_depths_bulk(
    ctx: Context,
    session_id: str = "default",
    depths: list[float] | None = None,
) -> dict:
    """Set depths for all nodes from an array (length must equal node count).

    Use case: initialize a hot-start or override an entire depth field
    before a step. The array is positional — index ``i`` maps to node
    ``i`` in storage order (see ``query.list_nodes`` for the canonical
    order).
    """
    if not depths:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] depths must be non-empty.")
    session = await _get_session(ctx, session_id)
    n = await asyncio.to_thread(session.nodes.count)
    if len(depths) != n:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] depths length {len(depths)} != node count {n}."
        )
    import numpy as np

    arr = np.asarray(depths, dtype=np.float64)
    await asyncio.to_thread(session.nodes.set_depths_bulk, arr)
    return {"status": "ok", "session_id": session_id, "count": len(depths)}


@nodes_mcp.tool()
async def set_lat_inflows_bulk(
    ctx: Context,
    session_id: str = "default",
    inflows: list[float] | None = None,
) -> dict:
    """Set lateral inflows for all nodes from an array.

    Positional, length must equal node count.
    """
    if not inflows:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] inflows must be non-empty.")
    session = await _get_session(ctx, session_id)
    n = await asyncio.to_thread(session.nodes.count)
    if len(inflows) != n:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] inflows length {len(inflows)} != node count {n}."
        )
    import numpy as np

    arr = np.asarray(inflows, dtype=np.float64)
    await asyncio.to_thread(session.nodes.set_lat_inflows_bulk, arr)
    return {"status": "ok", "session_id": session_id, "count": len(inflows)}


# ===========================================================================
# Storage-node subtype config
# ===========================================================================


@nodes_mcp.tool()
async def get_storage_curve(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the storage-curve index assigned to a storage node."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    curve_idx = await asyncio.to_thread(session.nodes.get_storage_curve, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "curve_index": curve_idx,
    }


@nodes_mcp.tool()
async def set_storage_curve(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    curve_index: int = 0,
) -> dict:
    """Assign a storage curve to a storage node.

    ``curve_index`` references a curve defined via ``tables.add_curve``
    (with ``curve_type='storage'``).
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_storage_curve, idx, curve_index)
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "curve_index": curve_index,
    }


@nodes_mcp.tool()
async def get_storage_functional(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return functional storage params ``(a, b, c)`` for a storage node.

    Functional form: ``area = a * depth^b + c``.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    a, b, c = await asyncio.to_thread(session.nodes.get_storage_functional, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "a": a,
        "b": b,
        "c": c,
    }


@nodes_mcp.tool()
async def set_storage_functional(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    a: float = 0.0,
    b: float = 0.0,
    c: float = 0.0,
) -> dict:
    """Set functional storage params ``area = a * depth^b + c``."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_storage_functional, idx, float(a), float(b), float(c))
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "a": a,
        "b": b,
        "c": c,
    }


@nodes_mcp.tool()
async def get_storage_seep_rate(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the seepage rate for a storage node (depth/time)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    rate = await asyncio.to_thread(session.nodes.get_storage_seep_rate, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "seep_rate": rate,
    }


@nodes_mcp.tool()
async def set_storage_seep_rate(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    rate: float = 0.0,
) -> dict:
    """Set the seepage rate for a storage node."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_storage_seep_rate, idx, float(rate))
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "seep_rate": rate,
    }


@nodes_mcp.tool()
async def get_exfil_params(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return Green-Ampt exfiltration params ``(suction, ksat, imd)`` for a storage node."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    suction, ksat, imd = await asyncio.to_thread(session.nodes.get_exfil_params, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "suction": suction,
        "ksat": ksat,
        "imd": imd,
    }


@nodes_mcp.tool()
async def set_exfil_params(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    suction: float = 0.0,
    ksat: float = 0.0,
    imd: float = 0.0,
) -> dict:
    """Set Green-Ampt exfiltration params for a storage node.

    Parameters
    ----------
    suction:
        Suction head at the wetting front.
    ksat:
        Saturated hydraulic conductivity.
    imd:
        Initial moisture deficit.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(
        session.nodes.set_exfil_params,
        idx,
        float(suction),
        float(ksat),
        float(imd),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "suction": suction,
        "ksat": ksat,
        "imd": imd,
    }


# ===========================================================================
# Outfall-node subtype config
# ===========================================================================


@nodes_mcp.tool()
async def get_outfall_type(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the outfall boundary type code for an outfall node.

    ``outfall_type`` enum: 0=FREE, 1=NORMAL, 2=FIXED, 3=TIDAL, 4=TIMESERIES.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    type_code = await asyncio.to_thread(session.nodes.get_outfall_type, idx)
    name_map = {0: "free", 1: "normal", 2: "fixed", 3: "tidal", 4: "timeseries"}
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "outfall_type_code": type_code,
        "outfall_type": name_map.get(type_code, "unknown"),
    }


@nodes_mcp.tool()
async def set_outfall_type(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    outfall_type: str = "free",
) -> dict:
    """Set the outfall boundary type for an outfall node.

    ``outfall_type``: ``free`` / ``normal`` / ``fixed`` / ``tidal`` /
    ``timeseries`` or the integer code (0..4).
    """
    type_int = _resolve_outfall_type(outfall_type)
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_outfall_type, idx, type_int)
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "outfall_type": outfall_type,
    }


@nodes_mcp.tool()
async def get_outfall_param(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the outfall parameter value (fixed stage or computed param).

    Meaning depends on the outfall type: for FIXED it's the stage
    elevation; for TIDAL / TIMESERIES it's an index into a curve / series.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    value = await asyncio.to_thread(session.nodes.get_outfall_param, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "param": value,
    }


@nodes_mcp.tool()
async def set_outfall_stage(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    stage: float = 0.0,
) -> dict:
    """Set the fixed stage elevation for a FIXED outfall."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_outfall_stage, idx, float(stage))
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "stage": stage,
    }


@nodes_mcp.tool()
async def set_outfall_tidal(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    curve_index: int = 0,
) -> dict:
    """Assign a tidal curve to a TIDAL outfall (hour-of-day vs stage)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_outfall_tidal, idx, curve_index)
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "curve_index": curve_index,
    }


@nodes_mcp.tool()
async def set_outfall_timeseries(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    timeseries_index: int = 0,
) -> dict:
    """Assign a time series to a TIMESERIES outfall (time vs stage)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_outfall_timeseries, idx, timeseries_index)
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "timeseries_index": timeseries_index,
    }


@nodes_mcp.tool()
async def get_outfall_flap_gate(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return whether an outfall has a flap gate (prevents backflow)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    has_gate = await asyncio.to_thread(session.nodes.get_outfall_flap_gate, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "has_flap_gate": bool(has_gate),
    }


@nodes_mcp.tool()
async def set_outfall_flap_gate(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    has_gate: bool = False,
) -> dict:
    """Set whether an outfall has a flap gate."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_outfall_flap_gate, idx, has_gate)
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "has_flap_gate": has_gate,
    }


@nodes_mcp.tool()
async def get_outfall_route_to(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the subcatchment index outfall discharge is routed to (-1 = none)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    target = await asyncio.to_thread(session.nodes.get_outfall_route_to, idx)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "route_to_subcatch": target,
    }


@nodes_mcp.tool()
async def set_outfall_route_to(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    subcatch_index: int = -1,
) -> dict:
    """Route outfall discharge to a subcatchment (``-1`` = none)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_outfall_route_to, idx, subcatch_index)
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "route_to_subcatch": subcatch_index,
    }


# ===========================================================================
# Divider-node subtype config
# ===========================================================================


@nodes_mcp.tool()
async def get_divider_type(
    ctx: Context, session_id: str = "default", node_id: str | int = ""
) -> dict:
    """Return the divider rule type for a divider node.

    ``divider_type`` enum: 0=CUTOFF, 1=OVERFLOW, 2=TABULAR, 3=WEIR.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    type_code = await asyncio.to_thread(session.nodes.get_divider_type, idx)
    name_map = {0: "cutoff", 1: "overflow", 2: "tabular", 3: "weir"}
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "divider_type_code": type_code,
        "divider_type": name_map.get(type_code, "unknown"),
    }


@nodes_mcp.tool()
async def set_divider_type(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    divider_type: str = "cutoff",
) -> dict:
    """Set the divider rule type for a divider node.

    ``divider_type``: ``cutoff`` / ``overflow`` / ``tabular`` / ``weir`` or
    the integer code (0..3).
    """
    type_int = _resolve_divider_type(divider_type)
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(session.nodes.set_divider_type, idx, type_int)
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "divider_type": divider_type,
    }


# ===========================================================================
# Quality + conversion
# ===========================================================================


@nodes_mcp.tool()
async def get_quality(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    pollutant_index: int = 0,
) -> dict:
    """Return the current concentration of a pollutant at a node."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    conc = await asyncio.to_thread(session.nodes.get_quality, idx, pollutant_index)
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "pollutant_index": pollutant_index,
        "concentration": conc,
    }


@nodes_mcp.tool()
async def set_quality_mass_flux(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    pollutant_index: int = 0,
    mass_flux: float = 0.0,
) -> dict:
    """Inject a persistent pollutant mass flux at a node (mass/sec).

    Runs against the running simulation; persists until cleared.
    """
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    idx = await _resolve_node(session, node_id)
    await asyncio.to_thread(
        session.nodes.set_quality_mass_flux,
        idx,
        pollutant_index,
        float(mass_flux),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "pollutant_index": pollutant_index,
        "mass_flux": mass_flux,
    }


@nodes_mcp.tool()
async def depth_from_volume(
    ctx: Context,
    session_id: str = "default",
    node_id: str | int = "",
    volume: float = 0.0,
) -> dict:
    """Compute the depth corresponding to a given storage volume.

    Inverts the storage curve / functional relationship for a storage node.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_node(session, node_id)
    depth = await asyncio.to_thread(session.nodes.get_depth_from_volume, idx, float(volume))
    return {
        "session_id": session_id,
        "node_id": node_id,
        "node_index": idx,
        "volume": volume,
        "depth": depth,
    }
