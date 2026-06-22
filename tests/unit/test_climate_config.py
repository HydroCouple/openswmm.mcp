"""Unit tests for the climatology configuration MCP tools (tools/climate.py)."""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):
        pass


async def _opened_session(session_manager, inp_path, session_id: str):
    """Create an 'opened' (editable, not yet initialized) openswmm session."""
    session = await session_manager.create_session(
        session_id=session_id, inp_path=inp_path, engine="openswmm"
    )
    session.backend.solver.open()
    session.state = "opened"
    return session


class TestGetClimateConfig:
    async def test_returns_all_components(self, session_manager, inp_path):
        from openswmm_mcp.tools.climate import get_climate_config

        await _opened_session(session_manager, inp_path, "g")
        cfg = await get_climate_config(MockContext(session_manager), session_id="g")

        for key in ("temperature", "evaporation", "wind", "snowmelt",
                    "areal_depletion", "adjustments"):
            assert key in cfg
        assert len(cfg["evaporation"]["monthly"]) == 12
        assert len(cfg["wind"]["monthly"]) == 12
        assert len(cfg["areal_depletion"]["impervious"]) == 10
        assert len(cfg["adjustments"]["conductivity"]) == 12


class TestSetClimateConfig:
    async def test_round_trip_all_components(self, session_manager, inp_path):
        from openswmm_mcp.tools.climate import (
            get_climate_config,
            set_adjustments,
            set_areal_depletion,
            set_evaporation_config,
            set_snowmelt_config,
            set_temperature_config,
            set_windspeed_config,
        )

        ctx = MockContext(session_manager)
        await _opened_session(session_manager, inp_path, "s")

        await set_temperature_config(
            ctx, session_id="s",
            latitude=41.5, elevation=200.0, longitude_correction_min=120.0,
        )
        await set_evaporation_config(
            ctx, session_id="s", method="monthly", monthly=[0.2] * 12, dry_only=True,
        )
        await set_windspeed_config(ctx, session_id="s", source="monthly", monthly=[3.0] * 12)
        await set_snowmelt_config(
            ctx, session_id="s", divide_temp=33.0, ati_weight=0.3, neg_melt_ratio=0.4,
        )
        await set_areal_depletion(
            ctx, session_id="s",
            impervious=[1, .9, .8, .7, .6, .5, .4, .3, .2, .1],
        )
        # conductivity 0.0 must clamp to 1.0 (legacy behaviour)
        await set_adjustments(
            ctx, session_id="s",
            temperature=[1.0] * 12, conductivity=[2.0, 0.0] + [1.0] * 10,
        )

        cfg = await get_climate_config(ctx, session_id="s")
        assert cfg["temperature"]["latitude"] == 41.5
        assert cfg["temperature"]["elevation"] == 200.0
        assert cfg["temperature"]["longitude_correction_min"] == 120.0
        assert cfg["evaporation"]["method"] == "monthly"
        assert cfg["evaporation"]["monthly"] == [0.2] * 12
        assert cfg["evaporation"]["dry_only"] is True
        assert cfg["wind"]["source"] == "monthly"
        assert cfg["wind"]["monthly"] == [3.0] * 12
        assert cfg["snowmelt"]["divide_temp"] == 33.0
        assert cfg["snowmelt"]["ati_weight"] == 0.3
        assert cfg["areal_depletion"]["impervious"][0] == 1.0
        assert cfg["adjustments"]["temperature"] == [1.0] * 12
        assert cfg["adjustments"]["conductivity"][0] == 2.0
        assert cfg["adjustments"]["conductivity"][1] == 1.0  # 0.0 -> 1.0

    async def test_set_temp_timeseries_switches_source(self, session_manager, inp_path):
        from openswmm_mcp.tools.climate import get_climate_config, set_temperature_config

        ctx = MockContext(session_manager)
        await _opened_session(session_manager, inp_path, "ts")
        await set_temperature_config(ctx, session_id="ts", source="timeseries")
        cfg = await get_climate_config(ctx, session_id="ts")
        assert cfg["temperature"]["source"] == "timeseries"


class TestValidation:
    async def test_invalid_values_raise(self, session_manager, inp_path):
        from openswmm_mcp.tools.climate import (
            set_areal_depletion,
            set_evaporation_config,
            set_temperature_config,
            set_windspeed_config,
        )

        ctx = MockContext(session_manager)
        await _opened_session(session_manager, inp_path, "v")

        with pytest.raises(ToolError):
            await set_temperature_config(ctx, session_id="v", latitude=200.0)
        with pytest.raises(ToolError):
            await set_temperature_config(ctx, session_id="v", source="bogus")
        with pytest.raises(ToolError):
            await set_evaporation_config(ctx, session_id="v", method="nope")
        with pytest.raises(ToolError):
            await set_areal_depletion(ctx, session_id="v", impervious=[0.5] * 9)
        with pytest.raises(ToolError):
            await set_windspeed_config(ctx, session_id="v", monthly=[1.0] * 5)


class TestLifecycle:
    async def test_edit_rejected_after_initialize(self, session_manager, inp_path):
        from openswmm_mcp.tools.climate import set_snowmelt_config

        session = await _opened_session(session_manager, inp_path, "li")
        session.backend.solver.initialize()
        session.state = "initialized"

        with pytest.raises(ToolError):
            await set_snowmelt_config(
                MockContext(session_manager), session_id="li", divide_temp=30.0,
            )
