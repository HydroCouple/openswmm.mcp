"""Every engine field in the catalog is reachable through the generic tools.

``get`` must read every property of every element kind present in the model,
and every service property, returning JSON or a typed engine refusal (a
lifecycle phase or a subtype the element does not have) -- never a crash or an
unserialisable value. Writable scalar fields must accept their own value back.
"""

from __future__ import annotations

import inspect
import json
import re

import openswmm.engine
import pytest
from fastmcp.exceptions import ToolError

from openswmm_mcp import catalog as cat

# Engine refusals a read may legitimately return: any typed engine error (a
# lifecycle phase, a missing CRS, ...), a subtype the element lacks
# (AttributeError), or an absent optional row (KeyError / IndexError).
_ENGINE_ERRORS = [
    n
    for n, o in inspect.getmembers(openswmm.engine)
    if inspect.isclass(o) and issubclass(o, openswmm.engine.EngineError)
]
ALLOWED = re.compile(rf"^({'|'.join(_ENGINE_ERRORS)}|AttributeError|KeyError|IndexError)\b")


def _element_fields() -> dict[str, list[str]]:
    out = {}
    for kind, entry in cat.element_kinds().items():
        if "." not in entry["collection"]:
            out[kind] = cat.field_names(kind)
    return out


def _service_targets() -> list[str]:
    return sorted(
        name
        for name, t in cat.targets().items()
        if "collection" not in t
        and "construct" not in t
        and cat.element_of(name) is None
        and any(m["form"] == "property" for m in cat.members(name).values())
    )


async def _open(tools, inp_path, run: bool):
    await tools("open_model", path=inp_path, session_id="s")
    if run:
        await tools("run", session_id="s")


@pytest.mark.parametrize("run", [False, True], ids=["opened", "ended"])
async def test_every_element_field_reads(tools, inp_path, run):
    await _open(tools, inp_path, run)
    counts = (await tools("describe", session_id="s"))["element_kinds"]
    checked = 0
    for kind, fields in _element_fields().items():
        if not counts.get(kind, {}).get("count"):
            continue
        table = await tools("get", session_id="s", kind=kind, fields=fields)
        json.dumps(table)
        for field, message in table.get("errors", {}).items():
            assert ALLOWED.match(message), f"{kind}.{field}: {message}"
        checked += len(fields)
    assert checked > 150


@pytest.mark.parametrize("run", [False, True], ids=["opened", "ended"])
async def test_every_service_field_reads(tools, inp_path, run):
    await _open(tools, inp_path, run)
    for target in _service_targets():
        if target.startswith("surface2d"):
            continue  # the fixture has no 2D surface; see test_twod_surface
        fields = [m["name"] for m in cat.members(target).values() if m["form"] == "property"]
        values = await tools("get", session_id="s", kind=target, fields=fields)
        json.dumps(values)
        for field, message in values.get("errors", {}).items():
            assert ALLOWED.match(message), f"{target}.{field}: {message}"


async def test_writable_scalars_accept_their_own_value(tools, inp_path):
    await _open(tools, inp_path, run=False)
    for kind, fields in _element_fields().items():
        writable = [
            f
            for f in fields
            if cat.field(kind, f)["access"] == "rw"
            and cat.field(kind, f)["type"] in ("float", "int", "bool", "str")
        ]
        if not writable:
            continue
        table = await tools("get", session_id="s", kind=kind, fields=writable, limit=1)
        if not table["ids"]:
            continue
        key = table["ids"][0]
        changes = [
            {"id": key, "field": f, "value": table["columns"][f][0]}
            for f in writable
            if table["columns"][f][0] is not None
        ]
        result = await tools("set", session_id="s", kind=kind, changes=changes)
        for r in result["results"]:
            assert (
                r["ok"]
                or ALLOWED.match(r["error"])
                or "INVALID_STATE" in r["error"]
                or "NOT_SUPPORTED" in r["error"]
            ), f"{kind}.{r['field']}: {r['error']}"


async def test_twod_surface(tools, output_dir):
    import shutil
    from pathlib import Path

    inp = output_dir / "twod_example.inp"
    shutil.copy(Path(__file__).parent / "data" / "twod_example.inp", inp)
    await tools("open_model", path=str(inp), session_id="s2")
    result = await tools("run", session_id="s2")
    assert result["finished"]
    report = await tools("report", session_id="s2", name="2d")
    assert report["active"] and "continuity_error" in report["values"]
    fields = [m["name"] for m in cat.members("surface2d").values() if m["form"] == "property"]
    values = await tools("get", session_id="s2", kind="surface2d", fields=fields)
    assert values["values"]["n_cells"] > 0
    for field, message in values.get("errors", {}).items():
        assert ALLOWED.match(message), f"surface2d.{field}: {message}"
    # The fixture has no subcatchments: an empty kind reads as no rows.
    assert (await tools("get", session_id="s2", kind="subcatchment", fields=["area"]))["ids"] == []
    assert (await tools("find", session_id="s2", kind="subcatchment"))["ids"] == []


async def test_unknown_names_suggest_alternatives(tools, inp_path):
    await _open(tools, inp_path, run=False)
    with pytest.raises(ToolError, match="Did you mean: .*depth"):
        await tools("get", session_id="s", kind="node", fields=["depht"])
    with pytest.raises(ToolError, match="node_lat_inflow"):
        await tools("call", session_id="s", target="forcing", method="node_lat_inflw", args={})
    with pytest.raises(ToolError, match="Unknown topic 'nodez'. Did you mean: node"):
        await tools("describe", topic="nodez")
