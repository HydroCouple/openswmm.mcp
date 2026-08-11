"""forcing.get_climate_evap_rate — climate-derived PET read-back.

Runs against the real ``openswmm.engine`` over the reference 1D fixture.
Skips cleanly when the installed engine build predates
``Forcing.climate_evap_rate``.
"""

from __future__ import annotations

import pytest

eng = pytest.importorskip("openswmm.engine")
if not hasattr(eng.Forcing, "climate_evap_rate"):
    pytest.skip(
        "openswmm.engine build predates Forcing.climate_evap_rate (rebuild the engine wheel)",
        allow_module_level=True,
    )

from openswmm_mcp.errors import ToolError  # noqa: E402
from openswmm_mcp.tools.forcing import (  # noqa: E402
    get_climate_evap_rate,
    get_climate_state,
    set_climate_dry_only,
    set_climate_forcing,
)


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):
        pass


async def _open_and_run(session_manager, tmp_inp, session_id="default"):
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=tmp_inp, session_id=session_id)
    await step_simulation(ctx, session_id=session_id, num_steps=1)
    return ctx


def _aligned_climate_inp(tmp_path_dir, tmp_inp):
    """Copy the site model with the runoff steps aligned to the routing step.

    Climate state (temperature / wind) refreshes on the runoff clock, not
    every routing step, so over the model's WET_STEP / DRY_STEP a single
    step() would not fire a climate update. Rewriting WET_STEP and DRY_STEP to
    the ROUTING_STEP value makes each step() trigger exactly one climate
    refresh so a forcing read-back is observable. Mirrors the engine suite's
    ``_aligned_model``.
    """
    import os
    import re

    with open(tmp_inp) as f:
        text = f.read()
    m = re.search(r"^ROUTING_STEP\s+(\S+)", text, flags=re.MULTILINE)
    routing = m.group(1) if m else "0:00:05"
    text = re.sub(r"^(WET_STEP\s+)\S+", rf"\g<1>{routing}", text, flags=re.MULTILINE)
    text = re.sub(r"^(DRY_STEP\s+)\S+", rf"\g<1>{routing}", text, flags=re.MULTILINE)
    path = os.path.join(tmp_path_dir, "climate_aligned.inp")
    with open(path, "w") as f:
        f.write(text)
    return path


class TestGetClimateEvapRate:
    async def test_returns_rate(self, session_manager, tmp_inp):
        ctx = await _open_and_run(session_manager, tmp_inp)
        out = await get_climate_evap_rate(ctx, session_id="default")
        assert isinstance(out["evap_rate"], float)
        assert out["evap_rate"] >= 0.0
        assert out["session_id"] == "default"

    async def test_requires_running_state(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="not_running")
        with pytest.raises(ToolError):
            await get_climate_evap_rate(ctx, session_id="not_running")


class TestSetClimateForcing:
    async def test_temperature_applies_and_reads_back(self, tmp_path, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import step_simulation

        aligned = _aligned_climate_inp(str(tmp_path), tmp_inp)
        ctx = await _open_and_run(session_manager, aligned)
        out = await set_climate_forcing(
            ctx, session_id="default", variable="temperature", value=55.0, persist=True
        )
        assert out["status"] == "applied"
        assert out["variable"] == "temperature"
        # Climate state refreshes on the runoff clock; with the runoff step
        # aligned to the routing step, one step fires the climate update that
        # consumes the (persistent) forcing.
        await step_simulation(ctx, session_id="default", num_steps=1)
        state = await get_climate_state(ctx, session_id="default")
        assert state["temperature"] == pytest.approx(55.0)

    async def test_wind_applies_and_reads_back(self, tmp_path, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import step_simulation

        aligned = _aligned_climate_inp(str(tmp_path), tmp_inp)
        ctx = await _open_and_run(session_manager, aligned)
        await set_climate_forcing(
            ctx, session_id="default", variable="wind", value=12.0, persist=True
        )
        await step_simulation(ctx, session_id="default", num_steps=1)
        state = await get_climate_state(ctx, session_id="default")
        assert state["wind_speed"] == pytest.approx(12.0)

    async def test_evap_applies(self, session_manager, tmp_inp):
        ctx = await _open_and_run(session_manager, tmp_inp)
        out = await set_climate_forcing(ctx, session_id="default", variable="evap", value=0.25)
        assert out["status"] == "applied"
        assert out["variable"] == "evap"

    async def test_invalid_variable_rejected(self, session_manager, tmp_inp):
        ctx = await _open_and_run(session_manager, tmp_inp)
        with pytest.raises(ToolError):
            await set_climate_forcing(ctx, session_id="default", variable="humidity", value=1.0)

    async def test_requires_running_state(self, session_manager, tmp_inp):
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=tmp_inp, session_id="not_running")
        with pytest.raises(ToolError):
            await set_climate_forcing(
                ctx, session_id="not_running", variable="temperature", value=50.0
            )


class TestSetClimateDryOnly:
    async def test_toggle_and_read_back(self, session_manager, tmp_inp):
        ctx = await _open_and_run(session_manager, tmp_inp)
        out = await set_climate_dry_only(ctx, session_id="default", flag=True)
        assert out["dry_only"] is True
        state = await get_climate_state(ctx, session_id="default")
        assert state["dry_only"] is True
        out2 = await set_climate_dry_only(ctx, session_id="default", flag=False)
        assert out2["dry_only"] is False
