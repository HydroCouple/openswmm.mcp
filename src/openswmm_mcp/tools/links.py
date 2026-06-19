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
from openswmm_mcp.errors import ErrorCode, ToolError, resolve_index
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
    idx = await resolve_index(session.links, link_id, "Link")
    if idx < 0:
        raise ToolError(f"[{ErrorCode.ELEMENT_NOT_FOUND}] Link '{link_id}' not found.")
    return idx


def _zip_link_results(session: SimSession, values: list[float]) -> list[dict[str, Any]]:
    """Build [{id, index, value}, ...] from a bulk array."""
    links = session.links
    n = len(links)
    return [
        {"id": links.get_id(i), "index": i, "value": values[i]}
        for i in range(min(n, len(values)))
    ]


# ===========================================================================
# Statistics (v1: link.stats.<attr> sub-view)
# ===========================================================================


async def _read_stat(
    ctx: Context, session_id: str, link_id: str | int, attr: str, output_key: str
) -> dict:
    """Shared body for the per-link stats sub-view readers."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: getattr(session.links[idx].stats, attr))
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        output_key: value,
    }


@links_mcp.tool()
async def stat_max_flow(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the peak flow recorded for a link over the simulation."""
    return await _read_stat(ctx, session_id, link_id, "max_flow", "max_flow")


@links_mcp.tool()
async def stat_max_velocity(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the peak velocity for a link."""
    return await _read_stat(ctx, session_id, link_id, "max_velocity", "max_velocity")


@links_mcp.tool()
async def stat_max_filling(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the peak depth/full-depth ratio (0..1+) for a conduit."""
    return await _read_stat(ctx, session_id, link_id, "max_filling", "max_filling")


@links_mcp.tool()
async def stat_vol_flow(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the total volume conveyed through a link."""
    return await _read_stat(ctx, session_id, link_id, "vol_flow", "vol_flow")


@links_mcp.tool()
async def stat_surcharge_time(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return total surcharge duration (hours) for a link."""
    return await _read_stat(ctx, session_id, link_id, "surcharge_time", "surcharge_time_hours")


@links_mcp.tool()
async def stat_pump_cycles(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the on/off cycle count for a pump link."""
    return await _read_stat(ctx, session_id, link_id, "pump_cycles", "pump_cycles")


@links_mcp.tool()
async def stat_pump_on_time(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return total on-time (seconds) for a pump link."""
    return await _read_stat(ctx, session_id, link_id, "pump_on_time", "pump_on_time_seconds")


@links_mcp.tool()
async def stat_pump_volume(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return total volume pumped by a pump link."""
    return await _read_stat(ctx, session_id, link_id, "pump_volume", "pump_volume")


@links_mcp.tool()
async def hyd_power(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the current hydraulic power dissipated in a link.

    Unlike the ``stat_*`` tools, ``hyd_power`` lives directly on the Link
    (not under ``link.stats``) — it's the instantaneous value, not a
    cumulative statistic.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].hyd_power)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "hydraulic_power": value,
    }


# ===========================================================================
# Bulk array readers (v1: link.<attr> numpy property)
# ===========================================================================


@links_mcp.tool()
async def get_flows_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current flows for all links as {id, index, value} records."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(lambda: session.links.flows)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_link_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@links_mcp.tool()
async def get_depths_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current depths for all links."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(lambda: session.links.depths)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_link_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@links_mcp.tool()
async def get_quality_bulk(
    ctx: Context, session_id: str = "default", pollutant_index: int = 0
) -> dict:
    """Return pollutant concentrations across all links for one pollutant."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(session.links.qualities, pollutant_index)
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
    n = await asyncio.to_thread(lambda: len(session.links))
    if len(flows) != n:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] flows length {len(flows)} != link count {n}."
        )
    import numpy as np

    arr = np.asarray(flows, dtype=np.float64)

    def _set() -> None:
        session.links.flows = arr

    await asyncio.to_thread(_set)
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
    value = await asyncio.to_thread(lambda: session.links[idx].control_setting)
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
    value = float(setting)

    def _set() -> None:
        session.links[idx].control_setting = value

    await asyncio.to_thread(_set)
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
    value = await asyncio.to_thread(lambda: session.links[idx].target_setting)
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
    value = float(target)

    def _set() -> None:
        session.links[idx].target_setting = value

    await asyncio.to_thread(_set)
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
    closed = await asyncio.to_thread(lambda: session.links[idx].closed)
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
    value = bool(closed)

    def _set() -> None:
        session.links[idx].closed = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "closed": closed,
    }


# ===========================================================================
# Pump subtype (v1: link.pump.<attr>)
# ===========================================================================


@links_mcp.tool()
async def get_pump_curve(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the pump-curve index for a pump link."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    curve_idx = await asyncio.to_thread(lambda: session.links[idx].pump.curve)
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

    def _set() -> None:
        session.links[idx].pump.curve = curve_index

    await asyncio.to_thread(_set)
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
    state = await asyncio.to_thread(lambda: session.links[idx].pump.init_state)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "init_state": int(bool(state)),
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
    state = bool(init_on)

    def _set() -> None:
        session.links[idx].pump.init_state = state

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "init_on": init_on,
    }


# ===========================================================================
# Conduit detail (v1: mix of direct Link props and weir sub-view)
# ===========================================================================


@links_mcp.tool()
async def get_barrels(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the number of parallel barrels for a conduit."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].barrels)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "barrels": int(value),
    }


@links_mcp.tool()
async def set_barrels(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    barrels: int = 1,
) -> dict:
    """Set the number of parallel barrels for a conduit."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = int(barrels)

    def _set() -> None:
        session.links[idx].barrels = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "barrels": value,
    }


@links_mcp.tool()
async def get_culvert_code(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the FHWA HDS-5 culvert inlet code (0 = not a culvert)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].culvert_code)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "culvert_code": int(value),
    }


@links_mcp.tool()
async def set_culvert_code(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    culvert_code: int = 0,
) -> dict:
    """Set the FHWA HDS-5 culvert inlet code."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = int(culvert_code)

    def _set() -> None:
        session.links[idx].culvert_code = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "culvert_code": value,
    }


