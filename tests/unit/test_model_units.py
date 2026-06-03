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
from openswmm_mcp.tools.model import get_unit_system


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
