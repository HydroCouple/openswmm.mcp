"""Links tools: fine-grained accessors beyond ``query.get_link_info``.

The aggregate ``query.get_link_info`` returns a batched property dict for any
link, and ``editing.set_link_properties`` handles the basic geometry setters
(length / roughness / offsets / initial_flow / max_flow / xsect_shape +
geom). This module adds the fine-grained pieces those aggregates don't
cover:

* **Statistics** (8) — peaks / durations + pump stats + hydraulic power.
* **Bulk arrays** (4) — depths / flows / quality reads, flows writes.
* **Control / open-close state** (5) — control_setting (continuous),
  target_setting (transition target), closed (binary).
* **Pump subtype** (4) — pump_curve, pump_init_state.
* **Conduit detail** (16) — barrels, culvert_code, loss_coeff,
  seep_rate, end_contractions, crest_height, discharge_coeff, flap_gate.
* **Quality** (1) — pollutant concentration getter.

Tools accept either an integer link index or a string link ID; resolution
is via ``session.links.get_index``. Many setters are RUNNING-state-only
(per the C contract); the tool layer enforces this with ``require_state``.
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

links_mcp = FastMCP("links")


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


async def _get_session(ctx: Context, session_id: str) -> SimSession:
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Links (fine-grained accessors)")
    return session


async def _resolve_link(session: SimSession, link_id: str | int) -> int:
    if isinstance(link_id, int):
        return link_id
    if not link_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] link_id must not be empty.")
    idx = await asyncio.to_thread(session.links.get_index, link_id)
    if idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found.")
    return idx


def _zip_link_results(session: SimSession, values: list[float]) -> list[dict[str, Any]]:
    """Build [{id, index, value}, ...] from a bulk array."""
    n = session.links.count()
    return [
        {"id": session.links.get_id(i), "index": i, "value": values[i]}
        for i in range(min(n, len(values)))
    ]


# ===========================================================================
# Statistics
# ===========================================================================


async def _stat_lookup(
    ctx: Context, session_id: str, link_id: str | int, attr: str, output_key: str
) -> dict:
    """Shared body for the per-link stat getters."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(getattr(session.links, attr), idx)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        output_key: value,
    }


@links_mcp.tool()
async def stat_max_flow(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the peak flow recorded for a link over the simulation."""
    return await _stat_lookup(ctx, session_id, link_id, "get_stat_max_flow", "max_flow")


@links_mcp.tool()
async def stat_max_velocity(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the peak velocity for a link."""
    return await _stat_lookup(ctx, session_id, link_id, "get_stat_max_velocity", "max_velocity")


@links_mcp.tool()
async def stat_max_filling(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the peak depth/full-depth ratio (0..1+) for a conduit."""
    return await _stat_lookup(ctx, session_id, link_id, "get_stat_max_filling", "max_filling")


@links_mcp.tool()
async def stat_vol_flow(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the total volume conveyed through a link."""
    return await _stat_lookup(ctx, session_id, link_id, "get_stat_vol_flow", "vol_flow")


@links_mcp.tool()
async def stat_surcharge_time(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return total surcharge duration (hours) for a link."""
    return await _stat_lookup(
        ctx,
        session_id,
        link_id,
        "get_stat_surcharge_time",
        "surcharge_time_hours",
    )


@links_mcp.tool()
async def stat_pump_cycles(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the on/off cycle count for a pump link."""
    return await _stat_lookup(ctx, session_id, link_id, "get_stat_pump_cycles", "pump_cycles")


@links_mcp.tool()
async def stat_pump_on_time(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return total on-time (seconds) for a pump link."""
    return await _stat_lookup(
        ctx,
        session_id,
        link_id,
        "get_stat_pump_on_time",
        "pump_on_time_seconds",
    )


@links_mcp.tool()
async def stat_pump_volume(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return total volume pumped by a pump link."""
    return await _stat_lookup(ctx, session_id, link_id, "get_stat_pump_volume", "pump_volume")


@links_mcp.tool()
async def hyd_power(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the current hydraulic power dissipated in a link."""
    return await _stat_lookup(ctx, session_id, link_id, "get_hyd_power", "hydraulic_power")


# ===========================================================================
# Bulk array readers (all links, one variable)
# ===========================================================================


@links_mcp.tool()
async def get_flows_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current flows for all links as {id, index, value} records."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.links.get_flows_bulk)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_link_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@links_mcp.tool()
async def get_depths_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current depths for all links."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.links.get_depths_bulk)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_link_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@links_mcp.tool()
async def get_quality_bulk(
    ctx: Context, session_id: str = "default", pollutant_index: int = 0
) -> dict:
    """Return pollutant concentrations across all links for one pollutant."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.links.get_quality_bulk, pollutant_index)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_link_results, session, values)
    return {
        "session_id": session_id,
        "pollutant_index": pollutant_index,
        "count": len(records),
        "results": records,
    }


@links_mcp.tool()
async def set_flows_bulk(
    ctx: Context,
    session_id: str = "default",
    flows: list[float] | None = None,
) -> dict:
    """Set flows for all links from a positional array (length = link count)."""
    if not flows:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] flows must be non-empty.")
    session = await _get_session(ctx, session_id)
    n = await asyncio.to_thread(session.links.count)
    if len(flows) != n:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] flows length {len(flows)} != link count {n}."
        )
    import numpy as np

    arr = np.asarray(flows, dtype=np.float64)
    await asyncio.to_thread(session.links.set_flows_bulk, arr)
    return {"status": "ok", "session_id": session_id, "count": len(flows)}


# ===========================================================================
# Control / open-close state (RUNNING-state setters)
# ===========================================================================


@links_mcp.tool()
async def get_control_setting(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the current continuous control setting (e.g. pump speed, orifice opening)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(session.links.get_control_setting, idx)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "setting": value,
    }


@links_mcp.tool()
async def set_control_setting(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    setting: float = 0.0,
) -> dict:
    """Set the control setting (typically 0..1) on a link.

    Distinct from forcing.set_link_control / controls.set_link_setting only
    in namespace — all three wrap the same C call. Use this when working
    primarily through the links namespace.
    """
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    idx = await _resolve_link(session, link_id)
    await asyncio.to_thread(session.links.set_control_setting, idx, float(setting))
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "setting": setting,
    }


@links_mcp.tool()
async def get_target_setting(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the target setting (the value the link is transitioning toward)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(session.links.get_target_setting, idx)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "target_setting": value,
    }


@links_mcp.tool()
async def set_target_setting(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    target: float = 0.0,
) -> dict:
    """Set the gradual-transition target setting on a link."""
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    idx = await _resolve_link(session, link_id)
    await asyncio.to_thread(session.links.set_target_setting, idx, float(target))
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "target_setting": target,
    }


