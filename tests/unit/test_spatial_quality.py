"""Tests for openswmm_mcp.tools.spatial_quality -- coordinates, quality, treatment, LID."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from openswmm_mcp.errors import ToolError
from openswmm_mcp.models import SpatialResult
from openswmm_mcp.tools.spatial_quality import (
    add_lid,
    get_coordinates,
    get_quality,
    set_coordinates,
    set_treatment,
)

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}


def _make_session(state: str = "running"):
    """Return a MagicMock that quacks like a SimSession."""
    session = MagicMock()
    session.state = state

    # spatial facade
    session.spatial.get_node_coord = MagicMock(return_value=(100.0, 200.0))
    session.spatial.set_node_coord = MagicMock()
    session.spatial.get_link_coord = MagicMock(return_value=[(0.0, 0.0), (10.0, 10.0)])
    session.spatial.set_link_coord = MagicMock()
    session.spatial.get_subcatch_coord = MagicMock(return_value=(50.0, 75.0))
    session.spatial.set_subcatch_coord = MagicMock()

    # quality facade
    session.quality.get_node_quality = MagicMock(return_value={"TSS": 12.5, "BOD": 3.1})
    session.quality.get_link_quality = MagicMock(return_value={"TSS": 8.0})
    session.quality.get_subcatch_quality = MagicMock(return_value={})
    session.quality.set_treatment = MagicMock()

    # infrastructure facade
    session.infrastructure.add_lid = MagicMock()

    return session


def _make_session_manager(sessions: dict | None = None):
    sm = MagicMock()
    _sessions = sessions or {}

    async def _get_session(sid):
        if sid not in _sessions:
            raise ToolError(f"No session with id '{sid}'.")
        return _sessions[sid]

    sm.get_session = AsyncMock(side_effect=_get_session)
    return sm


# ---------------------------------------------------------------------------
# get_coordinates
# ---------------------------------------------------------------------------


class TestGetCoordinates:
    async def test_get_coordinates_node(self):
        """Returns SpatialResult with x, y for a node."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await get_coordinates(
            ctx, session_id="default", element_type="node", element_id="J1"
        )

        assert isinstance(result, SpatialResult)
        assert result.element_type == "node"
        assert result.element_id == "J1"
        assert result.x == 100.0
        assert result.y == 200.0
        session.spatial.get_node_coord.assert_called_once_with("J1")

    async def test_get_coordinates_link(self):
        """Returns SpatialResult with vertices for a link."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await get_coordinates(
            ctx, session_id="default", element_type="link", element_id="C1"
        )

        assert isinstance(result, SpatialResult)
        assert result.element_type == "link"
        assert result.vertices is not None
        assert len(result.vertices) == 2

    async def test_get_coordinates_requires_element_id(self):
        """Raises ToolError when element_id is empty."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="element_id is required"):
            await get_coordinates(ctx, element_type="node", element_id="")

    async def test_get_coordinates_invalid_type(self):
        """Raises ToolError for unknown element_type."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="Unknown element type"):
            await get_coordinates(ctx, element_type="pump_station", element_id="PS1")


# ---------------------------------------------------------------------------
# set_coordinates
# ---------------------------------------------------------------------------


class TestSetCoordinates:
    async def test_set_coordinates_node(self):
        """set_coordinates calls the spatial facade and returns a status dict."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await set_coordinates(
            ctx,
            session_id="default",
            element_type="node",
            element_id="J1",
            x=300.0,
            y=400.0,
        )

        assert result["status"] == "updated"
        assert result["x"] == 300.0
        assert result["y"] == 400.0
        session.spatial.set_node_coord.assert_called_once_with("J1", 300.0, 400.0)

    async def test_set_coordinates_subcatchment(self):
        """set_coordinates works for subcatchments too."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await set_coordinates(
            ctx,
            session_id="default",
            element_type="subcatchment",
            element_id="S1",
            x=10.0,
            y=20.0,
        )

        assert result["status"] == "updated"
        assert result["element_type"] == "subcatchment"
        session.spatial.set_subcatch_coord.assert_called_once_with("S1", 10.0, 20.0)


# ---------------------------------------------------------------------------
# get_quality
# ---------------------------------------------------------------------------


class TestGetQuality:
    async def test_get_quality_all_pollutants(self):
        """get_quality returns all pollutants when no specific one is requested."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await get_quality(ctx, session_id="default", element_type="node", element_id="J1")

        assert result["element_type"] == "node"
        assert result["element_id"] == "J1"
        assert "TSS" in result["quality"]
        assert "BOD" in result["quality"]

    async def test_get_quality_single_pollutant(self):
        """get_quality filters to a single pollutant when specified."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await get_quality(
            ctx,
            session_id="default",
            element_type="node",
            element_id="J1",
            pollutant="TSS",
        )

        assert result["quality"] == {"TSS": 12.5}

    async def test_get_quality_unknown_pollutant(self):
        """get_quality raises ToolError for an unknown pollutant name."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="Pollutant.*not found"):
            await get_quality(
                ctx,
                session_id="default",
                element_type="node",
                element_id="J1",
                pollutant="LEAD",
            )

    async def test_get_quality_empty_result(self):
        """get_quality returns an empty dict when no pollutants are modelled."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        # subcatchment quality returns empty dict
        result = await get_quality(
            ctx,
            session_id="default",
            element_type="subcatchment",
            element_id="S1",
        )

        assert result["quality"] == {}


# ---------------------------------------------------------------------------
# set_treatment
# ---------------------------------------------------------------------------


class TestSetTreatment:
    async def test_set_treatment(self):
        """set_treatment calls the quality facade and returns a status dict."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await set_treatment(
            ctx,
            session_id="default",
            node_id="J1",
            pollutant="TSS",
            expression="R = 0.5 * C",
        )

        assert result["status"] == "treatment_set"
        assert result["node_id"] == "J1"
        assert result["pollutant"] == "TSS"
        assert result["expression"] == "R = 0.5 * C"
        session.quality.set_treatment.assert_called_once_with("J1", "TSS", "R = 0.5 * C")

    async def test_set_treatment_requires_node_id(self):
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="node_id is required"):
            await set_treatment(ctx, node_id="", pollutant="TSS", expression="R = 0.5 * C")

    async def test_set_treatment_requires_pollutant(self):
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="pollutant is required"):
            await set_treatment(ctx, node_id="J1", pollutant="", expression="R = 0.5 * C")

    async def test_set_treatment_requires_expression(self):
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="expression is required"):
            await set_treatment(ctx, node_id="J1", pollutant="TSS", expression="")


