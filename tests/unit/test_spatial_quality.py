"""Tests for openswmm_mcp.tools.spatial_quality -- coordinates, quality, treatment, LID."""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import SpatialResult
from openswmm_mcp.tools.spatial_quality import (
    add_lid,
    get_all_coordinates,
    get_coordinates,
    get_quality,
    set_coordinates,
    set_gage_coord,
    set_node_coords_bulk,
    set_treatment,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _open(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)


async def _open_and_step(fake_ctx, inp_path, session_id):
    from openswmm_mcp.tools.lifecycle import open_model, step_simulation

    await open_model(fake_ctx, inp_path=inp_path, session_id=session_id)
    await step_simulation(fake_ctx, session_id=session_id, num_steps=1)


# ---------------------------------------------------------------------------
# get_coordinates / set_coordinates
# ---------------------------------------------------------------------------


class TestGetCoordinates:
    async def test_get_node_coordinates_after_set(self, fake_ctx, inp_path, reference_model):
        """set_coordinates then get_coordinates round-trips for a node."""
        await _open(fake_ctx, inp_path, "coord_node")
        await set_coordinates(
            fake_ctx,
            session_id="coord_node",
            element_type="node",
            element_id=reference_model.FIRST_NODE_ID,
            x=300.0,
            y=400.0,
        )
        result = await get_coordinates(
            fake_ctx,
            session_id="coord_node",
            element_type="node",
            element_id=reference_model.FIRST_NODE_ID,
        )

        assert isinstance(result, SpatialResult)
        assert result.element_type == "node"
        assert result.element_id == reference_model.FIRST_NODE_ID
        assert result.x == pytest.approx(300.0)
        assert result.y == pytest.approx(400.0)

    async def test_get_link_coordinates_after_set(self, fake_ctx, inp_path, reference_model):
        """set_coordinates then get_coordinates round-trips for a link."""
        await _open(fake_ctx, inp_path, "coord_link")
        await set_coordinates(
            fake_ctx,
            session_id="coord_link",
            element_type="link",
            element_id=reference_model.FIRST_LINK_ID,
            x=50.0,
            y=60.0,
        )
        result = await get_coordinates(
            fake_ctx,
            session_id="coord_link",
            element_type="link",
            element_id=reference_model.FIRST_LINK_ID,
        )

        assert isinstance(result, SpatialResult)
        assert result.element_type == "link"
        assert result.x == pytest.approx(50.0)
        assert result.y == pytest.approx(60.0)

    async def test_get_subcatchment_coordinates_after_set(
        self, fake_ctx, inp_path, reference_model
    ):
        """set_coordinates then get_coordinates round-trips for a subcatchment."""
        await _open(fake_ctx, inp_path, "coord_sc")
        await set_coordinates(
            fake_ctx,
            session_id="coord_sc",
            element_type="subcatchment",
            element_id=reference_model.FIRST_SUBCATCH_ID,
            x=10.0,
            y=20.0,
        )
        result = await get_coordinates(
            fake_ctx,
            session_id="coord_sc",
            element_type="subcatchment",
            element_id=reference_model.FIRST_SUBCATCH_ID,
        )

        assert isinstance(result, SpatialResult)
        assert result.element_type == "subcatchment"
        assert result.x == pytest.approx(10.0)
        assert result.y == pytest.approx(20.0)

    async def test_get_coordinates_requires_element_id(self, fake_ctx, inp_path):
        """Raises ToolError when element_id is empty."""
        await _open(fake_ctx, inp_path, "coord_noid")

        with pytest.raises(ToolError, match="element_id is required"):
            await get_coordinates(
                fake_ctx,
                session_id="coord_noid",
                element_type="node",
                element_id="",
            )

    async def test_get_coordinates_invalid_type(self, fake_ctx, inp_path):
        """Raises ToolError for unknown element_type."""
        await _open(fake_ctx, inp_path, "coord_badtype")

        with pytest.raises(ToolError, match="Unknown element type"):
            await get_coordinates(
                fake_ctx,
                session_id="coord_badtype",
                element_type="pump_station",
                element_id="J1",
            )


# ---------------------------------------------------------------------------
# set_coordinates
# ---------------------------------------------------------------------------


class TestSetCoordinates:
    async def test_set_coordinates_returns_status(self, fake_ctx, inp_path, reference_model):
        """set_coordinates returns a dict with status 'updated'."""
        await _open(fake_ctx, inp_path, "set_coord")
        result = await set_coordinates(
            fake_ctx,
            session_id="set_coord",
            element_type="node",
            element_id=reference_model.FIRST_NODE_ID,
            x=100.0,
            y=200.0,
        )

        assert result["status"] == "updated"
        assert result["element_type"] == "node"
        assert result["element_id"] == reference_model.FIRST_NODE_ID
        assert result["x"] == 100.0
        assert result["y"] == 200.0

    async def test_set_coordinates_requires_element_id(self, fake_ctx, inp_path):
        """set_coordinates raises ToolError when element_id is empty."""
        await _open(fake_ctx, inp_path, "set_coord_noid")

        with pytest.raises(ToolError, match="element_id is required"):
            await set_coordinates(
                fake_ctx,
                session_id="set_coord_noid",
                element_type="node",
                element_id="",
                x=0.0,
                y=0.0,
            )


# ---------------------------------------------------------------------------
# set_gage_coord
# ---------------------------------------------------------------------------


class TestSetGageCoord:
    async def test_set_gage_coord_roundtrips(self, fake_ctx, inp_path, reference_model):
        """set_gage_coord updates the gage's coordinates, visible via the bulk read."""
        await _open(fake_ctx, inp_path, "gage_coord")
        result = await set_gage_coord(
            fake_ctx,
            session_id="gage_coord",
            gage_id=reference_model.GAGE_ID,
            x=123.0,
            y=456.0,
        )
        assert result["status"] == "updated"
        assert result["gage_id"] == reference_model.GAGE_ID
        assert result["x"] == 123.0
        assert result["y"] == 456.0

        allc = await get_all_coordinates(
            fake_ctx, session_id="gage_coord", element_type="gage"
        )
        rec = next(r for r in allc["coordinates"] if r["id"] == reference_model.GAGE_ID)
        assert rec["x"] == pytest.approx(123.0)
        assert rec["y"] == pytest.approx(456.0)

    async def test_set_gage_coord_requires_id(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path, "gage_coord_noid")
        with pytest.raises(ToolError, match="gage_id is required"):
            await set_gage_coord(fake_ctx, session_id="gage_coord_noid", gage_id="")

    async def test_set_gage_coord_unknown(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path, "gage_coord_bad")
        with pytest.raises(ToolError, match="not found"):
            await set_gage_coord(
                fake_ctx, session_id="gage_coord_bad", gage_id="NOPE", x=1.0, y=2.0
            )


# ---------------------------------------------------------------------------
# set_node_coords_bulk
# ---------------------------------------------------------------------------


class TestSetNodeCoordsBulk:
    async def test_bulk_roundtrips(self, fake_ctx, inp_path, reference_model):
        """set_node_coords_bulk replaces every node coordinate at once."""
        await _open(fake_ctx, inp_path, "bulk_coord")
        n = reference_model.NODE_COUNT
        coords = [[float(i), float(i) + 0.5] for i in range(n)]
        result = await set_node_coords_bulk(
            fake_ctx, session_id="bulk_coord", coordinates=coords
        )
        assert result["status"] == "updated"
        assert result["node_count"] == n

        allc = await get_all_coordinates(
            fake_ctx, session_id="bulk_coord", element_type="node"
        )
        assert allc["count"] == n
        # get_all_coordinates returns records in node-index order.
        assert allc["coordinates"][0]["x"] == pytest.approx(0.0)
        assert allc["coordinates"][0]["y"] == pytest.approx(0.5)
        assert allc["coordinates"][n - 1]["x"] == pytest.approx(float(n - 1))

    async def test_wrong_count_rejected(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path, "bulk_coord_bad")
        with pytest.raises(ToolError, match="exactly one"):
            await set_node_coords_bulk(
                fake_ctx, session_id="bulk_coord_bad", coordinates=[[1.0, 2.0]]
            )

    async def test_malformed_pair_rejected(self, fake_ctx, inp_path):
        await _open(fake_ctx, inp_path, "bulk_coord_malformed")
        with pytest.raises(ToolError, match="two-element"):
            await set_node_coords_bulk(
                fake_ctx,
                session_id="bulk_coord_malformed",
                coordinates=[[1.0, 2.0, 3.0]],
            )


# ---------------------------------------------------------------------------
# get_quality
# ---------------------------------------------------------------------------


class TestGetQuality:
    async def test_get_quality_returns_dict(self, fake_ctx, inp_path, reference_model):
        """get_quality returns a dict of pollutant concentrations after stepping."""
        await _open_and_step(fake_ctx, inp_path, "qual_step")
        result = await get_quality(
            fake_ctx,
            session_id="qual_step",
            element_type="node",
            element_id=reference_model.FIRST_NODE_ID,
        )

        assert result["element_type"] == "node"
        assert result["element_id"] == reference_model.FIRST_NODE_ID
        assert isinstance(result["quality"], dict)

    async def test_get_quality_known_pollutant(self, fake_ctx, inp_path, reference_model):
        """get_quality returns only the named pollutant when specified."""
        if reference_model.POLLUTANT_COUNT == 0:
            pytest.skip("Model has no pollutants.")

        await _open_and_step(fake_ctx, inp_path, "qual_pollut")
        result = await get_quality(
            fake_ctx,
            session_id="qual_pollut",
            element_type="node",
            element_id=reference_model.FIRST_NODE_ID,
            pollutant=reference_model.POLLUTANT_ID,
        )

        assert reference_model.POLLUTANT_ID in result["quality"]
        assert len(result["quality"]) == 1

    async def test_get_quality_unknown_pollutant(self, fake_ctx, inp_path, reference_model):
        """get_quality raises ToolError for an unknown pollutant name."""
        if reference_model.POLLUTANT_COUNT == 0:
            pytest.skip("Model has no pollutants.")

        await _open_and_step(fake_ctx, inp_path, "qual_unknown")

        with pytest.raises(ToolError, match="not found"):
            await get_quality(
                fake_ctx,
                session_id="qual_unknown",
                element_type="node",
                element_id=reference_model.FIRST_NODE_ID,
                pollutant="NONEXISTENT_POLLUTANT_XYZ",
            )

    async def test_get_quality_requires_element_id(self, fake_ctx, inp_path):
        """get_quality raises ToolError when element_id is empty."""
        await _open(fake_ctx, inp_path, "qual_noid")

        with pytest.raises(ToolError, match="element_id is required"):
            await get_quality(
                fake_ctx,
                session_id="qual_noid",
                element_type="node",
                element_id="",
            )

    async def test_get_quality_no_pollutants(self, fake_ctx, inp_path, reference_model):
        """get_quality returns empty dict when model has no pollutants."""
        if reference_model.POLLUTANT_COUNT > 0:
            pytest.skip("Model has pollutants — skipping no-pollutant test.")

        await _open_and_step(fake_ctx, inp_path, "qual_empty")
        result = await get_quality(
            fake_ctx,
            session_id="qual_empty",
            element_type="node",
            element_id=reference_model.FIRST_NODE_ID,
        )

        assert result["quality"] == {}


# ---------------------------------------------------------------------------
# set_treatment
# ---------------------------------------------------------------------------


class TestSetTreatment:
    async def test_set_treatment(self, fake_ctx, inp_path, reference_model):
        """set_treatment calls the quality engine and returns a status dict."""
        if reference_model.POLLUTANT_COUNT == 0:
            pytest.skip("Model has no pollutants.")

        await _open(fake_ctx, inp_path, "treat_set")
        result = await set_treatment(
            fake_ctx,
            session_id="treat_set",
            node_id=reference_model.FIRST_NODE_ID,
            pollutant=reference_model.POLLUTANT_ID,
            expression="R = 0.5 * C",
        )

        assert result["status"] == "treatment_set"
        assert result["node_id"] == reference_model.FIRST_NODE_ID
        assert result["pollutant"] == reference_model.POLLUTANT_ID
        assert result["expression"] == "R = 0.5 * C"

    async def test_set_treatment_requires_node_id(self, fake_ctx, inp_path):
        """set_treatment raises ToolError when node_id is empty."""
        await _open(fake_ctx, inp_path, "treat_noid")

        with pytest.raises(ToolError, match="node_id is required"):
            await set_treatment(
                fake_ctx,
                node_id="",
                pollutant="TSS",
                expression="R = 0.5 * C",
            )

    async def test_set_treatment_requires_pollutant(self, fake_ctx, inp_path):
        """set_treatment raises ToolError when pollutant is empty."""
        await _open(fake_ctx, inp_path, "treat_nopoll")

        with pytest.raises(ToolError, match="pollutant is required"):
            await set_treatment(
                fake_ctx,
                node_id="J1",
                pollutant="",
                expression="R = 0.5 * C",
            )

    async def test_set_treatment_requires_expression(self, fake_ctx, inp_path):
        """set_treatment raises ToolError when expression is empty."""
        await _open(fake_ctx, inp_path, "treat_noexpr")

        with pytest.raises(ToolError, match="expression is required"):
            await set_treatment(
                fake_ctx,
                node_id="J1",
                pollutant="TSS",
                expression="",
            )


# ---------------------------------------------------------------------------
# add_lid (input validation only — real LID control requires model with LIDs)
# ---------------------------------------------------------------------------


class TestAddLid:
    async def test_add_lid_requires_subcatch_id(self, fake_ctx, inp_path):
        """add_lid raises ToolError when subcatch_id is empty."""
        await _open(fake_ctx, inp_path, "lid_noid")

        with pytest.raises(ToolError, match="subcatch_id is required"):
            await add_lid(
                fake_ctx,
                session_id="lid_noid",
                subcatch_id="",
                lid_idx=0,
                area=100.0,
            )

    async def test_add_lid_requires_positive_area(self, fake_ctx, inp_path):
        """add_lid raises ToolError when area is not positive."""
        await _open(fake_ctx, inp_path, "lid_area")

        with pytest.raises(ToolError, match="area must be a positive"):
            await add_lid(
                fake_ctx,
                session_id="lid_area",
                subcatch_id="S1",
                lid_idx=0,
                area=0.0,
            )

        with pytest.raises(ToolError, match="area must be a positive"):
            await add_lid(
                fake_ctx,
                session_id="lid_area",
                subcatch_id="S1",
                lid_idx=0,
                area=-10.0,
            )
