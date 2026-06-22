"""Climatology configuration tools — temperature, evaporation, wind, snowmelt
globals, areal-depletion curves, and monthly adjustments.

These edit the model's climate *configuration* (the ``[TEMPERATURE]``,
``[EVAPORATION]``, ``[WINDSPEED]``, ``[ADJUSTMENTS]`` sections plus the snowmelt
globals and areal-depletion curves) *before* a run. They complement the runtime
climate-*forcing* tools in :mod:`openswmm_mcp.tools.forcing`, which override the
live temperature / wind / evaporation while a simulation is running.

Reads work once a model is opened (``opened`` and later states). Edits require
the ``opened`` state — once the engine is initialized the configuration has been
baked into the climate/snow solver. Values use the project's display units,
exactly as in the ``.inp`` file. Requires the ``openswmm`` backend.
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

logger = logging.getLogger(__name__)

climate_mcp = FastMCP("climate")

# ---------------------------------------------------------------------------
# Enum name <-> int maps (mirror the C SWMM_TempSource / SWMM_EvapType /
# SWMM_WindType enums). Setters accept either a name or the int.
# ---------------------------------------------------------------------------

_TEMP_SOURCES: dict[str, int] = {"none": 0, "timeseries": 1, "file": 2}
_EVAP_METHODS: dict[str, int] = {
    "constant": 0, "monthly": 1, "timeseries": 2, "temperature": 3, "file": 4,
}
_WIND_SOURCES: dict[str, int] = {"monthly": 0, "file": 1}

_TEMP_SOURCE_NAMES = {v: k for k, v in _TEMP_SOURCES.items()}
_EVAP_METHOD_NAMES = {v: k for k, v in _EVAP_METHODS.items()}
_WIND_SOURCE_NAMES = {v: k for k, v in _WIND_SOURCES.items()}

_MONTHS = 12
_ADC_POINTS = 10


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _editable_climate(ctx: Context, session_id: str):
    """Return ``(session, climate_view)`` for an edit, requiring 'opened'."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Climate configuration editing")
    require_state(session, "opened")
    return session, session.climate


async def _readable_climate(ctx: Context, session_id: str):
    """Return ``(session, climate_view)`` for a read (solver-backed states)."""
    sm = get_session_manager(ctx)
    session = await sm.get_session(session_id)
    require_new_engine(session, "Climate configuration read-back")
    require_state(session, "opened", "initialized", "running", "ended")
    return session, session.climate


def _coerce_enum(value, mapping: dict[str, int], label: str) -> int:
    """Accept an int code or a case-insensitive name; return the int code."""
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {label} must be a name or int code.")
    if isinstance(value, int):
        if value not in mapping.values():
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] {label} code {value} out of range. "
                f"Valid: {sorted(mapping.values())}."
            )
        return value
    key = str(value).strip().lower()
    if key not in mapping:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown {label} '{value}'. "
            f"Valid: {', '.join(sorted(mapping))}."
        )
    return mapping[key]


def _check_len(values, n: int, label: str) -> list[float]:
    seq = list(values)
    if len(seq) != n:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] {label} must have exactly {n} values, "
            f"got {len(seq)}."
        )
    return [float(v) for v in seq]


def _apply(fn) -> None:
    """Run a blocking climate-edit closure, mapping engine/value errors to
    ToolError while letting validation ToolErrors propagate unchanged."""
    try:
        fn()
    except ToolError:
        raise
    except Exception as exc:  # EngineError subclasses + ValueError from the C layer
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] {exc}") from exc


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


