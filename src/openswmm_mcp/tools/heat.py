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

"""Heat-transport configuration tools — flux modules, radiative parameters,
solar position, cloud cover, and inlet water temperatures.

Wraps the engine's ``Heat`` view (``session.heat``, C API ``openswmm_heat.h``):
the ``[HEAT_FLUXES]`` module toggles, the ``[RADIATIVE_FLUXES]`` scalars, the
``[SOLAR_RADIATION]`` and ``[CLOUD_COVER]`` sections, and the
``[HEAT_SOURCES]`` inlet-temperature table with its per-node overrides.

**Temperatures are degrees Celsius, everywhere.** Radiation is W/m²; cloud
fraction, albedo, emissivity and the other radiative factors are fractions in
``[0, 1]``, never percents. Solar latitude is degrees north, longitude degrees
east, elevation metres, timezone hours from UTC.

**Values are refused, not clamped.** Every setter mirrors the ``.inp`` parser's
own range check, so a value the deck rejects is a value these tools reject, and
a refused write does not take effect at all — there is no partial application to
undo. Inlet temperatures must lie in ``[-50, 100]`` degC; fractions in
``[0, 1]``; a negative shortwave constant is refused.

**Edits are LIVE.** The flux modules re-read their configuration every routing
step, so these tools work in ``opened``, ``initialized``, ``running`` and
``ended`` states and a mid-run edit takes effect on the next step. That is
deliberate: it is the same contract the water-age table carries (see
``water_age_set_global_source``).

Three constraints an agent should know *before* writing rather than after:

* ``COMPUTED`` shortwave requires BOTH latitude and longitude to have been set
  explicitly. Check ``heat_get_config``'s ``solar_sited`` flag first — the
  engine will not silently borrow the ``[TEMPERATURE]`` SNOWMELT latitude,
  because that field defaults to 0 and borrowing it would model equatorial noon.
* The ``shortwave`` radiative parameter is writable only while the mode is
  ``constant``. In the other two modes a stored constant is never read, so
  storing one would look configured while changing nothing. Switch the mode
  first with ``heat_set_shortwave_mode``.
* Only the ``dwf`` and ``external_inflow`` sources take per-node overrides.
  Every other source refuses NODE scope outright — the same answer the deck
  gets, refused rather than silently deferred to the global value.

Requires the ``openswmm`` backend.
"""

from __future__ import annotations

import asyncio
import logging

from fastmcp import Context, FastMCP

from openswmm_mcp._util.transport_enums import (
    coerce_enum,
    heat_cloud_param_codes,
    heat_flux_module_codes,
    heat_radiative_param_codes,
    heat_shortwave_mode_codes,
    heat_solar_param_codes,
    heat_source_kind_codes,
    name_for,
)
from openswmm_mcp.dependencies import (
    get_session_manager,
    require_new_engine,
    require_state,
)
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

logger = logging.getLogger(__name__)