@links_mcp.tool()
async def get_closed(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return whether a link is currently closed (no flow)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    closed = await asyncio.to_thread(session.links.get_closed, idx)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "closed": bool(closed),
    }


@links_mcp.tool()
async def set_closed(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    closed: bool = False,
) -> dict:
    """Close or open a link (binary on/off state)."""
    session = await _get_session(ctx, session_id)
    require_state(session, "running")
    idx = await _resolve_link(session, link_id)
    await asyncio.to_thread(session.links.set_closed, idx, closed)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "closed": closed,
    }


# ===========================================================================
# Pump subtype
# ===========================================================================


@links_mcp.tool()
async def get_pump_curve(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the pump-curve index for a pump link."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    curve_idx = await asyncio.to_thread(session.links.get_pump_curve, idx)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "curve_index": curve_idx,
    }


@links_mcp.tool()
async def set_pump_curve(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    curve_index: int = 0,
) -> dict:
    """Assign a pump curve to a pump link (curve type PUMP1..PUMP4)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    await asyncio.to_thread(session.links.set_pump_curve, idx, curve_index)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "curve_index": curve_index,
    }


@links_mcp.tool()
async def get_pump_init_state(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the initial ON/OFF state of a pump (1 = on, 0 = off)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    state = await asyncio.to_thread(session.links.get_pump_init_state, idx)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "init_state": int(state),
    }


@links_mcp.tool()
async def set_pump_init_state(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    init_on: bool = False,
) -> dict:
    """Set the initial ON/OFF state of a pump."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    await asyncio.to_thread(session.links.set_pump_init_state, idx, 1 if init_on else 0)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "init_on": init_on,
    }


# ===========================================================================
# Conduit detail (loss coefficients, barrels, culvert code, flap gate, seep)
# ===========================================================================


async def _scalar_get(
    ctx: Context,
    session_id: str,
    link_id: str | int,
    getter: str,
    value_key: str,
    value_type: type,
) -> dict:
    """Shared body for the conduit-detail scalar getters."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    v = await asyncio.to_thread(getattr(session.links, getter), idx)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        value_key: value_type(v),
    }


async def _scalar_set(
    ctx: Context,
    session_id: str,
    link_id: str | int,
    setter: str,
    value,
    value_key: str,
    value_type: type,
) -> dict:
    """Shared body for the conduit-detail scalar setters."""
    if value is None:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {value_key} is required.")
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    await asyncio.to_thread(getattr(session.links, setter), idx, value_type(value))
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        value_key: value_type(value),
    }


@links_mcp.tool()
async def get_barrels(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the number of parallel barrels for a conduit."""
    return await _scalar_get(ctx, session_id, link_id, "get_barrels", "barrels", int)