@climate_mcp.tool()
async def get_climate_config(ctx: Context, session_id: str = "default") -> dict:
    """Read the full climate configuration of the model (read-only).

    Returns temperature, evaporation, wind, snowmelt-global, areal-depletion,
    and monthly-adjustment settings in the project's display units.
    """
    session, c = await _readable_climate(ctx, session_id)
    dry_only = await asyncio.to_thread(session.forcing.get_climate_dry_only)

    def _read():
        return {
            "temperature": {
                "source": _TEMP_SOURCE_NAMES.get(c.temp_source, c.temp_source),
                "timeseries": c.temp_timeseries,
                "file_start": c.temp_file_start,
                "elevation": c.elevation,
                "latitude": c.latitude,
                "longitude_correction_min": c.longitude_correction,
            },
            "evaporation": {
                "method": _EVAP_METHOD_NAMES.get(c.evap_type, c.evap_type),
                "monthly": c.evap_monthly,
                "timeseries": c.evap_timeseries,
                "pan_coeff": c.pan_coeff,
                "recovery_pattern": c.evap_recovery,
                "dry_only": bool(dry_only),
            },
            "wind": {
                "source": _WIND_SOURCE_NAMES.get(c.wind_type, c.wind_type),
                "monthly": c.wind_monthly,
            },
            "snowmelt": {
                "divide_temp": c.snow_temp,
                "ati_weight": c.ati_weight,
                "neg_melt_ratio": c.neg_melt_ratio,
            },
            "areal_depletion": {
                "impervious": c.adc_impervious,
                "pervious": c.adc_pervious,
            },
            "adjustments": {
                "temperature": c.adjust_temperature,
                "evaporation": c.adjust_evaporation,
                "rainfall": c.adjust_rainfall,
                "conductivity": c.adjust_conductivity,
            },
        }

    cfg = await asyncio.to_thread(_read)
    cfg["session_id"] = session_id
    cfg["units"] = "project display units (same as the .inp); months Jan..Dec"
    return cfg


# ---------------------------------------------------------------------------
# Setters (one per component; all parameters optional — unset = unchanged)
# ---------------------------------------------------------------------------


@climate_mcp.tool()
async def set_temperature_config(
    ctx: Context,
    session_id: str = "default",
    source: str | int | None = None,
    timeseries: str | None = None,
    elevation: float | None = None,
    latitude: float | None = None,
    longitude_correction_min: float | None = None,
    file_start: float | None = None,
) -> dict:
    """Edit [TEMPERATURE] config. ``source`` is none/timeseries/file. Setting
    ``timeseries`` also switches the source to TIMESERIES. ``latitude`` must be
    in [-90, 90]; ``longitude_correction_min`` is minutes of solar-time offset.
    """
    _, c = await _editable_climate(ctx, session_id)
    changed: list[str] = []

    def _do():
        if source is not None:
            c.temp_source = _coerce_enum(source, _TEMP_SOURCES, "temperature source")
            changed.append("source")
        if timeseries is not None:
            c.temp_timeseries = timeseries
            changed.append("timeseries")
        if elevation is not None:
            c.elevation = float(elevation); changed.append("elevation")
        if latitude is not None:
            c.latitude = float(latitude); changed.append("latitude")
        if longitude_correction_min is not None:
            c.longitude_correction = float(longitude_correction_min)
            changed.append("longitude_correction_min")
        if file_start is not None:
            c.temp_file_start = float(file_start); changed.append("file_start")

    await asyncio.to_thread(lambda: _apply(_do))
    return {"status": "applied", "session_id": session_id, "changed": changed}


@climate_mcp.tool()
async def set_evaporation_config(
    ctx: Context,
    session_id: str = "default",
    method: str | int | None = None,
    monthly: list[float] | None = None,
    timeseries: str | None = None,
    pan_coeff: list[float] | None = None,
    recovery_pattern: str | None = None,
    dry_only: bool | None = None,
) -> dict:
    """Edit [EVAPORATION] config. ``method`` is constant/monthly/timeseries/
    temperature/file. ``monthly`` and ``pan_coeff`` are 12 values each. Setting
    ``timeseries`` also switches the method to TIMESERIES.
    """
    session, c = await _editable_climate(ctx, session_id)
    changed: list[str] = []

    def _do():
        if method is not None:
            c.evap_type = _coerce_enum(method, _EVAP_METHODS, "evaporation method")
            changed.append("method")
        if monthly is not None:
            c.evap_monthly = _check_len(monthly, _MONTHS, "monthly"); changed.append("monthly")
        if timeseries is not None:
            c.evap_timeseries = timeseries; changed.append("timeseries")
        if pan_coeff is not None:
            c.pan_coeff = _check_len(pan_coeff, _MONTHS, "pan_coeff"); changed.append("pan_coeff")
        if recovery_pattern is not None:
            c.evap_recovery = recovery_pattern; changed.append("recovery_pattern")

    await asyncio.to_thread(lambda: _apply(_do))
    if dry_only is not None:
        await asyncio.to_thread(session.forcing.climate_dry_only, bool(dry_only))
        changed.append("dry_only")
    return {"status": "applied", "session_id": session_id, "changed": changed}


