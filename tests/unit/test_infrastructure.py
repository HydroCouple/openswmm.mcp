"""Unit tests for the infrastructure MCP tool surface.

Covers [TRANSECTS], [STREETS], [INLETS], [LID_CONTROLS], and [LID_USAGE]
design-time configuration.

All ``add_*`` operations require ``SWMM_STATE_BUILDING`` per the engine
contract (``openswmm_infrastructure.h``). Tests therefore set up sessions
via ``building.create_model`` rather than ``lifecycle.open_model``.

Read-only tools and validation-error paths use opened sessions where
convenient.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):  # noqa: ARG002
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_building_session(session_manager, session_id: str = "infra"):
    """Create a building session via building.create_model and return ctx."""
    from openswmm_mcp.tools.building import create_model

    ctx = MockContext(session_manager)
    await create_model(ctx, session_id=session_id)
    return ctx


async def _opened_session(session_manager, inp_path: str, session_id: str = "infra_op"):
    """Open the reference .inp via lifecycle.open_model and return the ctx."""
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    return ctx


# ===========================================================================
# Read-only counts on a building session (0 across the board)
# ===========================================================================


class TestCounts:
    async def test_all_counts_zero_on_empty_building(self, session_manager):
        from openswmm_mcp.tools.infrastructure import (
            inlet_count,
            lid_count,
            street_count,
            transect_count,
        )

        ctx = await _create_building_session(session_manager, "infra_counts")
        for tool in (transect_count, street_count, inlet_count, lid_count):
            result = await tool(ctx, session_id="infra_counts")
            assert result["count"] == 0


# ===========================================================================
# [TRANSECTS]
# ===========================================================================


class TestTransects:
    async def test_add_transect_returns_index(self, session_manager):
        from openswmm_mcp.tools.infrastructure import add_transect, transect_count

        ctx = await _create_building_session(session_manager, "infra_tx_add")
        result = await add_transect(
            ctx, session_id="infra_tx_add", transect_id="TX1",
        )
        assert result["status"] == "ok"
        assert result["id"] == "TX1"
        assert result["index"] == 0

        c = await transect_count(ctx, session_id="infra_tx_add")
        assert c["count"] == 1

    async def test_empty_transect_id_rejected(self, session_manager):
        from openswmm_mcp.tools.infrastructure import add_transect

        ctx = await _create_building_session(session_manager, "infra_tx_empty")
        with pytest.raises(ToolError, match="transect_id must not be empty"):
            await add_transect(ctx, session_id="infra_tx_empty", transect_id="")

    async def test_set_roughness_and_add_stations(self, session_manager):
        from openswmm_mcp.tools.infrastructure import (
            add_transect,
            add_transect_station,
            set_transect_roughness,
        )

        ctx = await _create_building_session(session_manager, "infra_tx_full")
        await add_transect(ctx, session_id="infra_tx_full", transect_id="TX_FULL")
        await set_transect_roughness(
            ctx,
            session_id="infra_tx_full",
            transect_index=0,
            n_left=0.05, n_right=0.05, n_channel=0.03,
        )
        # Stations along an asymmetric overbank profile.
        for s, e in [(0, 100), (10, 95), (20, 90), (30, 95), (40, 100)]:
            r = await add_transect_station(
                ctx, session_id="infra_tx_full",
                transect_index=0, station=float(s), elevation=float(e),
            )
            assert r["status"] == "ok"


# ===========================================================================
# [STREETS]
# ===========================================================================


class TestStreets:
    async def test_add_street_and_set_params(self, session_manager):
        from openswmm_mcp.tools.infrastructure import (
            add_street,
            set_street_params,
            street_count,
        )

        ctx = await _create_building_session(session_manager, "infra_st")
        added = await add_street(ctx, session_id="infra_st", street_id="ST1")
        assert added["index"] == 0

        result = await set_street_params(
            ctx,
            session_id="infra_st",
            street_index=0,
            t_crown=20.0,
            h_curb=0.5,
            sx=0.02,
            n_road=0.016,
            gutter_depres=0.0,
            gutter_width=0.0,
            sides=2,
            back_width=0.0,
            back_slope=0.0,
            back_n=0.0,
        )
        assert result["status"] == "ok"
        assert (await street_count(ctx, session_id="infra_st"))["count"] == 1

    async def test_invalid_sides_rejected(self, session_manager):
        from openswmm_mcp.tools.infrastructure import (
            add_street,
            set_street_params,
        )

        ctx = await _create_building_session(session_manager, "infra_st_bad")
        await add_street(ctx, session_id="infra_st_bad", street_id="ST")
        with pytest.raises(ToolError, match="sides must be 1 or 2"):
            await set_street_params(
                ctx, session_id="infra_st_bad", street_index=0, sides=3,
            )

    async def test_empty_street_id_rejected(self, session_manager):
        from openswmm_mcp.tools.infrastructure import add_street

        ctx = await _create_building_session(session_manager, "infra_st_empty")
        with pytest.raises(ToolError, match="street_id must not be empty"):
            await add_street(ctx, session_id="infra_st_empty", street_id="")


# ===========================================================================
# [INLETS]
# ===========================================================================


class TestInlets:
    async def test_add_inlet_requires_type(self, session_manager):
        from openswmm_mcp.tools.infrastructure import add_inlet

        ctx = await _create_building_session(session_manager, "infra_in_t")
        with pytest.raises(ToolError, match="inlet_type must not be empty"):
            await add_inlet(
                ctx, session_id="infra_in_t", inlet_id="I1", inlet_type="",
            )


# ===========================================================================
# [LID_CONTROLS]
# ===========================================================================


class TestLidControls:
    async def test_add_lid_by_string_type(self, session_manager):
        from openswmm_mcp.tools.infrastructure import add_lid, lid_count

        ctx = await _create_building_session(session_manager, "infra_lid_s")
        result = await add_lid(
            ctx, session_id="infra_lid_s", lid_id="BIO1", lid_type="bio_cell",
        )
        assert result["status"] == "ok"
        assert result["index"] == 0
        assert (await lid_count(ctx, session_id="infra_lid_s"))["count"] == 1

    async def test_add_lid_by_int_type(self, session_manager):
        from openswmm_mcp.tools.infrastructure import add_lid

        ctx = await _create_building_session(session_manager, "infra_lid_i")
        result = await add_lid(
            ctx, session_id="infra_lid_i", lid_id="L", lid_type=5,  # RAIN_BARREL
        )
        assert result["status"] == "ok"

    async def test_unknown_lid_type_rejected(self, session_manager):
        from openswmm_mcp.tools.infrastructure import add_lid

        ctx = await _create_building_session(session_manager, "infra_lid_bad")
        with pytest.raises(ToolError, match="Unknown lid_type"):
            await add_lid(
                ctx, session_id="infra_lid_bad", lid_id="L", lid_type="bogus",
            )

    async def test_lid_layer_setters_all_succeed(self, session_manager):
        from openswmm_mcp.tools.infrastructure import (
            add_lid,
            set_lid_drain,
            set_lid_soil,
            set_lid_storage,
            set_lid_surface,
        )

        ctx = await _create_building_session(session_manager, "infra_lid_layers")
        await add_lid(
            ctx, session_id="infra_lid_layers", lid_id="L_FULL",
            lid_type="bio_cell",
        )
        r1 = await set_lid_surface(
            ctx, session_id="infra_lid_layers", lid_index=0,
            storage=6.0, roughness=0.1, slope=0.01,
        )
        r2 = await set_lid_soil(
            ctx, session_id="infra_lid_layers", lid_index=0,
            thick=12.0, porosity=0.5, fc=0.2, wp=0.1, ksat=0.5, kslope=10.0,
        )
        r3 = await set_lid_storage(
            ctx, session_id="infra_lid_layers", lid_index=0,
            thick=12.0, void_frac=0.75, ksat=0.5,
        )
        r4 = await set_lid_drain(
            ctx, session_id="infra_lid_layers", lid_index=0,
            coeff=0.5, expon=0.5, offset=0.0,
        )
        for r in (r1, r2, r3, r4):
            assert r["status"] == "ok"


# ===========================================================================
# [LID_USAGE]
#
# The reference site_drainage_example.inp has 7 subcatchments (S1..S7).
# add_lid_usage takes an integer subcatch index — we use an opened session
# (rather than an empty building session) so the subcatchments resolve.
# lid_add still happens before initialize via building state on a separate
# session would not share subcatchments, so the LID-usage tests just verify
# the resolver + validation rather than the engine round-trip.
# ===========================================================================


class TestLidUsage:
    async def test_unknown_subcatch_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.infrastructure import add_lid_usage

        ctx = await _opened_session(session_manager, inp_path, "infra_usage_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await add_lid_usage(
                ctx, session_id="infra_usage_bad",
                subcatch_id="NOSUCH", lid_index=0,
                number=1, area=1.0, width=1.0, init_sat=0.0, from_imperv=1.0,
            )

    async def test_invalid_area_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.infrastructure import add_lid_usage

        ctx = await _opened_session(session_manager, inp_path, "infra_usage_a")
        with pytest.raises(ToolError, match="area must be positive"):
            await add_lid_usage(
                ctx, session_id="infra_usage_a", subcatch_id="S1", lid_index=0,
                number=1, area=0.0, width=1.0, init_sat=0.0, from_imperv=1.0,
            )

    async def test_init_sat_out_of_range_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.infrastructure import add_lid_usage

        ctx = await _opened_session(session_manager, inp_path, "infra_usage_is")
        with pytest.raises(ToolError, match=r"init_sat must be in \[0, 1\]"):
            await add_lid_usage(
                ctx, session_id="infra_usage_is", subcatch_id="S1", lid_index=0,
                number=1, area=1.0, width=1.0, init_sat=1.5, from_imperv=1.0,
            )

    async def test_from_imperv_out_of_range_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.infrastructure import add_lid_usage

        ctx = await _opened_session(session_manager, inp_path, "infra_usage_fi")
        with pytest.raises(ToolError, match=r"from_imperv must be in \[0, 1\]"):
            await add_lid_usage(
                ctx, session_id="infra_usage_fi", subcatch_id="S1", lid_index=0,
                number=1, area=1.0, width=1.0, init_sat=0.0, from_imperv=1.5,
            )

    async def test_number_below_one_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.infrastructure import add_lid_usage

        ctx = await _opened_session(session_manager, inp_path, "infra_usage_n")
        with pytest.raises(ToolError, match="number must be >= 1"):
            await add_lid_usage(
                ctx, session_id="infra_usage_n", subcatch_id="S1", lid_index=0,
                number=0, area=1.0, width=1.0, init_sat=0.0, from_imperv=1.0,
            )

    async def test_empty_subcatch_id_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.infrastructure import add_lid_usage

        ctx = await _opened_session(session_manager, inp_path, "infra_usage_e")
        with pytest.raises(ToolError, match="subcatch_id must not be empty"):
            await add_lid_usage(
                ctx, session_id="infra_usage_e", subcatch_id="", lid_index=0,
                number=1, area=1.0, width=1.0, init_sat=0.0, from_imperv=1.0,
            )


# ===========================================================================
# Backend guard
# ===========================================================================


class TestLegacyGuard:
    async def test_legacy_backend_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.infrastructure import lid_count

        session = await session_manager.create_session(
            session_id="legacy_infra",
            inp_path=inp_path,
            engine="legacy",
        )
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(session_manager)
        with pytest.raises(ToolError, match="not supported|legacy"):
            await lid_count(ctx, session_id="legacy_infra")
