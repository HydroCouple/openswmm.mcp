"""Engine surfaces added since the 2026-10-03 sync, through the generic tools.

* ``describe`` resolves shared-view names, reports build capabilities and a
  service's current values (every option, through native enumeration).
* ``timeseries`` reads 2D cells and edges, and a running session's 1D output.
* ``call`` reaches the new standalone targets: flow tracing over the session's
  network and the 2D results reader.
* ``report`` reads the 2D services of a meshed model under their current
  catalog names.

Real engine, no mocks; output under ``tests/_output/`` (CLAUDE.md §4.1).
"""

from __future__ import annotations

import shutil

DATA_2D = "twod_example.inp"


async def _open_2d(tools, output_dir, session_id="two"):
    from tests.unit.conftest import DATA

    inp = output_dir / DATA_2D
    shutil.copy(DATA / DATA_2D, inp)
    await tools("open_model", path=str(inp), session_id=session_id)
    return session_id


async def test_describe_capabilities_and_canonical_names(tools, inp_path):
    caps = await tools("describe", topic="capabilities")
    assert caps["catalog_revision"] >= 2
    assert {"2d", "geopackage"} <= set(caps["features"])
    alias = await tools("describe", topic="surface2d.groundwater")
    assert alias["target"] == "groundwater2d"


async def test_describe_options_lists_every_option(tools, inp_path):
    await tools("open_model", path=inp_path, session_id="m")
    options = await tools("describe", topic="options", session_id="m")
    items = options["values"]["items"]
    assert len(items) > 90
    assert items["FLOW_UNITS"] == "CFS"
    assert "FV_NODE_DT" in items and "RPT_NODES" in items


async def test_live_1d_series_while_running(tools, inp_path):
    await tools("open_model", path=inp_path, session_id="live")
    await tools("run", session_id="live", until="+2h")
    series = await tools(
        "timeseries", source="live", kind="node", ids=["J1"], variable="depth", live=True
    )
    assert series["periods"] > 0
    assert series["series"]["J1"]["values"]


async def test_2d_cell_and_edge_series(tools, output_dir):
    session = await _open_2d(tools, output_dir)
    await tools("run", session_id=session)
    cells = await tools("timeseries", source=session, kind="cell", ids=["0", "3"], variable="depth")
    assert set(cells["series"]) == {"0", "3"}
    assert cells["periods"] > 1
    edge = await tools(
        "timeseries", source=session, kind="edge", ids=["0:1"], variable="Mesh2_edge_flux"
    )
    assert edge["series"]["0:1"]["values"]
    exported = await tools(
        "export", session_id=session, path="cells.csv", kind="cell", variable="depth"
    )
    assert exported["columns"][0] == "time" and exported["rows"] == cells["periods"]


async def test_2d_series_errors_name_what_was_recorded(tools, output_dir):
    from fastmcp.exceptions import ToolError

    session = await _open_2d(tools, output_dir, "two_err")
    await tools("run", session_id=session)
    try:
        await tools("timeseries", source=session, kind="cell", ids=["0"], variable="dpth")
    except ToolError as exc:
        assert "depth" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("an unrecorded 2D variable must be refused")


async def test_flow_trace_over_the_session_network(tools, inp_path, output_dir):
    await tools("open_model", path=inp_path, session_id="t")
    await tools("run", session_id="t")
    out = str(output_dir / "t" / "site_drainage_model.out")
    await tools("call", session_id="t", target="trace", method="prepare", args={"output_path": out})
    result = await tools(
        "call", session_id="t", target="trace", method="estimate", args={"seed": "J1"}
    )
    assert result["result"]["summary"]["reached_nodes"] > 1
    await tools("call", session_id="t", target="trace", method="close")


async def test_surface_output_defaults_to_the_session_file(tools, output_dir):
    session = await _open_2d(tools, output_dir, "two_out")
    await tools("run", session_id=session)
    shape = await tools(
        "call",
        session_id=session,
        target="surface_output",
        method="shape",
        args={"path": "/Mesh2_face_depth"},
    )
    assert shape["result"]


async def test_2d_reports_on_a_meshed_model(tools, output_dir):
    session = await _open_2d(tools, output_dir, "two_report")
    await tools("run", session_id=session)
    for name in ("2d", "groundwater"):
        report = await tools("report", session_id=session, name=name)
        assert report["active"] and report["values"], name
