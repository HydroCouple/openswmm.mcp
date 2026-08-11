"""P6 long-tail tools — gage metadata, storage geometry, virtual junctions,
treatment validation, hot-start file sim-time, and the extra renames.

Per ``openswmm.engine/plans/API_GAP_FILL_PLAN_2026-08-09.md`` §3.5.

Runs against the real ``openswmm.engine`` over the shared
``site_drainage_model.inp`` fixture (see ``conftest.py``). The fixture has no
storage node, so the storage-geometry round trip converts ``J2`` first; it has
no patterns or transects, so those renames are only covered by their
argument validation.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _opened(fake_ctx, inp_path, session_id):
    """Open the reference model leniently, landing in the editable 'opened' state."""
    from openswmm_mcp.tools.lifecycle import open_model

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id, lenient_open=True)
    return fake_ctx


async def _initialized(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)
    return fake_ctx


# ---------------------------------------------------------------------------
# Gage metadata (the read-back side of editing.configure_gage)
# ---------------------------------------------------------------------------


class TestGageMetadata:
    async def test_reads_back_what_the_inp_declares(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.editing import get_gage_metadata

        await _initialized(fake_ctx, inp_path, "g_meta")
        out = await get_gage_metadata(fake_ctx, session_id="g_meta", gage_id="RainGage")

        # [RAINGAGES] declares a 0:05 interval against the "2-yr" series.
        assert out["rain_interval"] == pytest.approx(300.0)
        assert out["timeseries_id"] == "2-yr"
        assert out["station_id"] == ""
        assert out["rain_units"] in ("in", "mm")

    async def test_configure_gage_round_trips(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.editing import configure_gage, get_gage_metadata

        # Gage properties are geometry setters (CHECK_GEOMETRY): BUILDING or OPENED only.
        await _opened(fake_ctx, inp_path, "g_rt")
        await configure_gage(fake_ctx, session_id="g_rt", gage_id="RainGage", rain_interval=900.0)
        out = await get_gage_metadata(fake_ctx, session_id="g_rt", gage_id="RainGage")
        assert out["rain_interval"] == pytest.approx(900.0)

    async def test_rain_units_round_trip(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.editing import get_gage_metadata, set_gage_rain_units

        await _opened(fake_ctx, inp_path, "g_units")
        res = await set_gage_rain_units(
            fake_ctx, session_id="g_units", gage_id="RainGage", rain_units="mm"
        )
        assert res["rain_units_code"] == 1

        out = await get_gage_metadata(fake_ctx, session_id="g_units", gage_id="RainGage")
        assert out["rain_units"] == "mm"
        assert out["rain_units_code"] == 1

    async def test_rain_units_rejects_unknown_token(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.editing import set_gage_rain_units

        await _initialized(fake_ctx, inp_path, "g_bad")
        with pytest.raises(ToolError):
            await set_gage_rain_units(
                fake_ctx, session_id="g_bad", gage_id="RainGage", rain_units="cumulative"
            )

    async def test_station_id_round_trip(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.editing import get_gage_metadata, set_gage_station_id

        await _opened(fake_ctx, inp_path, "g_stn")
        await set_gage_station_id(
            fake_ctx, session_id="g_stn", gage_id="RainGage", station_id="STA-42"
        )
        out = await get_gage_metadata(fake_ctx, session_id="g_stn", gage_id="RainGage")
        assert out["station_id"] == "STA-42"


# ---------------------------------------------------------------------------
# Storage shape + geometry
# ---------------------------------------------------------------------------


class TestStorageGeometry:
    async def _storage_node(self, fake_ctx, inp_path, session_id):
        from openswmm_mcp.tools.editing import convert_node

        await _opened(fake_ctx, inp_path, session_id)
        await convert_node(fake_ctx, session_id=session_id, node_id="J2", new_type="storage")
        return "J2"

    async def test_round_trip(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.nodes import get_storage_geometry, set_storage_geometry

        node = await self._storage_node(fake_ctx, inp_path, "st_rt")
        await set_storage_geometry(
            fake_ctx,
            session_id="st_rt",
            node_id=node,
            shape="pyramidal",
            p1=10.0,
            p2=4.0,
            p3=2.0,
        )
        out = await get_storage_geometry(fake_ctx, session_id="st_rt", node_id=node)
        assert out["shape"] == "pyramidal"
        assert out["shape_code"] == 5
        assert out["p1"] == pytest.approx(10.0)
        assert out["p2"] == pytest.approx(4.0)
        assert out["p3"] == pytest.approx(2.0)

    async def test_redimension_without_restating_shape(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.nodes import get_storage_geometry, set_storage_geometry

        node = await self._storage_node(fake_ctx, inp_path, "st_dim")
        await set_storage_geometry(
            fake_ctx, session_id="st_dim", node_id=node, shape="cylindrical", p1=6.0, p2=6.0
        )
        await set_storage_geometry(fake_ctx, session_id="st_dim", node_id=node, p1=8.0, p2=8.0)
        out = await get_storage_geometry(fake_ctx, session_id="st_dim", node_id=node)
        assert out["shape"] == "cylindrical"
        assert out["p1"] == pytest.approx(8.0)

    async def test_unknown_shape_rejected(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.nodes import set_storage_geometry

        node = await self._storage_node(fake_ctx, inp_path, "st_bad")
        with pytest.raises(ToolError):
            await set_storage_geometry(
                fake_ctx, session_id="st_bad", node_id=node, shape="trapezoidal", p1=1.0
            )

    async def test_tabular_and_functional_are_not_settable_here(self, fake_ctx, inp_path):
        """``tabular`` / ``functional`` have their own tools and are rejected."""
        from openswmm_mcp.tools.nodes import set_storage_geometry

        node = await self._storage_node(fake_ctx, inp_path, "st_tab")
        for shape in ("tabular", "functional"):
            with pytest.raises(ToolError):
                await set_storage_geometry(fake_ctx, session_id="st_tab", node_id=node, shape=shape)


# ---------------------------------------------------------------------------
# Virtual-junction predicates
# ---------------------------------------------------------------------------


class TestVirtualJunctions:
    async def test_plain_junction_is_not_virtual(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.nodes import is_virtual

        await _initialized(fake_ctx, inp_path, "vj_is")
        out = await is_virtual(fake_ctx, session_id="vj_is", node_id="J2")
        assert out["is_virtual"] is False

    async def test_eligibility_is_a_dry_run(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.nodes import is_virtual, virtual_eligible

        await _initialized(fake_ctx, inp_path, "vj_el")
        out = await virtual_eligible(fake_ctx, session_id="vj_el", node_id="J2")
        assert isinstance(out["rule_code"], int)
        assert out["eligible"] == (out["rule_code"] == 0)

        # Read-only: the node's virtual flag is unchanged either way.
        after = await is_virtual(fake_ctx, session_id="vj_el", node_id="J2")
        assert after["is_virtual"] is False

    async def test_outfall_is_ineligible(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.nodes import virtual_eligible

        await _initialized(fake_ctx, inp_path, "vj_out")
        out = await virtual_eligible(fake_ctx, session_id="vj_out", node_id="O1")
        # An outfall has one attached conduit, so rule 609 (not exactly two).
        assert out["eligible"] is False
        assert out["rule_code"] != 0


# ---------------------------------------------------------------------------
# Treatment expression validation
# ---------------------------------------------------------------------------


class TestTreatmentValidation:
    async def test_valid_expression(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.quality import treatment_validate_expression

        # The grammar's variables are C R DT HRT Q V D AREA -- C is the pollutant's
        # own concentration. A pollutant name is not an identifier.
        await _initialized(fake_ctx, inp_path, "tv_ok")
        out = await treatment_validate_expression(
            fake_ctx, session_id="tv_ok", expression="C = 0.5 * C"
        )
        assert out["valid"] is True
        assert out["column"] == -1

    async def test_malformed_expression_reports_a_column(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.quality import treatment_validate_expression

        await _initialized(fake_ctx, inp_path, "tv_bad")
        out = await treatment_validate_expression(
            fake_ctx, session_id="tv_bad", expression="C = 0.5 * * C"
        )
        assert out["valid"] is False
        assert out["message"]
        assert isinstance(out["column"], int)

    async def test_nothing_is_written(self, fake_ctx, inp_path):
        """Validation must not install the expression on any node."""
        from openswmm_mcp.tools.quality import treatment_get, treatment_validate_expression

        await _initialized(fake_ctx, inp_path, "tv_ro")
        before = await treatment_get(fake_ctx, session_id="tv_ro", node_id="J2", pollutant_id="TSS")
        await treatment_validate_expression(fake_ctx, session_id="tv_ro", expression="C = 0.5 * C")
        after = await treatment_get(fake_ctx, session_id="tv_ro", node_id="J2", pollutant_id="TSS")
        assert after["expression"] == before["expression"]

    async def test_empty_expression_rejected(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.quality import treatment_validate_expression

        await _initialized(fake_ctx, inp_path, "tv_empty")
        with pytest.raises(ToolError):
            await treatment_validate_expression(fake_ctx, session_id="tv_empty", expression="")


# ---------------------------------------------------------------------------
# Hot-start file sim-time (distinct from the live clock)
# ---------------------------------------------------------------------------


class TestHotstartFileSimTime:
    async def test_matches_the_clock_at_save_time(self, fake_ctx, inp_path, tmp_path):
        from openswmm_mcp.tools.hotstart import get_file_sim_time, save_hotstart
        from openswmm_mcp.tools.lifecycle import (
            get_simulation_time,
            open_model,
            step_simulation,
        )

        await open_model(fake_ctx, inp_path=inp_path, session_id="hs_t")
        await step_simulation(fake_ctx, session_id="hs_t", num_steps=20)
        clock = await get_simulation_time(fake_ctx, session_id="hs_t")

        path = str(tmp_path / "checkpoint.hsf")
        await save_hotstart(fake_ctx, session_id="hs_t", path=path)

        out = await get_file_sim_time(fake_ctx, session_id="hs_t", path=path)
        assert out["path"].endswith("checkpoint.hsf")
        assert out["sim_datetime"]
        assert isinstance(out["sim_datetime_oadate"], float)
        # The file's stamp is the moment the state was captured — i.e. now.
        assert clock is not None

    async def test_missing_file_raises(self, fake_ctx, inp_path, tmp_path):
        from openswmm_mcp.tools.hotstart import get_file_sim_time

        await _initialized(fake_ctx, inp_path, "hs_miss")
        with pytest.raises(ToolError):
            await get_file_sim_time(fake_ctx, session_id="hs_miss", path=str(tmp_path / "nope.hsf"))

    async def test_empty_path_raises(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.hotstart import get_file_sim_time

        await _initialized(fake_ctx, inp_path, "hs_nopath")
        with pytest.raises(ToolError):
            await get_file_sim_time(fake_ctx, session_id="hs_nopath", path="")


# ---------------------------------------------------------------------------
# Remaining renames
# ---------------------------------------------------------------------------


class TestRenames:
    async def test_pollutant(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.editing import rename_pollutant
        from openswmm_mcp.tools.pollutants import get_units

        await _opened(fake_ctx, inp_path, "rn_pol")
        res = await rename_pollutant(
            fake_ctx, session_id="rn_pol", pollutant_id="TSS", new_id="TSS_mgL"
        )
        assert res["status"] == "ok"
        assert res["index"] == 0

        await get_units(fake_ctx, session_id="rn_pol", pollutant_id="TSS_mgL")
        with pytest.raises(ToolError):
            await get_units(fake_ctx, session_id="rn_pol", pollutant_id="TSS")

    async def test_landuse(self, fake_ctx, inp_path):
        from openswmm_mcp.tools.editing import rename_landuse
        from openswmm_mcp.tools.quality import landuse_id

        await _opened(fake_ctx, inp_path, "rn_lu")
        res = await rename_landuse(
            fake_ctx, session_id="rn_lu", landuse_id="Commercial", new_id="Retail"
        )
        idx = res["index"]
        out = await landuse_id(fake_ctx, session_id="rn_lu", index=idx)
        assert out["id"] == "Retail"

    @pytest.mark.parametrize(
        "tool_name,kwargs",
        [
            ("rename_pollutant", {"pollutant_id": "TSS"}),
            ("rename_landuse", {"landuse_id": "Commercial"}),
            ("rename_pattern", {"pattern_id": "P1"}),
            ("rename_transect", {"transect_id": "T1"}),
        ],
    )
    async def test_empty_new_id_rejected(self, fake_ctx, tool_name, kwargs):
        """Validation fires before any session lookup, so no model is needed."""
        import openswmm_mcp.tools.editing as editing

        tool = getattr(editing, tool_name)
        with pytest.raises(ToolError):
            await tool(fake_ctx, session_id="rn_none", new_id="", **kwargs)