heat_mcp = FastMCP("heat")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Heat edits are documented as LIVE (the flux modules re-read the config every
# routing step), so writes are NOT narrowed to "opened" the way the climate
# configuration is -- they are legal wherever a solver exists.
_HEAT_STATES = ("opened", "initialized", "running", "ended")


async def _editable_heat(ctx: Context, session_id: str) -> tuple[SimSession, object]:
    """Return ``(session, heat_view)`` for a heat read or a live heat edit."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Heat transport configuration")
    require_state(session, *_HEAT_STATES)
    return session, session.heat


def _apply(fn):
    """Run a blocking heat closure, mapping engine refusals to ``ToolError``."""
    try:
        return fn()
    except ToolError:
        raise
    except Exception as exc:  # EngineError subclasses + ValueError from the C layer
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}") from exc


# ---------------------------------------------------------------------------
# Overall configuration
# ---------------------------------------------------------------------------


@heat_mcp.tool()
async def get_config(ctx: Context, session_id: str = "default") -> dict:
    """Read the whole heat-transport configuration and current radiative state.

    Returns ``enabled`` (the ``[OPTIONS] HEAT_TRANSPORT`` switch), every
    ``[HEAT_FLUXES]`` module toggle, the shortwave mode as both ``mode_code``
    and ``mode``, ``solar_sited`` (whether latitude AND longitude were both set
    explicitly — the precondition for ``computed`` shortwave), and
    ``cloud_configured``.

    Two of the fields are *state*, not configuration, and read 0 before the
    first routing step: ``current_shortwave_wm2`` is the resolved incoming
    shortwave with cloud attenuation already applied, and ``current_cloud``
    is the cloud fraction in effect. ``current_shortwave_wm2`` also reads 0
    whenever radiative exchange is switched off.

    Gate a ``computed`` shortwave selection on ``solar_sited`` rather than
    discovering the refusal from ``heat_set_shortwave_mode``. See
    ``heat_get_radiative``, ``heat_get_solar`` and ``heat_get_cloud`` for the
    individual parameter values.
    """
    # wraps: swmm_heat_get_enabled swmm_heat_get_module swmm_heat_get_shortwave_mode swmm_heat_get_current_shortwave swmm_heat_get_solar_sited swmm_heat_get_cloud_configured swmm_heat_get_current_cloud  # noqa: E501
    _, heat = await _editable_heat(ctx, session_id)
    module_codes = heat_flux_module_codes()
    mode_codes = heat_shortwave_mode_codes()

    def _read() -> dict:
        modules = {name: bool(heat.modules[code]) for name, code in module_codes.items()}
        mode = int(heat.shortwave_mode)
        return {
            "enabled": bool(heat.enabled),
            "modules": modules,
            "shortwave_mode_code": mode,
            "shortwave_mode": name_for(mode, mode_codes),
            "current_shortwave_wm2": float(heat.current_shortwave),
            "solar_sited": bool(heat.solar_sited),
            "cloud_configured": bool(heat.cloud.configured),
            "current_cloud": float(heat.cloud.current),
        }

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@heat_mcp.tool()
async def set_module(
    ctx: Context,
    session_id: str = "default",
    module: str | int = "",
    on: bool = True,
) -> dict:
    """Switch one ``[HEAT_FLUXES]`` module on or off.

    ``module`` is a name or int code: ``surface_exchange`` (latent + sensible),
    ``radiative_exchange`` (shortwave + longwave), or ``layer_conduction`` (LID
    vertical conduction). The modules are independent — switching one off does
    not disturb the others' parameters.

    The edit is live: the module set is re-read every routing step, so this
    takes effect on the next step. Read the toggles back with
    ``heat_get_config``.
    """
    # wraps: swmm_heat_set_module
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_flux_module_codes()
    code = coerce_enum(module, codes, "heat flux module")

    def _set() -> None:
        heat.modules[code] = bool(on)

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "module": name_for(code, codes),
        "module_code": code,
        "on": bool(on),
    }


# ---------------------------------------------------------------------------
# [RADIATIVE_FLUXES]
# ---------------------------------------------------------------------------


@heat_mcp.tool()
async def get_radiative(
    ctx: Context,
    session_id: str = "default",
    param: str | int = "",
) -> dict:
    """Read one ``[RADIATIVE_FLUXES]`` parameter, or all of them.

    Leave ``param`` empty to get every parameter as a name-keyed ``values``
    dict; name one to get a single ``value`` plus its ``param_code``.

    Parameters and units: ``shortwave`` W/m² (read only in ``constant`` mode);
    ``albedo`` water reflectance, ``shade_factor``, ``sky_view``,
    ``emiss_water``, ``emiss_landcover``, ``atm_emiss_coeff`` (the Brunt
    coefficient) and ``lw_reflection`` are all fractions in ``[0, 1]``.

    Note that ``albedo`` here is the WATER surface reflectance; the land
    albedo used by the clear-sky model is ``ground_albedo`` on
    ``heat_get_solar``. Write with ``heat_set_radiative``.
    """
    # wraps: swmm_heat_get_radiative
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_radiative_param_codes()

    if param == "" or param is None:

        def _read_all() -> dict:
            return {"values": {n: float(heat.radiative[c]) for n, c in codes.items()}}

        out = await asyncio.to_thread(_apply, _read_all)
        out["session_id"] = session_id
        return out

    code = coerce_enum(param, codes, "heat radiative parameter")
    value = await asyncio.to_thread(_apply, lambda: float(heat.radiative[code]))
    return {
        "session_id": session_id,
        "param": name_for(code, codes),
        "param_code": code,
        "value": value,
    }


@heat_mcp.tool()
async def set_radiative(
    ctx: Context,
    session_id: str = "default",
    param: str | int = "",
    value: float = 0.0,
) -> dict:
    """Write one ``[RADIATIVE_FLUXES]`` parameter.

    ``param`` is a name or int code (see ``heat_get_radiative`` for the list
    and units). Every fraction must lie in ``[0, 1]``; ``shortwave`` must be
    non-negative and is accepted ONLY while the shortwave mode is ``constant``
    — a constant is not read in ``timeseries`` or ``computed`` mode, so storing
    one there would look configured while changing nothing. Switch the mode
    first with ``heat_set_shortwave_mode``.

    Out-of-range values are REFUSED, not clamped, and a refused write does not
    take effect. The edit is live and applies on the next routing step.
    """
    # wraps: swmm_heat_set_radiative
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_radiative_param_codes()
    code = coerce_enum(param, codes, "heat radiative parameter")
    val = float(value)

    def _set() -> None:
        heat.radiative[code] = val

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "param": name_for(code, codes),
        "param_code": code,
        "value": val,
    }


# ---------------------------------------------------------------------------
# Shortwave mode
# ---------------------------------------------------------------------------


@heat_mcp.tool()
async def set_shortwave_mode(
    ctx: Context,
    session_id: str = "default",
    mode: str | int = "",
    timeseries: str = "",
) -> dict:
    """Select where incoming shortwave radiation comes from.

    ``mode`` is ``constant`` (the fixed ``shortwave`` W/m² on
    ``heat_set_radiative``), ``timeseries`` (a measured ``[TIMESERIES]``
    record), or ``computed`` (solar position plus the Bird clear-sky model).
    The three are mutually exclusive in effect — exactly one is read, with no
    precedence ladder behind it.

    Switching modes does NOT erase the other modes' settings: a stored constant
    survives while a timeseries is active and vice versa, so a caller can move
    back and forth without losing what it configured.

    ``computed`` is refused unless BOTH latitude and longitude have been set
    via ``heat_set_solar``; check ``solar_sited`` on ``heat_get_config`` first.
    ``timeseries`` is refused unless a series has been bound — pass the series
    name as ``timeseries`` and this tool binds it (which also selects the mode)
    in the same call. Naming a ``timeseries`` that does not exist in
    ``[TIMESERIES]`` is refused.
    """
    # wraps: swmm_heat_set_shortwave_mode swmm_heat_set_shortwave_timeseries
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_shortwave_mode_codes()
    ts_code = codes.get("timeseries")

    if timeseries:
        # Binding a series selects TIMESERIES mode as a side effect, so this
        # is the only ordering that cannot transiently fail.
        await asyncio.to_thread(_apply, lambda: heat.set_shortwave_timeseries(timeseries))
        code = ts_code if mode == "" or mode is None else coerce_enum(mode, codes, "shortwave mode")
        if code != ts_code:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] mode '{name_for(code, codes)}' contradicts the "
                f"timeseries binding. Pass timeseries only with mode 'timeseries' (or omit mode)."
            )
    else:
        if mode == "" or mode is None:
            raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a mode (or a timeseries).")
        code = coerce_enum(mode, codes, "shortwave mode")

        def _set() -> None:
            heat.shortwave_mode = code

        await asyncio.to_thread(_apply, _set)

    return {
        "status": "ok",
        "session_id": session_id,
        "shortwave_mode": name_for(code, codes),
        "shortwave_mode_code": code,
        "timeseries": timeseries,
    }


# ---------------------------------------------------------------------------
# [SOLAR_RADIATION]
# ---------------------------------------------------------------------------


@heat_mcp.tool()
async def get_solar(
    ctx: Context,
    session_id: str = "default",
    param: str | int = "",
) -> dict:
    """Read one ``[SOLAR_RADIATION]`` parameter, or all of them.

    Leave ``param`` empty for a name-keyed ``values`` dict of every parameter.
    These are consulted only under ``computed`` shortwave.

    Units: ``latitude`` degrees north ``[-90, 90]``; ``longitude`` degrees east
    ``[-180, 180]``; ``timezone`` hours from UTC, positive east; ``elevation``
    metres ``[-500, 9000]`` (below sea level is legal, and if never written the
    climate state's elevation is used); ``turbidity_380`` and ``turbidity_500``
    Bird aerosol optical depths; ``precip_water`` precipitable water in cm;
    ``ozone`` ozone column in cm; ``ground_albedo`` LAND albedo ``[0, 1]``
    — which is NOT the water ``albedo`` on ``heat_get_radiative``.

    Whether latitude and longitude were both set explicitly is reported as
    ``solar_sited`` by ``heat_get_config``.
    """
    # wraps: swmm_heat_get_solar
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_solar_param_codes()

    if param == "" or param is None:

        def _read_all() -> dict:
            return {"values": {n: float(heat.solar[c]) for n, c in codes.items()}}

        out = await asyncio.to_thread(_apply, _read_all)
        out["session_id"] = session_id
        return out

    code = coerce_enum(param, codes, "heat solar parameter")
    value = await asyncio.to_thread(_apply, lambda: float(heat.solar[code]))
    return {
        "session_id": session_id,
        "param": name_for(code, codes),
        "param_code": code,
        "value": value,
    }


@heat_mcp.tool()
async def set_solar(
    ctx: Context,
    session_id: str = "default",
    param: str | int = "",
    value: float = 0.0,
) -> dict:
    """Write one ``[SOLAR_RADIATION]`` parameter.

    See ``heat_get_solar`` for the parameter list, ranges and units.
    Out-of-range values are REFUSED, not clamped.

    Writing ``latitude`` or ``longitude`` also marks it as explicitly provided,
    which is exactly what the ``computed`` shortwave mode checks for — set both
    before calling ``heat_set_shortwave_mode`` with ``computed``.
    """
    # wraps: swmm_heat_set_solar
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_solar_param_codes()
    code = coerce_enum(param, codes, "heat solar parameter")
    val = float(value)

    def _set() -> None:
        heat.solar[code] = val

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "param": name_for(code, codes),
        "param_code": code,
        "value": val,
    }


# ---------------------------------------------------------------------------
# [CLOUD_COVER]
# ---------------------------------------------------------------------------


@heat_mcp.tool()
async def get_cloud(
    ctx: Context,
    session_id: str = "default",
    param: str | int = "",
) -> dict:
    """Read one ``[CLOUD_COVER]`` parameter, or all of them, plus cloud state.

    Leave ``param`` empty for a name-keyed ``values`` dict. Always returns
    ``configured`` (is a ``[CLOUD_COVER]`` section in effect at all?) and
    ``current`` (the cloud fraction in effect at the current routing step).

    ``fraction`` is a FRACTION in ``[0, 1]``, not a percent. ``sw_atten_k`` and
    ``sw_atten_n`` are the Kasten-Czeplak shortwave attenuation coefficients
    and ``lw_cloud_k`` the Bolz longwave coefficient; all three must be
    non-negative. Write with ``heat_set_cloud``, bind a record with
    ``heat_set_cloud_timeseries``, or return to clear sky with
    ``heat_clear_cloud``.
    """
    # wraps: swmm_heat_get_cloud swmm_heat_get_cloud_configured swmm_heat_get_current_cloud
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_cloud_param_codes()

    if param == "" or param is None:

        def _read_all() -> dict:
            return {
                "values": {n: float(heat.cloud[c]) for n, c in codes.items()},
                "configured": bool(heat.cloud.configured),
                "current": float(heat.cloud.current),
            }

        out = await asyncio.to_thread(_apply, _read_all)
        out["session_id"] = session_id
        return out

    code = coerce_enum(param, codes, "heat cloud parameter")

    def _read_one() -> dict:
        return {
            "value": float(heat.cloud[code]),
            "configured": bool(heat.cloud.configured),
            "current": float(heat.cloud.current),
        }

    out = await asyncio.to_thread(_apply, _read_one)
    out["session_id"] = session_id
    out["param"] = name_for(code, codes)
    out["param_code"] = code
    return out


@heat_mcp.tool()
async def set_cloud(
    ctx: Context,
    session_id: str = "default",
    param: str | int = "",
    value: float = 0.0,
) -> dict:
    """Write one ``[CLOUD_COVER]`` parameter.

    Writing ANY cloud parameter marks cloud cover as configured — there is no
    separate enable switch. ``fraction`` must lie in ``[0, 1]`` (it is a
    fraction, not a percent) and the attenuation coefficients must be
    non-negative; out-of-range values are REFUSED, not clamped.

    Use ``heat_clear_cloud`` to remove the section entirely and return to the
    exact clear-sky longwave path.
    """
    # wraps: swmm_heat_set_cloud
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_cloud_param_codes()
    code = coerce_enum(param, codes, "heat cloud parameter")
    val = float(value)

    def _set() -> None:
        heat.cloud[code] = val

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "param": name_for(code, codes),
        "param_code": code,
        "value": val,
    }


@heat_mcp.tool()
async def set_cloud_timeseries(
    ctx: Context,
    session_id: str = "default",
    timeseries: str = "",
) -> dict:
    """Bind a ``[TIMESERIES]`` record (by name) as the cloud-fraction source.

    The series supplies the cloud fraction per step in place of the constant
    ``fraction`` parameter. Naming a series that does not exist in
    ``[TIMESERIES]`` is refused. Binding also marks cloud cover as configured.
    """
    # wraps: swmm_heat_set_cloud_timeseries
    if not timeseries:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a timeseries name.")
    _, heat = await _editable_heat(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: heat.cloud.set_timeseries(timeseries))
    return {"status": "ok", "session_id": session_id, "timeseries": timeseries}


@heat_mcp.tool()
async def clear_cloud(ctx: Context, session_id: str = "default") -> dict:
    """Remove ``[CLOUD_COVER]`` entirely — back to clear sky.

    This restores the exact clear-sky longwave path rather than an
    approximation of it: the cloud factor becomes a literal 1.0 that the
    atmospheric-emissivity term short-circuits. ``configured`` on
    ``heat_get_cloud`` reads false afterwards.
    """
    # wraps: swmm_heat_clear_cloud
    _, heat = await _editable_heat(ctx, session_id)
    await asyncio.to_thread(_apply, heat.cloud.clear)
    return {"status": "ok", "session_id": session_id, "configured": False}


# ---------------------------------------------------------------------------
# [HEAT_SOURCES] -- global inlet temperatures
# ---------------------------------------------------------------------------


@heat_mcp.tool()
async def list_sources(ctx: Context, session_id: str = "default") -> dict:
    """List every ``[HEAT_SOURCES]`` water source with its global inlet temperature.

    The table is a fixed enum extent, not a parsed list, so ``count`` is always
    7 and this never fails on a model with no heat configured. Sources:
    ``rainfall`` (washoff runoff), ``dwf``, ``gw``, ``rdii``,
    ``external_inflow`` (``[INFLOWS]``), ``iface`` (interface file), and
    ``initial_state`` (water present at t=0).

    Each row carries ``temp_c`` in degrees Celsius and ``configured``. The
    distinction matters: an unconfigured source reads the 20 degC DEFAULT
    rather than a value the model actually stated, and only ``configured``
    tells the two apart. Per-node overrides live in
    ``heat_list_node_overrides``; the resolved value at a specific node is
    ``heat_get_effective_source_temp``.
    """
    # wraps: swmm_heat_source_count swmm_heat_get_source_temp swmm_heat_get_source_configured
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_source_kind_codes()

    def _read() -> dict:
        sources = heat.sources
        rows = [
            {
                "source": name,
                "source_code": code,
                "temp_c": float(sources[code]),
                "configured": bool(sources.is_configured(code)),
            }
            for name, code in sorted(codes.items(), key=lambda kv: kv[1])
        ]
        return {"count": len(sources), "sources": rows}

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@heat_mcp.tool()
async def set_source_temp(
    ctx: Context,
    session_id: str = "default",
    source: str | int = "",
    temp_c: float = 20.0,
) -> dict:
    """Set the GLOBAL inlet temperature (degC) for one water source.

    ``source`` is a name or int code (see ``heat_list_sources``). ``temp_c``
    must lie in ``[-50, 100]`` degrees Celsius — the parser's own range.
    Out-of-range values are REFUSED, not clamped, and a refused write does not
    take effect.

    Writing marks the source configured, so the model writer will emit a row
    for it. Use ``heat_clear_source_temp`` to undo that. Per-node values
    override this global one for the ``dwf`` and ``external_inflow`` sources —
    see ``heat_set_node_override``.
    """
    # wraps: swmm_heat_set_source_temp
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_source_kind_codes()
    code = coerce_enum(source, codes, "heat source kind")
    val = float(temp_c)

    def _set() -> None:
        heat.sources[code] = val

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "source": name_for(code, codes),
        "source_code": code,
        "temp_c": val,
    }


@heat_mcp.tool()
async def clear_source_temp(
    ctx: Context,
    session_id: str = "default",
    source: str | int = "",
) -> dict:
    """Return one source to the 20 degC default and mark it unconfigured.

    After this the model writer emits no ``[HEAT_SOURCES]`` row for the source.
    NODE overrides for the same source are left untouched — they are separate
    rows, and removing them here would delete model the caller did not name.
    Remove those explicitly with ``heat_remove_node_override``.
    """
    # wraps: swmm_heat_clear_source_temp
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_source_kind_codes()
    code = coerce_enum(source, codes, "heat source kind")
    await asyncio.to_thread(_apply, lambda: heat.sources.clear(code))
    return {
        "status": "ok",
        "session_id": session_id,
        "source": name_for(code, codes),
        "source_code": code,
        "configured": False,
        "temp_c": 20.0,
    }


# ---------------------------------------------------------------------------
# [HEAT_SOURCES] -- per-node overrides
# ---------------------------------------------------------------------------


@heat_mcp.tool()
async def list_node_overrides(ctx: Context, session_id: str = "default") -> dict:
    """List every per-node inlet-temperature override row.

    Each row carries ``source`` / ``source_code``, ``node_index`` and
    ``temp_c`` (degrees Celsius). Rows are addressed by their 0-based position
    in this list; ``heat_remove_node_override`` shifts later rows down, so
    re-read the list after removing rather than reusing a cached index.
    """
    # wraps: swmm_heat_node_override_count swmm_heat_get_node_override
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_source_kind_codes()

    def _read() -> dict:
        overrides = heat.node_overrides
        rows = []
        for row_index in range(len(overrides)):
            row = overrides[row_index]
            rows.append(
                {
                    "row_index": row_index,
                    "source": name_for(int(row.source), codes),
                    "source_code": int(row.source),
                    "node_index": int(row.node_index),
                    "temp_c": float(row.temp_c),
                }
            )
        return {"count": len(rows), "overrides": rows}

    out = await asyncio.to_thread(_apply, _read)
    out["session_id"] = session_id
    return out


@heat_mcp.tool()
async def set_node_override(
    ctx: Context,
    session_id: str = "default",
    source: str | int = "",
    node_id: str | int = "",
    temp_c: float = 20.0,
) -> dict:
    """Add or update the inlet temperature (degC) for one source at one node.

    Only ``dwf`` and ``external_inflow`` take NODE scope. Every other source is
    refused outright — refused rather than silently deferred to the global
    value, which is the same answer the deck gets.

    ``temp_c`` must lie in ``[-50, 100]`` degrees Celsius; out-of-range values
    are REFUSED, not clamped. ``node_id`` may be a node id string or an index.

    An existing (source, node) pair is UPDATED, not duplicated, so calling this
    twice for the same pair is an edit rather than an error — one row per pair
    either way. Read the rows back with ``heat_list_node_overrides``.
    """
    # wraps: swmm_heat_set_node_override
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_source_kind_codes()
    code = coerce_enum(source, codes, "heat source kind")
    if node_id == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a node_id.")
    val = float(temp_c)

    def _set() -> None:
        heat.node_overrides.set(code, node_id, val)

    await asyncio.to_thread(_apply, _set)
    return {
        "status": "ok",
        "session_id": session_id,
        "source": name_for(code, codes),
        "source_code": code,
        "node_id": node_id,
        "temp_c": val,
    }


@heat_mcp.tool()
async def remove_node_override(
    ctx: Context,
    session_id: str = "default",
    row_index: int = -1,
) -> dict:
    """Remove one per-node override by its row index.

    ``row_index`` is the 0-based position reported by
    ``heat_list_node_overrides``. Later rows shift DOWN by one, so a caller
    removing several rows must re-read the list between removals rather than
    working through a cached set of indices.
    """
    # wraps: swmm_heat_remove_node_override
    if row_index < 0:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a non-negative row_index.")
    _, heat = await _editable_heat(ctx, session_id)
    await asyncio.to_thread(_apply, lambda: heat.node_overrides.remove(int(row_index)))
    return {"status": "ok", "session_id": session_id, "row_index": int(row_index)}


@heat_mcp.tool()
async def get_effective_source_temp(
    ctx: Context,
    session_id: str = "default",
    source: str | int = "",
    node_id: str | int = "",
) -> dict:
    """Report the temperature (degC) one source's water actually enters a node at.

    This is the resolved value the engine itself uses: the per-node override
    when one exists for the (source, node) pair, otherwise the global source
    temperature. Read it here rather than re-deriving the precedence from
    ``heat_list_sources`` and ``heat_list_node_overrides``, which would drift
    from the engine's own resolution.
    """
    # wraps: swmm_heat_get_effective_source_temp
    _, heat = await _editable_heat(ctx, session_id)
    codes = heat_source_kind_codes()
    code = coerce_enum(source, codes, "heat source kind")
    if node_id == "":
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Provide a node_id.")
    value = await asyncio.to_thread(_apply, lambda: float(heat.sources.effective(code, node_id)))
    return {
        "session_id": session_id,
        "source": name_for(code, codes),
        "source_code": code,
        "node_id": node_id,
        "temp_c": value,
    }
