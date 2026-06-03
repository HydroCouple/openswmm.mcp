"""P2.1 — model.list_aquifers / list_snowpacks MCP tools.

Surfaces the previously-unexposed ``solver.aquifers`` / ``solver.snowpacks``
collections. Run against the real engine over the fixture model (no mocks);
the fixture need not contain aquifers/snowpacks — the tools must still return
a well-formed ``{count, ids}`` envelope.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError
from openswmm_mcp.tools.model import get_pattern_factors, list_aquifers, list_snowpacks


async def _open(fake_ctx, inp_path, session_id="default"):
    from openswmm_mcp.tools.lifecycle import open_model

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)


class TestListNamedCollections:
    async def test_list_aquifers_shape(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        result = await list_aquifers(fake_ctx, session_id="default")
        assert result["session_id"] == "default"
        assert isinstance(result["ids"], list)
        assert result["count"] == len(result["ids"])

    async def test_list_snowpacks_shape(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        result = await list_snowpacks(fake_ctx, session_id="default")
        assert isinstance(result["ids"], list)
        assert result["count"] == len(result["ids"])


class TestGetPatternFactors:
    async def test_empty_id_raises(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        with pytest.raises(ToolError):
            await get_pattern_factors(fake_ctx, session_id="default", pattern_id="")

    async def test_unknown_pattern_raises(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path)
        with pytest.raises(ToolError):
            await get_pattern_factors(
                fake_ctx, session_id="default", pattern_id="__no_such_pattern__"
            )
