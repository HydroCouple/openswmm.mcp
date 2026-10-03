"""End-to-end workflows through the 14 core tools, on the real engine."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError


async def test_open_run_and_report(tools, inp_path):
    opened = await tools("open_model", path=inp_path)
    assert opened["state"] == "OPENED"
    assert opened["counts"] == {
        "gage": 1,
        "link": 11,
        "node": 12,
        "pollutant": 1,
        "subcatchment": 7,
        "table": opened["counts"]["table"],
    }
    assert opened["options"]["FLOW_UNITS"] == "CFS"

    partial = await tools("run", session_id="default", until="+2h")
    assert partial["finished"] is False and 0 < partial["progress"] < 1
    done = await tools("run", session_id="default")
    assert done["finished"] and done["state"] == "ENDED"
    assert abs(done["continuity_errors"]["routing"]) < 0.05
    assert Path(done["report_file"]).is_file() and Path(done["output_file"]).is_file()

    for name in (
        "summary",
        "mass_balance",
        "flooding",
        "capacity",
        "storage",
        "pumps",
        "subcatchments",
        "quality",
        "2d",
        "groundwater",
    ):
        json.dumps(await tools("report", session_id="default", name=name))
    capacity = await tools("report", session_id="default", name="capacity", top=3)
    fills = [r["max_filling"] for r in capacity["rows"]]
    assert len(fills) == 3 and fills == sorted(fills, reverse=True)
    stats = await tools("report", session_id="default", name="statistics", kind="link", ids=["C1"])
    assert stats["rows"][0]["id"] == "C1" and stats["rows"][0]["max_flow"] > 0

    with pytest.raises(ToolError, match="finished"):
        await tools("run", session_id="default")


async def test_discover_and_query(tools, inp_path):
    overview = await tools("describe")
    assert {"node", "link", "subcatchment"} <= set(overview["element_kinds"])
    assert "forcing" in overview["services"]
    await tools("open_model", path=inp_path, session_id="m")
    node = await tools("describe", topic="node", session_id="m")
    depth = next(f for f in node["fields"] if f["name"] == "depth")
    assert depth["units"] == "ft" and depth["access"] == "rw"
    assert "stats" in node["subviews"]
    member = await tools("describe", topic="forcing.node_lat_inflow")
    assert "node" in member["signature"]
    assert (await tools("describe", topic="enum:NodeType"))["members"]["OUTFALL"] == 1
    assert "node" in await tools("describe", topic="output")

    outfalls = await tools("find", session_id="m", kind="node", type="OUTFALL")
    assert outfalls["ids"] == ["O1"]
    deep = await tools("find", session_id="m", kind="node", where="max_depth >= 5")
    assert deep["ids"] and all(i for i in deep["ids"])
    table = await tools(
        "get", session_id="m", kind="link", fields=["length", "xsect.g1", "type"], ids=["C1", "C2"]
    )
    assert table["ids"] == ["C1", "C2"] and table["units"]["length"] == "ft"
    assert table["columns"]["type"] == ["CONDUIT", "CONDUIT"]


async def test_set_call_and_forcing_change_results(tools, inp_path):
    await tools("open_model", path=inp_path, session_id="base")
    await tools("open_model", path=inp_path, session_id="wet")
    changed = await tools(
        "set",
        session_id="wet",
        kind="link",
        changes=[
            {"id": "C1", "field": "roughness", "value": 0.02},
            {"id": "C1", "field": "id", "value": "nope"},
        ],
    )
    assert changed["applied"] == 1 and changed["results"][1]["ok"] is False
    await tools("run", session_id="wet", max_steps=10)
    forced = await tools(
        "call",
        session_id="wet",
        target="forcing",
        method="node_lat_inflow",
        args={"node": "J1", "value": 50.0, "persist": True},
    )
    assert forced["method"] == "node_lat_inflow"
    await tools("run", session_id="wet")
    await tools("run", session_id="base")

    compared = await tools("compare", a="base", b="wet", kind="node", variable="depth")
    assert compared["compared"] == 12
    j1 = next(r for r in compared["rows"] if r["id"] == "J1")
    assert j1["peak_b"] > j1["peak_a"]
    with pytest.raises(ToolError, match="run/session/save"):
        await tools("call", session_id="wet", target="solver", method="step")


async def test_results_timeseries_export_and_observed(tools, inp_path, output_dir):
    await tools("open_model", path=inp_path)
    await tools("run", session_id="default")
    series = await tools(
        "timeseries", source="default", kind="node", ids=["J1"], variable="depth", max_points=40
    )
    j1 = series["series"]["J1"]
    assert len(j1["values"]) <= 40 and max(j1["values"]) == pytest.approx(j1["peak"], rel=1e-6)
    quality = await tools(
        "timeseries", source="default", kind="link", ids=["C1"], variable="quality:TSS"
    )
    assert quality["series"]["C1"]["values"]
    system = await tools("timeseries", source="default", kind="system", ids=[], variable="rainfall")
    assert system["series"]["system"]["values"]

    exported = await tools(
        "export",
        session_id="default",
        path="nodes.csv",
        kind="node",
        fields=["invert_elev", "stats.max_depth"],
    )
    assert exported["rows"] == 12
    series_file = await tools(
        "export", session_id="default", path="flows.json", kind="link", variable="flow"
    )
    rows = json.loads(Path(series_file["path"]).read_text())
    assert set(rows[0]) >= {"time", "C1"}

    observed = output_dir / "observed.csv"
    full = await tools(
        "timeseries", source="default", kind="node", ids=["J1"], variable="depth", max_points=100000
    )
    with observed.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["time", "J1"])
        for t, v in zip(full["series"]["J1"]["times"], full["series"]["J1"]["values"]):
            writer.writerow([t, v])
    fit = await tools("compare", a=str(observed), b="default", kind="node", variable="depth")
    assert fit["rows"][0]["nse"] == pytest.approx(1.0, abs=1e-6)


async def test_save_clone_reset_and_close(tools, inp_path, output_dir):
    await tools("open_model", path=inp_path)
    await tools("run", session_id="default", until="+6h")
    for name, fmt in (
        ("copy.inp", "inp"),
        ("copy.gpkg", "gpkg"),
        ("state.hsf", "hotstart"),
        ("swmm5.inp", "inp"),
    ):
        saved = await tools(
            "save",
            session_id="default",
            path=name,
            profile="SWMM5" if name == "swmm5.inp" else None,
        )
        assert saved["format"] == fmt and Path(saved["path"]).is_file()
    clone = await tools("session", action="clone", session_id="default", new_session_id="c")
    assert clone["state"] == "INITIALIZED"
    listed = await tools("session", action="list")
    assert {s["session_id"] for s in listed["sessions"]} == {"default", "c"}
    reset = await tools("session", action="reset", session_id="default")
    assert reset["state"] == "OPENED"
    await tools("session", action="close", session_id="c")
    with pytest.raises(ToolError, match="No session 'c'"):
        await tools("session", action="state", session_id="c")


async def test_build_a_model_from_scratch(tools):
    created = await tools("open_model", session_id="new")
    assert created["state"] == "OPENED" and not created["counts"]
    await tools(
        "edit",
        session_id="new",
        action="add",
        kind="node",
        ids=["J1"],
        type="JUNCTION",
        properties={"invert_elev": 10.0, "max_depth": 5.0},
    )
    await tools(
        "edit",
        session_id="new",
        action="add",
        kind="node",
        ids=["O1"],
        type="OUTFALL",
        properties={"invert_elev": 9.0},
    )
    await tools(
        "edit",
        session_id="new",
        action="add",
        kind="link",
        ids=["C1"],
        type="CONDUIT",
        properties={"from_node": "J1", "to_node": "O1", "length": 100.0, "roughness": 0.013},
    )
    await tools(
        "call",
        session_id="new",
        target="link:C1",
        method="set_nodes",
        args={"from_node": "J1", "to_node": "O1"},
    )
    await tools(
        "set",
        session_id="new",
        kind="link.xsect",
        changes=[{"id": "C1", "field": "g1", "value": 1.0}],
    )
    ran = await tools("run", session_id="new")
    assert ran["finished"]
    summary = await tools("report", session_id="new", name="summary")
    assert summary["counts"] == {"link": 1, "node": 2}


async def test_structural_edits(tools, inp_path):
    await tools("open_model", path=inp_path)
    preview = await tools(
        "edit", session_id="default", action="preview_delete", kind="node", ids=["J2"]
    )
    assert preview["results"][0]["impact"]
    await tools(
        "edit", session_id="default", action="rename", kind="link", ids=["C1"], new_id="C1a"
    )
    await tools("edit", session_id="default", action="delete", kind="subcatchment", ids=["S7"])
    await tools(
        "edit", session_id="default", action="convert", kind="node", ids=["J11"], type="STORAGE"
    )
    found = await tools("find", session_id="default", kind="node", type="STORAGE")
    assert "J11" in found["ids"]
    ran = await tools("run", session_id="default")
    assert ran["finished"]
    summary = await tools("report", session_id="default", name="summary")
    assert summary["counts"]["subcatchment"] == 6
    assert summary["files"]["inp"].endswith("_edited.inp")


async def test_author_with_the_builder(tools, output_dir):
    """The docs' `builder` walkthrough: author, write, open and run."""
    await tools("open_model", session_id="b")
    kinds = await tools("describe", topic="enum:NodeType")
    assert "JUNCTION" in json.dumps(kinds)

    async def build(method, **args):
        return await tools("call", session_id="b", target="builder", method=method, args=args)

    assert (await build("add_node", node_id="J1", node_type=0))["result"] == 0
    await build("add_node", node_id="O1", node_type=1)
    await build("add_link", link_id="C1", link_type=0)
    await build("set_node_invert", idx=0, elev=10.0)
    await build("set_node_max_depth", idx=0, depth=5.0)
    await build("set_node_invert", idx=1, elev=9.0)
    await build("set_link_nodes", idx=0, from_node=0, to_node=1)
    await build("set_link_length", idx=0, length=100.0)
    await build("set_link_xsect", idx=0, shape=0, g1=1.0)
    for key, value in (
        ("START_DATE", "01/01/2026"),
        ("END_DATE", "01/01/2026"),
        ("END_TIME", "06:00:00"),
    ):
        await build("set_option", key=key, value=value)
    await build("write", path="built.inp")
    with pytest.raises(ToolError, match="cannot run in place"):
        await build("to_solver")

    opened = await tools("open_model", path=str(output_dir / "built.inp"), session_id="run")
    assert opened["counts"] == {"link": 1, "node": 2}
    assert (await tools("run", session_id="run"))["finished"]
