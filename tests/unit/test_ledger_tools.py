"""The ``tally`` tool: commit, facts, verify, prove, root and list on the reference model."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from .conftest import Tools

C2_ID = "f:link:C2:max_flow"
TSS_ID = "f:pollutant:TSS:continuity_error_pct"


@pytest.fixture
async def ledger_tools(output_dir: Path, monkeypatch):
    """A client to a server with the ledger toolset enabled."""
    monkeypatch.setenv("OPENSWMM_MCP_WORKING_DIR", str(output_dir))
    monkeypatch.setenv("OPENSWMM_MCP_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("OPENSWMM_MCP_TOOLSETS", "core,ledger")
    from openswmm_mcp.server import build_server

    async with Client(build_server()) as client:
        yield Tools(client)


def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _keys(v)


def _no_salt(result) -> None:
    assert "salt" not in set(_keys(result))


async def _committed(tools, inp_path, sid="led", label=None):
    await tools("open_model", path=inp_path, session_id=sid)
    await tools("run", session_id=sid, until="end")
    args = {"label": label} if label else {}
    commit = await tools("tally", action="commit", session_id=sid, **args)
    facts = await tools("tally", action="facts", tally_id=commit["tally_id"])
    return commit, facts["facts"]


def _picks(facts):
    by_id = {f["id"]: f for f in facts}
    continuity = [f for f in facts if "continuity_error_pct" in f["id"]]
    assert len(continuity) == 3, [f["id"] for f in continuity]
    assert TSS_ID in by_id and C2_ID in by_id
    return continuity, by_id[C2_ID]


def _narrative(facts_cited):
    text = "Mass balance closed. "
    for f in facts_cited:
        text += f"The {f['entity']} {f['attr']} was {f['value']} [{f['id']}]. "
    return text


def _claims(facts_cited):
    return [{"fact_id": f["id"], "value": f["value"]} for f in facts_cited]


async def test_verify_walkthrough(ledger_tools, inp_path, output_dir):
    tools = ledger_tools
    commit, facts = await _committed(tools, inp_path)
    continuity, c2 = _picks(facts)
    tally_id = commit["tally_id"]
    # Every material diagnostic (continuity or non-zero) must be cited for "verified".
    material = [
        f
        for f in facts
        if f["scope"] == "diagnostic" and (f in continuity or f["value"] not in (0, 0.0))
    ]
    assert TSS_ID in {f["id"] for f in material}
    cited = [*material, c2]
    narrative = _narrative(cited)
    claims = _claims(cited)

    # (a) correct narrative
    ok = await tools(
        "tally", action="verify", tally_id=tally_id, claims=claims, narrative=narrative
    )
    (output_dir / "verify_a.json").write_text(json.dumps(ok, indent=2))
    assert ok["summary"]["status"] == "verified", ok["summary"]
    assert all(r["verdict"] == "matched" for r in ok["results"])
    _no_salt(ok)

    # (b) altered value
    value = str(c2["value"])
    assert narrative.count(value) == 1
    bad_claims = [{**c, "value": 15.9} if c["fact_id"] == C2_ID else c for c in claims]
    bad = await tools(
        "tally",
        action="verify",
        tally_id=tally_id,
        claims=bad_claims,
        narrative=narrative.replace(value, "15.9"),
    )
    assert bad["summary"]["status"] == "failed"
    altered = [r for r in bad["results"] if r["verdict"] == "altered"]
    assert [r["fact_id"] for r in altered] == [C2_ID]
    _no_salt(bad)

    # (c) omit the TSS continuity fact
    partial = [f for f in cited if f["id"] != TSS_ID]
    gap = await tools(
        "tally",
        action="verify",
        tally_id=tally_id,
        claims=_claims(partial),
        narrative=_narrative(partial),
    )
    assert gap["summary"]["status"] == "verified_with_gaps", gap
    assert gap["coverage"]["value"] < 1
    assert TSS_ID in json.dumps(gap["coverage"]["uncited"])
    _no_salt(gap)


async def test_prove_and_cli(ledger_tools, inp_path, output_dir):
    tools = ledger_tools
    commit, facts = await _committed(tools, inp_path)
    continuity, _ = _picks(facts)
    proof = await tools(
        "tally", action="prove", tally_id=commit["tally_id"], fact_ids=[C2_ID, TSS_ID]
    )
    _no_salt(proof)
    assert len(proof["commands"]) == len(proof["proof_files"]) == 2

    for command in proof["commands"]:
        argv = shlex.split(command)
        if argv[0] == "python":
            argv[0] = sys.executable
        done = subprocess.run(argv, capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
        assert done.stdout.startswith("OK")

    # Corrupt the first hex character of a sibling hash in one proof file.
    victim = Path(proof["proof_files"][0])
    doc = json.loads(victim.read_text())
    done_ok = False
    for key, val in list(doc.items()):
        if isinstance(val, list) and val and isinstance(val[0], str):
            val[0] = ("0" if val[0][0] != "0" else "1") + val[0][1:]
            done_ok = True
            break
    if not done_ok:
        doc["fact"]["value"] = doc["fact"]["value"] + 1
    victim.write_text(json.dumps(doc))
    argv = shlex.split(proof["commands"][0])
    argv[0] = sys.executable
    done = subprocess.run(argv, capture_output=True, text=True)
    assert done.returncode == 1

    tally_file = Path(commit["path"])
    done = subprocess.run(
        [sys.executable, "-m", "openswmm_mcp.ledger", "root", str(tally_file)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0
    assert "salt" not in done.stdout


async def test_no_salt_leaks(ledger_tools, inp_path):
    tools = ledger_tools
    commit, facts = await _committed(tools, inp_path)
    tid = commit["tally_id"]
    salt = json.loads(Path(commit["path"]).read_text())["salt"]
    continuity, c2 = _picks(facts)
    results = [
        commit,
        {"facts": facts},
        await tools("tally", action="verify", tally_id=tid, claims=_claims([c2])),
        await tools("tally", action="prove", tally_id=tid, fact_ids=[C2_ID]),
        await tools("tally", action="root", tally_id=tid),
        await tools("tally", action="list"),
    ]
    for result in results:
        _no_salt(result)
        assert salt not in json.dumps(result)
    listing = results[-1]
    assert tid in [t["tally_id"] for t in listing["tallies"]]


async def test_commit_twice(ledger_tools, inp_path):
    tools = ledger_tools
    first, facts1 = await _committed(tools, inp_path)
    second = await tools("tally", action="commit", session_id="led")
    facts2 = (await tools("tally", action="facts", tally_id=second["tally_id"]))["facts"]
    assert [(f["id"], f["value"]) for f in facts1] == [(f["id"], f["value"]) for f in facts2]
    assert first["root"] != second["root"]


async def test_commit_before_run(ledger_tools, inp_path):
    await ledger_tools("open_model", path=inp_path, session_id="early")
    with pytest.raises(ToolError, match="INVALID_STATE"):
        await ledger_tools("tally", action="commit", session_id="early")


async def test_verify_unknown_tally(ledger_tools):
    with pytest.raises(ToolError, match="VALIDATION_ERROR"):
        await ledger_tools(
            "tally", action="verify", tally_id="nope/nothing", claims=[{"fact_id": "x", "value": 1}]
        )


async def test_edited_tally_file_is_refused(ledger_tools, inp_path):
    commit, _ = await _committed(ledger_tools, inp_path, sid="edit")
    path = Path(commit["path"])
    doc = json.loads(path.read_text(encoding="utf-8"))
    fact = next(f for f in doc["facts"] if f["id"] == "f:link:C2:max_flow")
    fact["value"] = 15.9
    path.write_text(json.dumps(doc), encoding="utf-8")
    claims = [{"fact_id": "f:link:C2:max_flow", "value": 15.9}]
    for action, extra in (("verify", {"claims": claims}), ("facts", {}), ("root", {})):
        with pytest.raises(ToolError, match="was modified"):
            await ledger_tools("tally", action=action, tally_id=commit["tally_id"], **extra)


async def test_facts_recommits_when_run_is_newer(ledger_tools, inp_path):
    tools = ledger_tools
    await tools("open_model", path=inp_path, session_id="fresh")
    await tools("run", session_id="fresh", until="end")
    first = await tools("tally", action="facts", session_id="fresh", label="m")
    assert first["committed"] is True and "recommitted" not in first
    root1 = (await tools("tally", action="root", tally_id=first["tally_id"]))["root"]
    again = await tools("tally", action="facts", session_id="fresh", label="m")
    assert again["committed"] is False
    assert (await tools("tally", action="root", tally_id=again["tally_id"]))["root"] == root1

    # Re-run: reset (it reopens as <stem>_edited.inp, so pin the label to keep the tally id).
    # Reset reopens the model, then run writes a new .out file.
    await tools("session", action="reset", session_id="fresh")
    await tools("run", session_id="fresh", until="end")
    newer = await tools("tally", action="facts", session_id="fresh", label="m")
    assert newer["committed"] is True
    assert newer["recommitted"].endswith(root1[:12])
    # tally_id is session/label, so the new tally overwrote the old one: the pinned id
    # now resolves to the new root.
    root2 = (await tools("tally", action="root", tally_id=newer["tally_id"]))["root"]
    assert root2 != root1
    pinned = await tools("tally", action="facts", tally_id=first["tally_id"])
    assert pinned["committed"] is False and "recommitted" not in pinned
    assert (await tools("tally", action="root", tally_id=pinned["tally_id"]))["root"] == root2