@links_mcp.tool()
async def get_loss_coeff(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the conduit's head-loss coefficients as ``(inlet, outlet, avg)``."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    inlet, outlet, avg = await asyncio.to_thread(lambda: session.links[idx].loss_coeff)
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
    coeffs = (float(inlet), float(outlet), float(avg))

    def _set() -> None:
        session.links[idx].loss_coeff = coeffs

    await asyncio.to_thread(_set)
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
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].seep_rate)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "seep_rate": float(value),
    }


@links_mcp.tool()
async def set_seep_rate(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    seep_rate: float = 0.0,
) -> dict:
    """Set the conduit seepage rate."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = float(seep_rate)

    def _set() -> None:
        session.links[idx].seep_rate = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "seep_rate": value,
    }


@links_mcp.tool()
async def get_flap_gate(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return whether a conduit / orifice has a flap gate."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].flap_gate)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "has_flap_gate": bool(value),
    }


@links_mcp.tool()
async def set_flap_gate(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    has_flap_gate: bool = False,
) -> dict:
    """Set the flap-gate flag (prevents backflow)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = bool(has_flap_gate)

    def _set() -> None:
        session.links[idx].flap_gate = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "has_flap_gate": value,
    }


@links_mcp.tool()
async def get_crest_height(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the crest height for a weir link."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].weir.crest_height)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "crest_height": float(value),
    }


@links_mcp.tool()
async def set_crest_height(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    crest_height: float = 0.0,
) -> dict:
    """Set the weir crest height."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = float(crest_height)

    def _set() -> None:
        session.links[idx].weir.crest_height = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "crest_height": value,
    }


@links_mcp.tool()
async def get_discharge_coeff(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the discharge coefficient (Cd) for a weir / orifice."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].weir.discharge_coeff)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "discharge_coeff": float(value),
    }


@links_mcp.tool()
async def set_discharge_coeff(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    discharge_coeff: float = 0.0,
) -> dict:
    """Set the discharge coefficient (Cd)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = float(discharge_coeff)

    def _set() -> None:
        session.links[idx].weir.discharge_coeff = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "discharge_coeff": value,
    }


@links_mcp.tool()
async def get_end_contractions(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the number of end contractions on a weir (0 / 1 / 2)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].weir.end_contractions)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "end_contractions": int(value),
    }


@links_mcp.tool()
async def set_end_contractions(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    end_contractions: int = 0,
) -> dict:
    """Set the number of end contractions on a weir."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = int(end_contractions)

    def _set() -> None:
        session.links[idx].weir.end_contractions = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "end_contractions": value,
    }


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
    conc = await asyncio.to_thread(
        lambda: session.links[idx].quality(pollutant_index)
    )
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "pollutant_index": pollutant_index,
        "concentration": conc,
    }


# ===========================================================================
# Orifice subtype (v1: link.orifice.<attr>)
# ===========================================================================


@links_mcp.tool()
async def get_orifice_open_close_rate(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the orifice open/close rate (time, in hours, to fully operate the gate)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].orifice.open_close_rate)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "open_close_rate": float(value),
    }


@links_mcp.tool()
async def set_orifice_open_close_rate(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    open_close_rate: float = 0.0,
) -> dict:
    """Set the orifice open/close rate (hours to fully operate the gate)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = float(open_close_rate)

    def _set() -> None:
        session.links[idx].orifice.open_close_rate = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "open_close_rate": value,
    }


# ===========================================================================
# Outlet subtype (v1: link.outlet.<attr>)
# ===========================================================================


@links_mcp.tool()
async def get_outlet_expon(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the outlet rating-curve exponent (functional rating types only)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].outlet.expon)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "expon": float(value),
    }


