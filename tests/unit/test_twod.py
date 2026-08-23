"""twod.* — 2D overland-flow surface tools.

Runs against the real ``openswmm.engine`` over the bundled
``twod_parking_lot.inp`` fixture (a 20 m x 20 m lot meshed as 8 triangles
on a 3x3 vertex grid, with vertex- and triangle-node couplings, one row of
every boundary-condition type, and a leaky-berm [2D_EDGE_CONVEYANCE] row).
Skips cleanly when the installed engine build predates the
``Solver.surface2d`` view.

Per ``openswmm.engine/docs/API_GAP_CLOSURE_PLAN_2026-06-10.md`` Phase C.1.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

eng = pytest.importorskip("openswmm.engine")
if not hasattr(eng.Solver, "surface2d"):
    pytest.skip(
        "openswmm.engine build predates the Solver.surface2d view (rebuild the engine wheel)",
        allow_module_level=True,
    )

from openswmm_mcp.errors import ToolError  # noqa: E402
from openswmm_mcp.tools.twod import (  # noqa: E402
    add_triangle_coupling,
    clear_triangle_couplings,
    force_clear,
    force_coupling_flux,
    force_evap,
    force_rainfall,
    get_coupling_map,
    get_edge_bc,
    get_edge_conveyance,
    get_edge_geometry_bulk,
    get_mass_balance,
    get_mesh_geometry,
    get_mesh_summary,
    get_solver_params,
    get_state,
    get_state_bulk,
    get_stats,
    get_totals,
    get_triangle_initial_conditions,
    get_vertex_coupling_params,
    get_vertex_head,
    reset_edge_conveyance,
    set_edge_bc,
    set_edge_conveyance,
    set_solver_params,
    set_triangle_initial_conditions,
    set_vertex_coupling_params,
    set_vertex_z,
)

_TWOD_INP = (Path(__file__).parent / "data" / "twod_parking_lot.inp").resolve()

# Fixture mesh constants (see the [2D_*] sections of twod_parking_lot.inp).
N_VERTICES = 9
N_TRIANGLES = 8
# [2D_VERTEX_NODE_MAP]: v4 -> J1, CD 0.65, AREA 2.0.
COUPLED_VERTEX = 4
# [2D_TRIANGLE_NODE_MAP]: tag "vault" (= T6) -> ST1, CD 0.60, AREA 10.0.
COUPLED_TRIANGLE = 6


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):
        pass


@pytest.fixture
def twod_inp_path(tmp_path: Path) -> str:
    dest = tmp_path / "twod_parking_lot.inp"
    shutil.copy(_TWOD_INP, dest)
    return str(dest)


async def _open(session_manager, twod_inp_path, session_id="twod"):
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=twod_inp_path, session_id=session_id)
    return ctx


async def _open_and_step(session_manager, twod_inp_path, session_id="twod", steps=5):
    from openswmm_mcp.tools.lifecycle import step_simulation

    ctx = await _open(session_manager, twod_inp_path, session_id=session_id)
    await step_simulation(ctx, session_id=session_id, num_steps=steps)
    return ctx


# ---------------------------------------------------------------------------
# Mesh
# ---------------------------------------------------------------------------


class TestMesh:
    async def test_mesh_summary(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_mesh_summary(ctx, session_id="twod")
        assert out["active"] is True
        assert out["n_vertices"] == N_VERTICES
        assert out["n_triangles"] == N_TRIANGLES
        assert out["boundary_edge_count"] > 0
        # The fixture couples the centre vertex to J1 and the NE triangle to ST1.
        assert out["vertex_coupling_count"] >= 1
        assert out["triangle_coupling_count"] >= 1

    async def test_inactive_model_reports_inactive(self, session_manager, inp_path):
        # The 1D-only reference model has no [2D_*] sections.
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=inp_path, session_id="oned")
        out = await get_mesh_summary(ctx, session_id="oned")
        assert out["active"] is False

    async def test_inactive_model_state_tool_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(session_manager)
        await open_model(ctx, inp_path=inp_path, session_id="oned2")
        with pytest.raises(ToolError):
            await get_state(ctx, session_id="oned2", triangle=0)

    async def test_mesh_geometry_window(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_mesh_geometry(ctx, session_id="twod", offset=0, limit=3)
        assert out["n_triangles"] == N_TRIANGLES
        assert len(out["triangles"]) == 3
        tri = out["triangles"][0]
        assert len(tri["vertices"]) == 3
        assert tri["area"] > 0.0
        assert len(tri["centroid"]) == 3
        assert tri["mannings_n"] > 0.0
        assert len(tri["neighbours"]) == 3
        # Vertex elevations span the fixture's 100 -> 101 m slope.
        assert out["vertex_z"]["min"] == pytest.approx(100.0)
        assert out["vertex_z"]["max"] == pytest.approx(101.0)

    async def test_set_vertex_z(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await set_vertex_z(ctx, session_id="twod", vertex=0, z=102.5)
        assert out["status"] == "ok"
        geo = await get_mesh_geometry(ctx, session_id="twod", offset=0, limit=1)
        assert geo["vertex_z"]["max"] == pytest.approx(102.5)

    async def test_coupling_map(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_coupling_map(ctx, session_id="twod")
        assert len(out["vertex_couplings"]) >= 1
        assert len(out["triangle_couplings"]) >= 1
        assert all(c["node_index"] >= 0 for c in out["vertex_couplings"])
        # The fixture's single [2D_TRIANGLE_NODE_MAP] row (tag "vault" -> T6).
        rows = out["triangle_coupling_rows"]
        assert len(rows) == 1
        assert rows[0]["row"] == 0
        assert rows[0]["triangle"] == COUPLED_TRIANGLE
        assert rows[0]["node_index"] >= 0
        assert rows[0]["cd"] == pytest.approx(0.60)
        assert rows[0]["area"] == pytest.approx(10.0)

    async def test_edge_geometry_bulk(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_edge_geometry_bulk(ctx, session_id="twod", offset=0, limit=4)
        for key in ("length", "nx", "ny"):
            assert out[key]["count"] == N_TRIANGLES * 3, key
        assert out["length"]["min"] > 0.0
        assert len(out["edges"]) == 4
        edge = out["edges"][0]
        assert edge["triangle"] == 0
        assert edge["edge"] == 0
        assert edge["length"] > 0.0
        # Outward unit normal: nx^2 + ny^2 == 1.
        assert edge["nx"] ** 2 + edge["ny"] ** 2 == pytest.approx(1.0, abs=1e-6)

    async def test_edge_geometry_bulk_summary_only(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_edge_geometry_bulk(ctx, session_id="twod")
        assert out["edges"] == []
        assert out["length"]["count"] == N_TRIANGLES * 3


# ---------------------------------------------------------------------------
# State, totals, stats, mass balance (after stepping)
# ---------------------------------------------------------------------------


class TestStateAndStats:
    async def test_get_state(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await get_state(ctx, session_id="twod", triangle=0)
        for key in ("depth", "head", "rainfall", "net_source", "coupling_flux"):
            assert isinstance(out[key], float)
        assert out["depth"] >= 0.0

    async def test_get_vertex_head(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await get_vertex_head(ctx, session_id="twod", vertex=0)
        assert out["vertex"] == 0
        assert isinstance(out["head"], float)

    async def test_get_state_bulk_each_variable(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        for variable, expected_n in (
            ("depth", N_TRIANGLES),
            ("head", N_TRIANGLES),
            ("vertex_head", N_VERTICES),
            ("coupling_flux", N_TRIANGLES),
            ("edge_flux", N_TRIANGLES * 3),
        ):
            out = await get_state_bulk(ctx, session_id="twod", variable=variable, limit=4)
            assert out["summary"]["count"] == expected_n, variable
            assert len(out["values"]) == 4

    async def test_get_state_bulk_vertex_render_depth(self, session_manager, twod_inp_path):
        """vertex_render_depth is per-vertex, signed (eta_v - z_v) for rendering."""
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await get_state_bulk(ctx, session_id="twod", variable="vertex_render_depth", limit=4)
        assert out["summary"]["count"] == N_VERTICES
        assert len(out["values"]) == 4

    async def test_get_state_bulk_invalid_variable(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await get_state_bulk(ctx, session_id="twod", variable="vorticity")

    async def test_get_totals(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await get_totals(ctx, session_id="twod")
        assert out["max_depth"] >= 0.0
        assert out["total_volume"] >= 0.0
        assert out["solver_steps"] >= 0

    async def test_get_stats(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await get_stats(ctx, session_id="twod", top_n=3)
        for key in ("max_depth", "max_velocity", "max_continuity_err"):
            assert out[key]["summary"]["count"] == N_TRIANGLES
            assert len(out[key]["top"]) == 3
            top = out[key]["top"]
            assert top[0]["value"] >= top[-1]["value"]

    async def test_get_mass_balance(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await get_mass_balance(ctx, session_id="twod")
        for key in (
            "init_storage",
            "final_storage",
            "rainfall_in",
            "coupling_1d_to_2d_in",
            "coupling_2d_to_1d_out",
            "outfall_in",
            "outfall_out",
            "evap_out",
            "boundary_in",
            "boundary_out",
            "continuity_error",
        ):
            assert isinstance(out[key], float), key


# ---------------------------------------------------------------------------
# Forcing
# ---------------------------------------------------------------------------


class TestForcing:
    async def test_uniform_rainfall_forcing(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await force_rainfall(
            ctx, session_id="twod", value=1.0e-5, mode="replace", persist=True
        )
        assert out["status"] == "ok"
        assert out["scope"] == "uniform"
        cleared = await force_clear(ctx, session_id="twod")
        assert cleared["status"] == "cleared"

    async def test_single_triangle_rainfall(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await force_rainfall(ctx, session_id="twod", value=2.0e-5, triangle=1, mode="add")
        assert out["scope"] == "triangle 1"

    async def test_coupling_flux_forcing(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await force_coupling_flux(
            ctx, session_id="twod", triangle=0, value=1.0e-4, persist=True
        )
        assert out["status"] == "ok"
        await force_clear(ctx, session_id="twod")

    async def test_uniform_evap_forcing(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await force_evap(ctx, session_id="twod", value=1.0e-6, mode="replace", persist=True)
        assert out["status"] == "ok"
        assert out["scope"] == "uniform"
        cleared = await force_clear(ctx, session_id="twod")
        assert cleared["status"] == "cleared"

    async def test_single_triangle_evap(self, session_manager, twod_inp_path):
        ctx = await _open_and_step(session_manager, twod_inp_path)
        out = await force_evap(ctx, session_id="twod", value=2.0e-6, triangle=1, mode="add")
        assert out["scope"] == "triangle 1"

    async def test_evap_invalid_mode_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await force_evap(ctx, session_id="twod", value=1.0e-6, mode="multiply")

    async def test_invalid_mode_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await force_rainfall(ctx, session_id="twod", value=1.0e-5, mode="multiply")


# ---------------------------------------------------------------------------
# Solver params
# ---------------------------------------------------------------------------


class TestSolverParams:
    async def test_roundtrip(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        before = await get_solver_params(ctx, session_id="twod")
        assert before["dry_depth"] > 0.0
        # Retired CVODE tolerances are gone from the tool surface.
        assert "rel_tolerance" not in before
        assert "abs_tolerance" not in before
        out = await set_solver_params(ctx, session_id="twod", dry_depth=before["dry_depth"] * 2.0)
        assert out["dry_depth"] == pytest.approx(before["dry_depth"] * 2.0)

    async def test_no_params_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await set_solver_params(ctx, session_id="twod")


# ---------------------------------------------------------------------------
# Edge BCs & conveyance
# ---------------------------------------------------------------------------


class TestEdgeBc:
    async def test_get_edge_bc_shape(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_edge_bc(ctx, session_id="twod", triangle=0, edge=0)
        assert out["bc_type"] in (
            "WALL",
            "NORMAL_FLOW",
            "SPECIFIED_STAGE",
            "SPECIFIED_FLOW",
            "RATING_CURVE",
        )
        for key in ("head", "slope", "flow", "cum_flux"):
            assert isinstance(out[key], float)

    async def test_set_edge_bc_roundtrip(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await set_edge_bc(
            ctx,
            session_id="twod",
            triangle=0,
            edge=0,
            bc_type="SPECIFIED_STAGE",
            head=100.75,
        )
        assert out["status"] == "ok"
        got = await get_edge_bc(ctx, session_id="twod", triangle=0, edge=0)
        assert got["bc_type"] == "SPECIFIED_STAGE"
        assert got["head"] == pytest.approx(100.75)

    async def test_invalid_edge_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await get_edge_bc(ctx, session_id="twod", triangle=0, edge=3)

    async def test_no_bc_params_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await set_edge_bc(ctx, session_id="twod", triangle=0, edge=0)


class TestEdgeConveyance:
    async def test_fixture_has_restricted_edges(self, session_manager, twod_inp_path):
        # The fixture's [2D_EDGE_CONVEYANCE] berm sets c = 0.30 on a row of edges.
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_edge_conveyance(ctx, session_id="twod")
        assert out["summary"]["count"] == N_TRIANGLES * 3
        assert out["summary"]["min"] == pytest.approx(0.30)
        assert len(out["restricted_edges"]) >= 1

    async def test_set_mirrors_to_neighbour(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await set_edge_conveyance(ctx, session_id="twod", triangle=0, edge=0, conveyance=0.5)
        assert out["status"] == "ok"
        got = await get_edge_conveyance(ctx, session_id="twod", triangle=0, edge=0)
        assert got["conveyance"] == pytest.approx(0.5)

    async def test_out_of_range_value_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await set_edge_conveyance(ctx, session_id="twod", triangle=0, edge=0, conveyance=1.5)

    async def test_reset(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await reset_edge_conveyance(ctx, session_id="twod")
        assert out["status"] == "reset"
        got = await get_edge_conveyance(ctx, session_id="twod")
        assert got["summary"]["min"] == pytest.approx(1.0)
        assert got["restricted_edges"] == []


# ---------------------------------------------------------------------------
# Triangle initial conditions
# ---------------------------------------------------------------------------


class TestTriangleInitialConditions:
    async def test_defaults_are_dry_and_still(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_triangle_initial_conditions(ctx, session_id="twod", triangle=0)
        assert out["init_depth"] == pytest.approx(0.0)
        assert out["init_u"] == pytest.approx(0.0)
        assert out["init_v"] == pytest.approx(0.0)

    async def test_set_depth_roundtrip(self, session_manager, twod_inp_path):
        # The fixture is a CMS project with ";; UNITS: SI (m)", so INIT_DEPTH
        # is metres here; on a US-FLOW_UNITS project it would be feet.
        ctx = await _open(session_manager, twod_inp_path)
        out = await set_triangle_initial_conditions(ctx, session_id="twod", triangle=0, depth=0.25)
        assert out["status"] == "ok"
        got = await get_triangle_initial_conditions(ctx, session_id="twod", triangle=0)
        assert got["init_depth"] == pytest.approx(0.25)

    async def test_set_velocity_roundtrip(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await set_triangle_initial_conditions(
            ctx, session_id="twod", triangle=1, u=0.4, v=-0.2
        )
        assert out["status"] == "ok"
        got = await get_triangle_initial_conditions(ctx, session_id="twod", triangle=1)
        assert got["init_u"] == pytest.approx(0.4)
        assert got["init_v"] == pytest.approx(-0.2)

    async def test_no_params_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await set_triangle_initial_conditions(ctx, session_id="twod", triangle=0)

    async def test_half_a_velocity_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await set_triangle_initial_conditions(ctx, session_id="twod", triangle=0, u=0.4)

    async def test_negative_depth_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await set_triangle_initial_conditions(ctx, session_id="twod", triangle=0, depth=-1.0)


# ---------------------------------------------------------------------------
# Coupling authoring
# ---------------------------------------------------------------------------


class TestTriangleCoupling:
    async def test_add_appends_rows(self, session_manager, twod_inp_path):
        # add() appends: a triangle may carry several rows, one per node.
        ctx = await _open(session_manager, twod_inp_path)
        first = await add_triangle_coupling(
            ctx, session_id="twod", triangle=0, node_name="J1", cd=0.7, area=3.0
        )
        assert first["status"] == "ok"
        second = await add_triangle_coupling(
            ctx, session_id="twod", triangle=0, node_name="ST1", cd=0.5, area=1.5
        )
        # 1 authored fixture row + 2 added.
        assert second["coupling_rows"] == first["coupling_rows"] + 1 == 3
        rows = (await get_coupling_map(ctx, session_id="twod"))["triangle_coupling_rows"]
        assert [r["triangle"] for r in rows] == [COUPLED_TRIANGLE, 0, 0]
        assert rows[2]["cd"] == pytest.approx(0.5)
        assert rows[2]["area"] == pytest.approx(1.5)

    async def test_clear_removes_every_row(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        await add_triangle_coupling(ctx, session_id="twod", triangle=0, node_name="J1")
        out = await clear_triangle_couplings(ctx, session_id="twod")
        assert out["status"] == "cleared"
        assert out["rows_removed"] == 2
        got = await get_coupling_map(ctx, session_id="twod")
        assert got["triangle_coupling_rows"] == []
        # The legacy per-triangle mirror is reset too; vertex couplings survive.
        assert got["triangle_couplings"] == []
        assert len(got["vertex_couplings"]) >= 1

    async def test_missing_node_name_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await add_triangle_coupling(ctx, session_id="twod", triangle=0)

    async def test_nonpositive_cd_or_area_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await add_triangle_coupling(ctx, session_id="twod", triangle=0, node_name="J1", cd=0.0)
        with pytest.raises(ToolError):
            await add_triangle_coupling(
                ctx, session_id="twod", triangle=0, node_name="J1", area=-1.0
            )


class TestVertexCouplingParams:
    async def test_authored_params_read_back(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await get_vertex_coupling_params(ctx, session_id="twod", vertex=COUPLED_VERTEX)
        assert out["cd"] == pytest.approx(0.65)
        assert out["area"] == pytest.approx(2.0)

    async def test_set_roundtrip(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await set_vertex_coupling_params(
            ctx, session_id="twod", vertex=COUPLED_VERTEX, cd=0.8, area=4.5
        )
        assert out["status"] == "ok"
        assert out["cd"] == pytest.approx(0.8)
        assert out["area"] == pytest.approx(4.5)
        got = await get_vertex_coupling_params(ctx, session_id="twod", vertex=COUPLED_VERTEX)
        assert got["cd"] == pytest.approx(0.8)
        assert got["area"] == pytest.approx(4.5)

    async def test_partial_update_leaves_the_other_alone(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        out = await set_vertex_coupling_params(
            ctx, session_id="twod", vertex=COUPLED_VERTEX, cd=0.9
        )
        assert out["cd"] == pytest.approx(0.9)
        assert out["area"] == pytest.approx(2.0)

    async def test_no_params_raises(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await set_vertex_coupling_params(ctx, session_id="twod", vertex=COUPLED_VERTEX)

    async def test_nonpositive_values_raise(self, session_manager, twod_inp_path):
        ctx = await _open(session_manager, twod_inp_path)
        with pytest.raises(ToolError):
            await set_vertex_coupling_params(ctx, session_id="twod", vertex=COUPLED_VERTEX, cd=0.0)
        with pytest.raises(ToolError):
            await set_vertex_coupling_params(
                ctx, session_id="twod", vertex=COUPLED_VERTEX, area=-2.0
            )
