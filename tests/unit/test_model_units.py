"""P0.2 — model.get_unit_system MCP tool.

Verifies the unit-discovery tool against the real ``openswmm.engine`` over the
``site_drainage_model.inp`` fixture (a CFS / US-customary model). The tool
exists because the engine returns every quantity in the units declared in the
``.inp`` file, so a client must be able to ask which units those are.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError
from openswmm_mcp.tools.model import get_report_start, get_unit_system, set_report_start


async def _open(fake_ctx, inp_path, session_id="default"):
    from openswmm_mcp.tools.lifecycle import open_model

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)


class TestGetUnitSystem:
    async def test_cfs_model_reports_us(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        result = await get_unit_system(fake_ctx, session_id="default")
        assert result["flow_units"] == "CFS"
        assert result["unit_system"] == "US"
        assert result["session_id"] == "default"

    async def test_unknown_session_raises(self, fake_ctx):
        # No session opened -> the dependency layer should surface a ToolError.
        with pytest.raises(ToolError):
            await get_unit_system(fake_ctx, session_id="does-not-exist")


class TestReportStart:
    async def test_get_returns_iso_string(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        result = await get_report_start(fake_ctx, session_id="default")
        assert result["session_id"] == "default"
        # Fixture report start is 01/01/1998 00:00.
        assert result["report_start"].startswith("1998-01-01")

    async def test_set_then_get_roundtrip(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        await set_report_start(fake_ctx, session_id="default", report_start="1998-01-01T03:00:00")
        result = await get_report_start(fake_ctx, session_id="default")
        assert result["report_start"] == "1998-01-01T03:00:00"

    async def test_empty_rejected(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        with pytest.raises(ToolError):
            await set_report_start(fake_ctx, session_id="default", report_start="")

    async def test_bad_format_rejected(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        with pytest.raises(ToolError):
            await set_report_start(fake_ctx, session_id="default", report_start="not-a-date")