@climate_mcp.tool()
async def set_windspeed_config(
    ctx: Context,
    session_id: str = "default",
    source: str | int | None = None,
    monthly: list[float] | None = None,
) -> dict:
    """Edit [TEMPERATURE] WINDSPEED config. ``source`` is monthly/file;
    ``monthly`` is 12 average wind speeds.
    """
    _, c = await _editable_climate(ctx, session_id)
    changed: list[str] = []

    def _do():
        if source is not None:
            c.wind_type = _coerce_enum(source, _WIND_SOURCES, "wind source")
            changed.append("source")
        if monthly is not None:
            c.wind_monthly = _check_len(monthly, _MONTHS, "monthly"); changed.append("monthly")

    await asyncio.to_thread(lambda: _apply(_do))
    return {"status": "applied", "session_id": session_id, "changed": changed}


@climate_mcp.tool()
async def set_snowmelt_config(
    ctx: Context,
    session_id: str = "default",
    divide_temp: float | None = None,
    ati_weight: float | None = None,
    neg_melt_ratio: float | None = None,
) -> dict:
    """Edit the [TEMPERATURE] SNOWMELT globals: snow/rain dividing temperature,
    ATI weight (TIPM, 0..1), and negative-melt ratio (RNM, 0..1).
    """
    _, c = await _editable_climate(ctx, session_id)
    changed: list[str] = []

    def _do():
        if divide_temp is not None:
            c.snow_temp = float(divide_temp); changed.append("divide_temp")
        if ati_weight is not None:
            c.ati_weight = float(ati_weight); changed.append("ati_weight")
        if neg_melt_ratio is not None:
            c.neg_melt_ratio = float(neg_melt_ratio); changed.append("neg_melt_ratio")

    await asyncio.to_thread(lambda: _apply(_do))
    return {"status": "applied", "session_id": session_id, "changed": changed}


@climate_mcp.tool()
async def set_areal_depletion(
    ctx: Context,
    session_id: str = "default",
    impervious: list[float] | None = None,
    pervious: list[float] | None = None,
) -> dict:
    """Edit the snow areal-depletion curves (10 fractions in [0, 1] each) for
    impervious and/or pervious surfaces.
    """
    _, c = await _editable_climate(ctx, session_id)
    changed: list[str] = []

    def _do():
        if impervious is not None:
            c.adc_impervious = _check_len(impervious, _ADC_POINTS, "impervious")
            changed.append("impervious")
        if pervious is not None:
            c.adc_pervious = _check_len(pervious, _ADC_POINTS, "pervious")
            changed.append("pervious")

    await asyncio.to_thread(lambda: _apply(_do))
    return {"status": "applied", "session_id": session_id, "changed": changed}


@climate_mcp.tool()
async def set_adjustments(
    ctx: Context,
    session_id: str = "default",
    temperature: list[float] | None = None,
    evaporation: list[float] | None = None,
    rainfall: list[float] | None = None,
    conductivity: list[float] | None = None,
) -> dict:
    """Edit the [ADJUSTMENTS] monthly arrays (12 values each): temperature
    offsets, and evaporation / rainfall / conductivity multipliers. Conductivity
    values <= 0 are stored as 1.0 (legacy behaviour).
    """
    _, c = await _editable_climate(ctx, session_id)
    changed: list[str] = []

    def _do():
        if temperature is not None:
            c.adjust_temperature = _check_len(temperature, _MONTHS, "temperature")
            changed.append("temperature")
        if evaporation is not None:
            c.adjust_evaporation = _check_len(evaporation, _MONTHS, "evaporation")
            changed.append("evaporation")
        if rainfall is not None:
            c.adjust_rainfall = _check_len(rainfall, _MONTHS, "rainfall")
            changed.append("rainfall")
        if conductivity is not None:
            c.adjust_conductivity = _check_len(conductivity, _MONTHS, "conductivity")
            changed.append("conductivity")

    await asyncio.to_thread(lambda: _apply(_do))
    return {"status": "applied", "session_id": session_id, "changed": changed}
