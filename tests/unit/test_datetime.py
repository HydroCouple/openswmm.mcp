"""Unit tests for the datetime MCP tool surface.

Pure SWMM DateTime conversion utilities — no session/engine state required,
but they call the compiled ``openswmm.engine.datetime_api``, so the suite is
skipped when the engine is not importable.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError


class MockContext:
    def __init__(self, session_manager=None):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):  # noqa: ARG002
        pass


class TestEncodeDecodeRoundtrip:
    async def test_date_roundtrip(self):
        from openswmm_mcp.tools.datetime_tools import decode_date, encode_date

        ctx = MockContext()
        enc = await encode_date(ctx, year=2024, month=6, day=14)
        assert enc["value"] > 0
        dec = await decode_date(ctx, value=enc["value"])
        assert (dec["year"], dec["month"], dec["day"]) == (2024, 6, 14)

    async def test_time_roundtrip(self):
        from openswmm_mcp.tools.datetime_tools import decode_time, encode_time

        ctx = MockContext()
        enc = await encode_time(ctx, hour=13, minute=30, second=15)
        assert 0.0 <= enc["value"] < 1.0
        dec = await decode_time(ctx, value=enc["value"])
        assert (dec["hour"], dec["minute"], dec["second"]) == (13, 30, 15)


class TestArithmetic:
    async def test_add_seconds_advances_one_day(self):
        from openswmm_mcp.tools.datetime_tools import add_seconds, encode_date

        ctx = MockContext()
        base = (await encode_date(ctx, year=2024, month=1, day=1))["value"]
        out = await add_seconds(ctx, value=base, seconds=86400.0)
        assert out["result"] == pytest.approx(base + 1.0)

    async def test_time_diff_is_seconds(self):
        from openswmm_mcp.tools.datetime_tools import encode_date, time_diff

        ctx = MockContext()
        d1 = (await encode_date(ctx, year=2024, month=1, day=2))["value"]
        d2 = (await encode_date(ctx, year=2024, month=1, day=1))["value"]
        diff = await time_diff(ctx, value1=d1, value2=d2)
        assert diff["seconds"] == 86400


class TestValidation:
    async def test_bad_month_rejected(self):
        from openswmm_mcp.tools.datetime_tools import encode_date

        ctx = MockContext()
        with pytest.raises(ToolError, match="month must be 1..12"):
            await encode_date(ctx, year=2024, month=13, day=1)

    async def test_bad_hour_rejected(self):
        from openswmm_mcp.tools.datetime_tools import encode_time

        ctx = MockContext()
        with pytest.raises(ToolError, match="hour must be 0..23"):
            await encode_time(ctx, hour=24, minute=0, second=0)
