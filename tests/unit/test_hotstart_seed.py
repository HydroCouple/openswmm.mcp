"""P1.3 — hotstart.seed_hotstart_state MCP tool.

Surfaces the engine's hot-start state setters so callers can build
deterministic initial conditions. Exercised against the real
``openswmm.engine`` over the ``site_drainage_model.inp`` fixture (no mocks):
run the model, save a hot-start file, then seed element overrides from it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError
from openswmm_mcp.tools.hotstart import save_hotstart, seed_hotstart_state


async def _open_and_run(fake_ctx, inp_path, session_id="default"):
    from openswmm_mcp.tools.lifecycle import open_model, run_simulation

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)
    await run_simulation(fake_ctx, session_id=session_id)


class TestSeedHotstartState:
    async def test_seed_applies_overrides(self, fake_ctx, inp_path, tmp_path):
        await _open_and_run(fake_ctx, inp_path)
        hsf = str(tmp_path / "seed.hsf")
        await save_hotstart(fake_ctx, session_id="default", path=hsf)

        result = await seed_hotstart_state(
            fake_ctx,
            session_id="default",
            path=hsf,
            node_depths={"J1": 0.5},
            link_flows={"C1": 1.0},
        )
        assert result.status == "seeded"
        assert "2 element override" in result.message

    async def test_missing_path_raises(self, fake_ctx, inp_path):
        await _open_and_run(fake_ctx, inp_path)
        with pytest.raises(ToolError):
            await seed_hotstart_state(fake_ctx, session_id="default", path="")

    async def test_nonexistent_file_raises(self, fake_ctx, inp_path):
        await _open_and_run(fake_ctx, inp_path)
        with pytest.raises(ToolError):
            await seed_hotstart_state(fake_ctx, session_id="default", path="/no/such/file.hsf")
