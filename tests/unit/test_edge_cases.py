"""Failure paths and edge cases of the core tools, on the real engine."""

from __future__ import annotations

from datetime import date, time

import numpy as np
import pytest
from fastmcp.exceptions import ToolError

from openswmm_mcp import catalog as cat


def test_parse_target_keeps_dots_in_ids():
    assert cat.parse_target("node:MH.1.outfall") == ("node.outfall", "MH.1")
    assert cat.parse_target("node:MH.1") == ("node", "MH.1")
    assert cat.parse_target("node:J1.stats") == ("node.stats", "J1")
    assert cat.parse_target("forcing") == ("forcing", None)


def test_to_json_numpy_scalars_and_dates():
    assert cat.to_json(np.bool_(True)) is True
    assert cat.to_json(np.array(2.5)) == 2.5
    assert cat.to_json(date(2024, 6, 1)) == "2024-06-01"
    assert cat.to_json(time(12, 30)) == "12:30:00"


async def test_failed_edit_then_run_rebuilds(tools, inp_path):
    await tools("open_model", path=inp_path)
    with pytest.raises(ToolError):  # N9 is added, then the duplicate J1 fails
        await tools(
            "edit",
            session_id="default",
            action="add",
            kind="node",
            ids=["N9", "J1"],
            type="JUNCTION",
        )
    ran = await tools("run", session_id="default")
    assert ran["finished"]


async def test_reset_keeps_edits_and_clone_resumes(tools, inp_path):
    await tools("open_model", path=inp_path)
    await tools(
        "set",
        session_id="default",
        kind="link",
        changes=[{"id": "C1", "field": "roughness", "value": 0.02}],
    )
    await tools("run", session_id="default", until="+3h")
    source = await tools("session", action="state", session_id="default")
    clone = await tools("session", action="clone", session_id="default", new_session_id="c")
    assert clone["start"] == source["current_time"]
    with pytest.raises(ToolError, match="exists"):
        await tools("session", action="clone", session_id="default", new_session_id="c")

    await tools("run", session_id="default")
    reset = await tools("session", action="reset", session_id="default")
    assert reset["state"] == "OPENED"
    got = await tools("get", session_id="default", kind="link", fields=["roughness"], ids=["C1"])
    assert got["columns"]["roughness"][0] == pytest.approx(0.02)
    with pytest.raises(ToolError, match="no finished results"):
        await tools("timeseries", source="default", kind="node", ids=["J1"], variable="depth")


async def test_xsect_from_link_matches_constructor(tools, inp_path):
    await tools("open_model", path=inp_path)
    fields = ["xsect.shape", "xsect.g1", "xsect.g2", "xsect.g3", "xsect.g4"]
    xs = await tools("get", session_id="default", kind="link", fields=fields, ids=["C1"])
    shape, *geoms = (xs["columns"][f][0] for f in fields)
    built = await tools(
        "call",
        session_id="default",
        target="xsect",
        method="area",
        args={"shape": shape, "units": "US", "depth": 0.5}
        | {f"geom{i}": g for i, g in enumerate(geoms, start=1)},
    )
    linked = await tools(
        "call",
        session_id="default",
        target="xsect",
        method="area",
        args={"from": {"method": "from_link", "link": "C1"}, "depth": 0.5},
    )
    assert linked["result"] == pytest.approx(built["result"]) and built["result"] > 0


async def test_dotted_ids_address_elements(tools):
    await tools("open_model", session_id="m")
    await tools(
        "edit",
        session_id="m",
        action="add",
        kind="node",
        ids=["MH.1"],
        type="OUTFALL",
        properties={"invert_elev": 1.0},
    )
    await tools(
        "set",
        session_id="m",
        kind="node.outfall",
        changes=[{"id": "MH.1", "field": "type", "value": "FIXED"}],
    )
    await tools(
        "call",
        session_id="m",
        target="node:MH.1.outfall",
        method="set_stage",
        args={"stage": 2.0},
    )
    got = await tools("get", session_id="m", kind="node", fields=["outfall.param"], ids=["MH.1"])
    assert got["columns"]["outfall.param"] == [2.0]
    found = await tools("find", session_id="m", kind="node", pattern=r"^MH\.")
    assert found["ids"] == ["MH.1"]


async def test_bad_inputs_are_tool_errors(tools, inp_path, output_dir):
    await tools("open_model", path=inp_path)
    with pytest.raises(ToolError, match="Bad pattern"):
        await tools("find", session_id="default", kind="node", pattern="(")
    await tools("run", session_id="default")
    with pytest.raises(ToolError, match="ISO datetime"):
        await tools(
            "timeseries",
            source="default",
            kind="node",
            ids=["J1"],
            variable="depth",
            start="yesterday",
        )
    bad = output_dir / "observed.csv"
    bad.write_text("time,J1\nnot-a-date,1.0\n")
    with pytest.raises(ToolError, match="observed CSV"):
        await tools("compare", a=str(bad), b="default", kind="node", variable="depth")


async def test_export_a_service(tools, inp_path):
    await tools("open_model", path=inp_path)
    await tools("run", session_id="default")
    fields = await tools("get", session_id="default", kind="options", fields=["routing_step"])
    exported = await tools(
        "export",
        session_id="default",
        path="options.csv",
        kind="options",
        fields=["routing_step"],
    )
    assert exported["rows"] == 1 and exported["columns"] == ["routing_step"]
    assert fields["values"]["routing_step"] is not None
