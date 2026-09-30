"""Every catalogued method is callable through ``call`` with the catalog's signature.

For each method in the engine catalog, ``call`` either binds the arguments and
reaches the engine, or refuses with a typed error. It never leaks a Python
``TypeError`` from a signature the catalog got wrong, and no target lacks a
handler:

* a method with required parameters, called without them, names the first
  missing one before anything reaches the engine;
* a method without required parameters runs on a fresh session and returns
  JSON or a typed refusal (a lifecycle phase, an unconfigured service).

Standalone targets get their constructor arguments: a cross-section, a new
GeoPackage and a hot start file saved from the 1D run.

The 1D fixture drives every target except ``surface2d*``, which runs on the
2D fixture.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from fastmcp.exceptions import ToolError
from openswmm.engine import Solver

from openswmm_mcp import catalog as cat
from openswmm_mcp.tools.access import _ids

DATA = Path(__file__).parent / "data"
MODELS = {"1d": "site_drainage_model.inp", "2d": "twod_example.inp"}

# Refusals a zero-argument call may return on a fresh session. GeoPackage
# signals state errors (commit without begin) as a plain RuntimeError.
REFUSED = re.compile(
    r"\[(INVALID_STATE|NOT_SUPPORTED|ENGINE_ERROR|ELEMENT_NOT_FOUND)\]|\] RuntimeError: "
)
# What a wrong catalog signature looks like once translated.
SIGNATURE_BUG = re.compile(r"TypeError|has no MCP handler|Unknown argument")
# Constructor arguments that let standalone targets reach their methods' binding.
CONSTRUCT = {
    "xsect": {"shape": "CIRCULAR", "geom1": 1.0, "units": "US"},
    "geopackage": {"path": "contract.gpkg"},
    "hotstart": {"path": "contract.hsf"},
}


def _model(name: str) -> str:
    return "2d" if name.startswith("surface2d") else "1d"


def _required(member: dict) -> list[str]:
    return [
        p["name"] for p in member.get("params", []) if p["required"] and "Solver" not in p["type"]
    ]


def _methods() -> list[tuple[str, str, dict]]:
    out = []
    for name in sorted(cat.targets()):
        for method, member in sorted(cat.members(name).items()):
            if member["form"] == "property":
                continue
            if name == "solver" and method in cat.SOLVER_DENY:
                continue
            out.append((name, method, member))
    for module, functions in sorted(cat.catalog()["functions"].items()):
        for method, member in sorted(functions.items()):
            out.append((module.lstrip("_"), method, dict(member, path=f"{module}.{method}")))
    return out


def _target_strings(inp: str, out_dir: Path) -> dict[str, str | None]:
    """``call`` target text per catalog target: an element of the right subtype for
    element targets (``node:O1.outfall``), None when the model has none."""
    solver = Solver(inp, str(out_dir / "probe.rpt"), str(out_dir / "probe.out"))
    solver.open()
    try:
        strings: dict[str, str | None] = {}
        for name in cat.targets():
            kind = cat.element_of(name)
            if kind is None:
                strings[name] = name
                continue
            sub = name[len(kind) + 1 :]
            strings[name] = None
            for key in _ids(solver, kind):
                try:
                    cat.resolve(solver, name, key)
                except Exception:
                    continue
                strings[name] = f"{kind}:{key}" + (f".{sub}" if sub else "")
                break
        return strings
    finally:
        solver.close()


async def _open(tools, output_dir: Path, model: str, session_id: str) -> str:
    inp = output_dir / MODELS[model]
    if not inp.exists():
        shutil.copy(DATA / MODELS[model], inp)
    await tools("open_model", path=str(inp), session_id=session_id)
    return str(inp)


def _args(name: str, member: dict) -> dict:
    return {} if member.get("static") else dict(CONSTRUCT.get(name, {}))


async def _save_hotstart(tools, output_dir: Path) -> None:
    """A hot start file mid-run (the engine saves one only while running)."""
    await _open(tools, output_dir, "1d", "hs")
    await tools("run", session_id="hs", max_steps=10)
    path = CONSTRUCT["hotstart"]["path"]
    await tools("call", session_id="hs", target="hotstart", method="save_from", args={"path": path})
    await tools("session", action="close", session_id="hs")


async def _call(tools, session_id: str, target: str, method: str, args: dict) -> str | None:
    """The refusal message, or None when the call returned JSON."""
    try:
        result = await tools("call", session_id=session_id, target=target, method=method, args=args)
    except ToolError as exc:
        return str(exc)
    json.dumps(result)
    return None


async def test_required_arguments_are_named(tools, output_dir):
    strings = {}
    for model in MODELS:
        inp = await _open(tools, output_dir, model, model)
        await tools("run", session_id=model)  # the output reader needs a results file
        strings[model] = _target_strings(inp, output_dir)
    await _save_hotstart(tools, output_dir)
    checked, problems = 0, []
    for name, method, member in _methods():
        required = _required(member)
        target = strings[_model(name)].get(name, name)
        if not required or target is None:
            continue
        message = await _call(tools, _model(name), target, method, _args(name, member))
        checked += 1
        if message is None or f"Missing argument '{required[0]}'" not in message:
            problems.append(f"{name}.{method}: {message}")
    assert problems == []
    assert checked > 450


async def test_zero_argument_methods_run_on_a_fresh_session(tools, output_dir):
    await _save_hotstart(tools, output_dir)
    strings = {model: None for model in MODELS}
    problems, ran = [], 0
    for i, (name, method, member) in enumerate(_methods()):
        if _required(member):
            continue
        model = _model(name)
        session_id = f"z{i}"
        inp = await _open(tools, output_dir, model, session_id)
        if strings[model] is None:
            strings[model] = _target_strings(inp, output_dir)
        target = strings[model].get(name, name)
        if target is None:
            await tools("session", action="close", session_id=session_id)
            continue
        message = await _call(tools, session_id, target, method, _args(name, member))
        await tools("session", action="close", session_id=session_id)
        if message is None:
            ran += 1
        elif SIGNATURE_BUG.search(message) or not REFUSED.search(message):
            problems.append(f"{name}.{method}: {message}")
    assert problems == []
    assert ran > 60
