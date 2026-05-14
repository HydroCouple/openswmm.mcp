"""Tests for the GeoPackage MCP tools.

These tests verify the MCP tool functions work correctly with
the GeoPackage Cython bindings. Requires OPENSWMM_WITH_GEOPACKAGE=ON.
"""

from __future__ import annotations

import pytest

# Check if GeoPackage is available
gpkg_available = False
try:
    from openswmm.engine import HAS_GEOPACKAGE
    gpkg_available = HAS_GEOPACKAGE
except (ImportError, AttributeError):
    pass

pytestmark = pytest.mark.skipif(
    not gpkg_available,
    reason="GeoPackage bindings not available"
)


class TestGeoPackageToolsImport:
    """Verify geopackage tools module imports cleanly."""

    def test_import_geopackage_mcp(self):
        from openswmm_mcp.tools.geopackage import geopackage_mcp
        assert geopackage_mcp is not None

    def test_tool_functions_exist(self):
        from openswmm_mcp.tools import geopackage
        assert hasattr(geopackage, "open_geopackage")
        assert hasattr(geopackage, "list_simulations")
        assert hasattr(geopackage, "get_result_timeseries")
        assert hasattr(geopackage, "get_result_summary")
        assert hasattr(geopackage, "import_observed_data")
        assert hasattr(geopackage, "compare_sim_vs_observed")
        assert hasattr(geopackage, "close_geopackage")


class TestGeoPackageServerMount:
    """Verify geopackage sub-server is mounted."""

    def test_server_imports(self):
        from openswmm_mcp.server import mcp
        assert mcp is not None


class TestModelsExtended:
    """Verify Pydantic model extensions for new fields."""

    def test_system_summary_new_fields(self):
        from openswmm_mcp.models import SystemSummary
        s = SystemSummary(
            session_id="test",
            state="running",
            node_count=10,
            link_count=15,
            subcatchment_count=5,
            gage_count=2,
            pollutant_count=1,
            flow_units="CFS",
            route_model="DYNWAVE",
            start_time=0.0,
            end_time=86400.0,
            routing_step=30.0,
            surcharge_method="DYNAMIC_SLOT",
            dps_celerity=25.0,
            dps_alpha=3.0,
            dps_decay_time=0.5,
            event_count=2,
            steady_state_skip=False,
        )
        assert s.surcharge_method == "DYNAMIC_SLOT"
        assert s.dps_celerity == 25.0
        assert s.event_count == 2

    def test_node_info_new_fields(self):
        from openswmm_mcp.models import NodeInfo
        n = NodeInfo(
            node_id="J1",
            index=0,
            node_type="JUNCTION",
            outfall_route_to=3,
        )
        assert n.outfall_route_to == 3

    def test_link_info_new_fields(self):
        from openswmm_mcp.models import LinkInfo
        l = LinkInfo(
            link_id="C1",
            index=0,
            link_type="CONDUIT",
            from_node="J1",
            to_node="J2",
            hydraulic_power=3120.0,
            pump_cycles=5,
            pump_on_time=1800.0,
            pump_volume=9000.0,
        )
        assert l.hydraulic_power == 3120.0
        assert l.pump_cycles == 5

    def test_mass_balance_new_fields(self):
        from openswmm_mcp.models import MassBalanceResult
        mb = MassBalanceResult(
            session_id="test",
            runoff_continuity_error=0.001,
            routing_continuity_error=0.002,
            quality_continuity_error=0.003,
            quality_continuity_errors={"TSS": 0.01, "Lead": 0.02},
            runoff_total={"rainfall": 1000.0},
            routing_total={"outflow": 800.0},
            routing_stats={
                "avg_step": 15.0,
                "n_steps": 100,
                "max_courant": 0.8,
            },
            max_courant=0.8,
        )
        assert mb.quality_continuity_errors["TSS"] == 0.01
        assert mb.routing_stats["max_courant"] == 0.8

    def test_backward_compatible_defaults(self):
        """New fields should default to None (backward compatible)."""
        from openswmm_mcp.models import SystemSummary, NodeInfo, LinkInfo
        s = SystemSummary(
            session_id="t", state="created", node_count=0, link_count=0,
            subcatchment_count=0, gage_count=0, pollutant_count=0,
            flow_units="CFS", route_model="DYNWAVE",
            start_time=0, end_time=0, routing_step=0,
        )
        assert s.surcharge_method is None
        assert s.dps_celerity is None
        assert s.event_count is None
        assert s.steady_state_skip is None

        n = NodeInfo(node_id="J1", index=0, node_type="JUNCTION")
        assert n.outfall_route_to is None

        l = LinkInfo(link_id="C1", index=0, link_type="CONDUIT",
                     from_node="J1", to_node="J2")
        assert l.hydraulic_power is None
        assert l.pump_cycles is None