# ---------------------------------------------------------------------------
# add_lid
# ---------------------------------------------------------------------------


class TestAddLid:
    async def test_add_lid(self):
        """add_lid calls infrastructure facade and returns a status dict."""
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        result = await add_lid(
            ctx,
            session_id="default",
            subcatch_id="S1",
            lid_type="BC",
            area=500.0,
        )

        assert result["status"] == "lid_added"
        assert result["subcatch_id"] == "S1"
        assert result["lid_type"] == "BC"
        assert result["area"] == 500.0
        session.infrastructure.add_lid.assert_called_once_with("S1", "BC", 500.0)

    async def test_add_lid_requires_subcatch_id(self):
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="subcatch_id is required"):
            await add_lid(ctx, subcatch_id="", lid_type="BC", area=100.0)

    async def test_add_lid_requires_lid_type(self):
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="lid_type is required"):
            await add_lid(ctx, subcatch_id="S1", lid_type="", area=100.0)

    async def test_add_lid_requires_positive_area(self):
        session = _make_session()
        sm = _make_session_manager({"default": session})
        ctx = MockContext(sm)

        with pytest.raises(ToolError, match="area must be a positive"):
            await add_lid(ctx, subcatch_id="S1", lid_type="BC", area=0.0)

        with pytest.raises(ToolError, match="area must be a positive"):
            await add_lid(ctx, subcatch_id="S1", lid_type="BC", area=-10.0)