@links_mcp.tool()
async def set_outlet_expon(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    expon: float = 0.0,
) -> dict:
    """Set the outlet rating-curve exponent."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = float(expon)

    def _set() -> None:
        session.links[idx].outlet.expon = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "expon": value,
    }


@links_mcp.tool()
async def get_outlet_rating_type(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the outlet rating-curve classification.

    ``rating_type`` enum: 0=FUNCTIONAL_HEAD, 1=FUNCTIONAL_DEPTH,
    2=TABULAR_HEAD, 3=TABULAR_DEPTH.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    # v1 OutletView.rating_type returns an OutletRatingType IntEnum.
    type_code = await asyncio.to_thread(
        lambda: int(session.links[idx].outlet.rating_type)
    )
    name_map = {
        0: "functional_head",
        1: "functional_depth",
        2: "tabular_head",
        3: "tabular_depth",
    }
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "rating_type_code": type_code,
        "rating_type": name_map.get(type_code, "unknown"),
    }


@links_mcp.tool()
async def set_outlet_rating_type(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    rating_type: int = 0,
) -> dict:
    """Set the outlet rating-curve classification (integer code 0..3).

    0=FUNCTIONAL_HEAD, 1=FUNCTIONAL_DEPTH, 2=TABULAR_HEAD, 3=TABULAR_DEPTH.
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = int(rating_type)

    def _set() -> None:
        session.links[idx].outlet.rating_type = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "rating_type_code": value,
    }


# ===========================================================================
# Pump depth knobs (v1: link.pump.<attr>)
# ===========================================================================


@links_mcp.tool()
async def get_pump_shutoff_depth(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the wet-well depth below which the pump shuts off."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].pump.shutoff_depth)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "shutoff_depth": float(value),
    }


@links_mcp.tool()
async def set_pump_shutoff_depth(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    shutoff_depth: float = 0.0,
) -> dict:
    """Set the wet-well depth below which the pump shuts off."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = float(shutoff_depth)

    def _set() -> None:
        session.links[idx].pump.shutoff_depth = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "shutoff_depth": value,
    }


@links_mcp.tool()
async def get_pump_startup_depth(
    ctx: Context, session_id: str = "default", link_id: str | int = ""
) -> dict:
    """Return the wet-well depth above which the pump starts up."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].pump.startup_depth)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "startup_depth": float(value),
    }


@links_mcp.tool()
async def set_pump_startup_depth(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    startup_depth: float = 0.0,
) -> dict:
    """Set the wet-well depth above which the pump starts up."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = float(startup_depth)

    def _set() -> None:
        session.links[idx].pump.startup_depth = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "startup_depth": value,
    }


# ===========================================================================
# Tag (v1: link.tag direct property)
# ===========================================================================


@links_mcp.tool()
async def get_tag(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return the link's free-form tag string (empty string when unset)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = await asyncio.to_thread(lambda: session.links[idx].tag)
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "tag": value,
    }


@links_mcp.tool()
async def set_tag(
    ctx: Context,
    session_id: str = "default",
    link_id: str | int = "",
    tag: str = "",
) -> dict:
    """Set the link's free-form tag string (empty string clears it)."""
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    value = tag

    def _set() -> None:
        session.links[idx].tag = value

    await asyncio.to_thread(_set)
    return {
        "status": "ok",
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "tag": value,
    }


# ===========================================================================
# Cross-section (v1: link.xsect view)
# ===========================================================================


@links_mcp.tool()
async def get_xsect(ctx: Context, session_id: str = "default", link_id: str | int = "") -> dict:
    """Return a link's cross-section shape plus its four geometry parameters.

    ``shape`` is the ``XSectShape`` enum name; ``shape_code`` is its integer
    value. ``g1..g4`` are the shape-dependent geometry values (for most
    closed conduits g1 is the full depth / max height).
    """
    session = await _get_session(ctx, session_id)
    idx = await _resolve_link(session, link_id)
    shape, g1, g2, g3, g4 = await asyncio.to_thread(
        lambda: session.links[idx].xsect.as_tuple()
    )
    return {
        "session_id": session_id,
        "link_id": link_id,
        "link_index": idx,
        "shape": shape.name,
        "shape_code": int(shape),
        "g1": float(g1),
        "g2": float(g2),
        "g3": float(g3),
        "g4": float(g4),
    }


# ===========================================================================
# Bulk array readers — settings + ids (v1: link.<attr> numpy property)
# ===========================================================================


@links_mcp.tool()
async def get_control_settings_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current control settings for all links as {id, index, value} records."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(lambda: session.links.control_settings)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_link_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@links_mcp.tool()
async def get_target_settings_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return current target settings for all links as {id, index, value} records."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(lambda: session.links.target_settings)
    values = ndarray_to_list(arr)
    records = await asyncio.to_thread(_zip_link_results, session, values)
    return {"session_id": session_id, "count": len(records), "results": records}


@links_mcp.tool()
async def get_ids_bulk(ctx: Context, session_id: str = "default") -> dict:
    """Return the list of all link IDs in index order."""
    session = await _get_session(ctx, session_id)
    arr = await asyncio.to_thread(lambda: session.links.ids)
    ids = [str(x) for x in ndarray_to_list(arr)]
    return {"session_id": session_id, "count": len(ids), "ids": ids}