@links_mcp.tool()
async def set_barrels(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    barrels: int = 1,
) -> dict:
    """Set the number of parallel barrels for a conduit."""
    return await _scalar_set(ctx, session_id, link_id, "set_barrels", barrels, "barrels", int)


@links_mcp.tool()
async def get_culvert_code(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the FHWA HDS-5 culvert inlet code (0 = not a culvert)."""
    return await _scalar_get(ctx, session_id, link_id, "get_culvert_code", "culvert_code", int)


@links_mcp.tool()
async def set_culvert_code(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    culvert_code: int = 0,
) -> dict:
    """Set the FHWA HDS-5 culvert inlet code."""
    return await _scalar_set(
        ctx,
        session_id,
        link_id,
        "set_culvert_code",
        culvert_code,
        "culvert_code",
        int,
    )


@links_mcp.tool()
async def get_loss_coeff(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the conduit's head-loss coefficients as ``(inlet, outlet, avg)``."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    inlet, outlet, avg = await asyncio.to_thread(session.links.get_loss_coeff, idx)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "inlet": inlet,
        "outlet": outlet,
        "avg": avg,
    }


@links_mcp.tool()
async def set_loss_coeff(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    inlet: float = 0.0,
    outlet: float = 0.0,
    avg: float = 0.0,
) -> dict:
    """Set the conduit head-loss coefficients (entrance, exit, average)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    await asyncio.to_thread(
        session.links.set_loss_coeff,
        idx,
        float(inlet),
        float(outlet),
        float(avg),
    )
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "inlet": inlet,
        "outlet": outlet,
        "avg": avg,
    }


@links_mcp.tool()
async def get_seep_rate(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the conduit seepage rate (depth/time)."""
    return await _scalar_get(ctx, session_id, link_id, "get_seep_rate", "seep_rate", float)


@links_mcp.tool()
async def set_seep_rate(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    seep_rate: float = 0.0,
) -> dict:
    """Set the conduit seepage rate."""
    return await _scalar_set(
        ctx,
        session_id,
        link_id,
        "set_seep_rate",
        seep_rate,
        "seep_rate",
        float,
    )


@links_mcp.tool()
async def get_flap_gate(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return whether a conduit / orifice has a flap gate."""
    return await _scalar_get(ctx, session_id, link_id, "get_flap_gate", "has_flap_gate", bool)


@links_mcp.tool()
async def set_flap_gate(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    has_flap_gate: bool = False,
) -> dict:
    """Set the flap-gate flag (prevents backflow)."""
    return await _scalar_set(
        ctx,
        session_id,
        link_id,
        "set_flap_gate",
        has_flap_gate,
        "has_flap_gate",
        bool,
    )


@links_mcp.tool()
async def get_crest_height(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the crest height for a weir link."""
    return await _scalar_get(ctx, session_id, link_id, "get_crest_height", "crest_height", float)


@links_mcp.tool()
async def set_crest_height(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    crest_height: float = 0.0,
) -> dict:
    """Set the weir crest height."""
    return await _scalar_set(
        ctx,
        session_id,
        link_id,
        "set_crest_height",
        crest_height,
        "crest_height",
        float,
    )


@links_mcp.tool()
async def get_discharge_coeff(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the discharge coefficient (Cd) for a weir / orifice."""
    return await _scalar_get(
        ctx, session_id, link_id, "get_discharge_coeff", "discharge_coeff", float
    )


@links_mcp.tool()
async def set_discharge_coeff(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    discharge_coeff: float = 0.0,
) -> dict:
    """Set the discharge coefficient (Cd)."""
    return await _scalar_set(
        ctx,
        session_id,
        link_id,
        "set_discharge_coeff",
        discharge_coeff,
        "discharge_coeff",
        float,
    )


@links_mcp.tool()
async def get_end_contractions(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the number of end contractions on a weir (0 / 1 / 2)."""
    return await _scalar_get(
        ctx,
        session_id,
        link_id,
        "get_end_contractions",
        "end_contractions",
        int,
    )


@links_mcp.tool()
async def set_end_contractions(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    end_contractions: int = 0,
) -> dict:
    """Set the number of end contractions on a weir."""
    return await _scalar_set(
        ctx,
        session_id,
        link_id,
        "set_end_contractions",
        end_contractions,
        "end_contractions",
        int,
    )


# ===========================================================================
# Quality
# ===========================================================================


@links_mcp.tool()
async def get_quality(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    pollutant_index: int = 0,
) -> dict:
    """Return the current concentration of a pollutant in a link."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    conc = await asyncio.to_thread(session.links.get_quality, idx, pollutant_index)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "pollutant_index": pollutant_index,
        "concentration": conc,
    }
