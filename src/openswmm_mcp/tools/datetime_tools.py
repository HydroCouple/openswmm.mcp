"""Date/time conversion utilities.

Thin wrappers over :mod:`openswmm.engine.datetime_api` (the SWMM DateTime C
API from ``openswmm_datetime.h``). SWMM's native DateTime is a ``double``
whose integer part is days since 1899-12-30 (the "OLE automation date"
epoch) and whose fractional part is the time-of-day fraction.

These tools are pure functions — they take no session and touch no engine
state, so they work without an open model. They let an agent (or GUI) do
the same SWMM-exact date math the engine uses internally: composing/parsing
report and event timestamps, advancing a DateTime by an elapsed interval, or
measuring the gap between two timestamps.
"""

from __future__ import annotations

import asyncio

from fastmcp import Context, FastMCP
from openswmm.engine import datetime_api

from openswmm_mcp.errors import ErrorCode, ToolError

datetime_mcp = FastMCP("datetime")


@datetime_mcp.tool()
async def encode_date(ctx: Context, year: int = 2000, month: int = 1, day: int = 1) -> dict:
    """Encode a calendar date as a SWMM DateTime (days since 1899-12-30).

    The returned ``value`` has a zero time-of-day fraction; add a time
    component with :func:`encode_time` (sum the two values) or advance it
    with :func:`add_seconds`.
    """
    if not (1 <= month <= 12):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] month must be 1..12; got {month}.")
    if not (1 <= day <= 31):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] day must be 1..31; got {day}.")
    value = await asyncio.to_thread(datetime_api.encode_date, int(year), int(month), int(day))
    return {"year": year, "month": month, "day": day, "value": value}


@datetime_mcp.tool()
async def encode_time(ctx: Context, hour: int = 0, minute: int = 0, second: int = 0) -> dict:
    """Encode a time-of-day as the fractional part of a SWMM DateTime."""
    if not (0 <= hour <= 23):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] hour must be 0..23; got {hour}.")
    if not (0 <= minute <= 59):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] minute must be 0..59; got {minute}.")
    if not (0 <= second <= 59):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] second must be 0..59; got {second}.")
    value = await asyncio.to_thread(datetime_api.encode_time, int(hour), int(minute), int(second))
    return {"hour": hour, "minute": minute, "second": second, "value": value}


@datetime_mcp.tool()
async def decode_date(ctx: Context, value: float = 0.0) -> dict:
    """Decode the calendar date (year, month, day) from a SWMM DateTime."""
    year, month, day = await asyncio.to_thread(datetime_api.decode_date, float(value))
    return {"value": value, "year": year, "month": month, "day": day}


@datetime_mcp.tool()
async def decode_time(ctx: Context, value: float = 0.0) -> dict:
    """Decode the time-of-day (hour, minute, second) from a SWMM DateTime."""
    hour, minute, second = await asyncio.to_thread(datetime_api.decode_time, float(value))
    return {"value": value, "hour": hour, "minute": minute, "second": second}


@datetime_mcp.tool()
async def add_seconds(ctx: Context, value: float = 0.0, seconds: float = 0.0) -> dict:
    """Advance a SWMM DateTime by a number of seconds (may be negative)."""
    out = await asyncio.to_thread(datetime_api.add_seconds, float(value), float(seconds))
    return {"value": value, "seconds": seconds, "result": out}


@datetime_mcp.tool()
async def time_diff(ctx: Context, value1: float = 0.0, value2: float = 0.0) -> dict:
    """Return ``value1 - value2`` as a whole number of seconds."""
    diff = await asyncio.to_thread(datetime_api.time_diff, float(value1), float(value2))
    return {"value1": value1, "value2": value2, "seconds": diff}
