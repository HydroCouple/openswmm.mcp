"""Ledger tool set (``OPENSWMM_MCP_TOOLSETS=core,ledger``): one ``tally`` tool.

``tally`` commits the facts of a finished run to a salted hash tree, lists them for
citation, verifies claims against them and discloses single facts for reviewers.
The tally JSON file under the session folder is the source of truth; the salt in it
is never returned by any action.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Literal

from fastmcp import Context

from openswmm_mcp.dependencies import get_ledger_store, get_session_manager
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.ledger import merkle
from openswmm_mcp.ledger.facts import extract_facts
from openswmm_mcp.ledger.schema import FACT_SCHEMA, SCHEMA_VERSION
from openswmm_mcp.ledger.store import LedgerStore
from openswmm_mcp.ledger.verify import verify_claims

_LABEL = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")
_FACT_VIEW = ("id", "kind", "entity", "attr", "value", "unit", "time", "scope")


def _bad(message: str) -> ToolError:
    return ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {message}")


def _need(value: Any, name: str, action: str) -> Any:
    if value in (None, "", []):
        raise _bad(f"action '{action}' needs '{name}'.")
    return value


def _check_label(label: str) -> str:
    if not _LABEL.fullmatch(label):
        raise _bad(
            f"label may use letters, digits, '_', '-' and '.' (not starting with '.'); "
            f"got '{label}'."
        )
    return label


def _public(tally: dict) -> dict:
    """The shareable half of a tally; never includes the salt."""
    return {
        "tally_id": tally.get("tally_id"),
        "root": tally.get("root"),
        "fact_count": tally.get("fact_count"),
        "meta": tally.get("meta"),
        "schema": tally.get("schema"),
    }


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_tally(tally_id: str, facts: list[dict], meta: dict) -> tuple[dict, bytes]:
    salt = secrets.token_bytes(32)
    levels, leaves = merkle.commit_facts(salt, facts)
    count = len(facts)
    root = merkle.published_root(merkle.tree_root(levels), count).hex()
    used = {f"{f['kind']}.{f['attr']}" for f in facts}
    tally = {
        "tally_id": tally_id,
        "root": root,
        "fact_count": count,
        "salt": salt.hex(),
        "meta": meta,
        "schema": {
            "version": SCHEMA_VERSION,
            "attributes": {k: v for k, v in FACT_SCHEMA.items() if k in used},
        },
        "facts": [{**f, "leaf": leaf.hex()} for f, leaf in zip(facts, leaves)],
        "sources_root": None,
    }
    return tally, salt


def _fingerprint(out_path: str) -> dict | None:
    """Identify the run that wrote the .out file (same path every run, so use mtime+size)."""
    try:
        st = os.stat(out_path)
    except OSError:
        return None
    return {"out_path": out_path, "out_mtime_ns": st.st_mtime_ns, "out_size": st.st_size}


async def _commit(ctx: Context, session_id: str, label: str | None) -> tuple[dict, Path]:
    sm = get_session_manager(ctx)
    store = get_ledger_store(ctx)
    session = await sm.get(session_id)
    label = _check_label(label or Path(session.inp_path).stem)
    facts, meta = await session.call(extract_facts, session, context="committing facts")
    if not facts:
        raise _bad(f"Session '{session_id}' produced no facts to commit.")
    tally_id = f"{session_id}/{label}"
    path = sm.session_dir(session_id) / f"{label}.tally.json"
    fingerprint = await asyncio.to_thread(_fingerprint, session.out_path)
    tally, _ = _build_tally(tally_id, facts, {**meta, "run_fingerprint": fingerprint})

    def _persist() -> None:
        _write_json(path, tally)
        store.record_tally(
            tally_id=tally_id,
            session_id=session_id,
            label=label,
            root=tally["root"],
            fact_count=tally["fact_count"],
            path=str(path),
            inp_sha256=str(meta.get("inp_sha256", "")),
        )

    await asyncio.to_thread(_persist)
    return tally, path


def _check_root(doc: dict, tally_id: str) -> None:
    """Refuse a tally file whose facts no longer hash to its root (it was edited)."""
    try:
        facts = [{k: v for k, v in f.items() if k != "leaf"} for f in doc["facts"]]
        levels, _ = merkle.commit_facts(bytes.fromhex(doc["salt"]), facts)
        ok = merkle.published_root(merkle.tree_root(levels), len(facts)).hex() == doc["root"]
    except (KeyError, TypeError, ValueError):
        ok = False
    if not ok:
        raise _bad(f"Tally file for '{tally_id}' does not match its root; it was modified.")


async def _load(ctx: Context, tally_id: str) -> tuple[dict, Path]:
    """Resolve ``<session_id>/<label>`` to its tally file, store row first."""
    sm = get_session_manager(ctx)
    store: LedgerStore = get_ledger_store(ctx)
    row = await asyncio.to_thread(store.get_tally, tally_id)
    candidates: list[Path] = []
    if row:
        candidates.append(Path(row["path"]))
    session_id, _, label = tally_id.partition("/")
    if _LABEL.fullmatch(session_id) and _LABEL.fullmatch(label):
        candidates.append(sm.working_dir / session_id / f"{label}.tally.json")
    for path in candidates:
        if path.is_file():
            try:
                doc = await asyncio.to_thread(_read_json, path)
            except (OSError, ValueError) as exc:
                raise _bad(f"Tally file for '{tally_id}' is unreadable: {exc}") from exc
            await asyncio.to_thread(_check_root, doc, tally_id)
            return doc, path
    known = [r["tally_id"] for r in await asyncio.to_thread(store.list_tallies)]
    close = difflib.get_close_matches(tally_id, known, n=3, cutoff=0.4)
    hint = f" Close matches: {close}." if close else ""
    raise _bad(f"Unknown tally_id '{tally_id}' (expected '<session_id>/<label>').{hint}")


async def _resolve(
    ctx: Context, tally_id: str | None, session_id: str | None, label: str | None, action: str
) -> tuple[dict, Path, bool, dict]:
    """Return (tally, path, committed, notes); notes may hold 'recommitted' or 'warning'."""
    if tally_id:  # a pinned reference: always exactly this tally
        tally, path = await _load(ctx, tally_id)
        return tally, path, False, {}
    if not session_id:
        raise _bad(f"action '{action}' needs 'tally_id' or 'session_id'.")
    session = await get_session_manager(ctx).get(session_id)
    name = _check_label(label or Path(session.inp_path).stem)
    try:
        tally, path = await _load(ctx, f"{session_id}/{name}")
    except ToolError:
        tally, path = await _commit(ctx, session_id, name)
        return tally, path, True, {}
    state = await session.call(lambda: session.state)
    if state != "ENDED":
        warning = f"Session has no finished run (state {state}); reusing the existing tally."
        return tally, path, False, {"warning": warning}
    current = await asyncio.to_thread(_fingerprint, session.out_path)
    if (tally.get("meta") or {}).get("run_fingerprint") == current:
        return tally, path, False, {}
    note = f"run is newer than tally {str(tally.get('root'))[:12]}"
    tally, path = await _commit(ctx, session_id, name)
    return tally, path, True, {"recommitted": note}


async def tally(
    ctx: Context,
    action: Literal["commit", "facts", "verify", "prove", "root", "list"],
    tally_id: str | None = None,
    session_id: str | None = None,
    label: str | None = None,
    claims: list[dict] | None = None,
    narrative: str | None = None,
    fact_ids: list[str] | None = None,
    kind: str | None = None,
    ids: list[str] | None = None,
) -> dict:
    """Commit a finished run's facts to a salted hash tree (commit, needs session_id);
    facts lists them with ids for citation; verify checks claims [{fact_id, value}] and
    an optional narrative against tally_id; prove discloses fact_ids to a reviewer who has
    only the root; root and list return the shareable half (tally_id is session/label)."""
    if action == "list":
        store = get_ledger_store(ctx)
        rows = await asyncio.to_thread(store.list_tallies)

        def _read_all() -> tuple[list[dict], list[str]]:
            found, missing = [], []
            for row in rows:
                try:
                    found.append(_public(_read_json(Path(row["path"]))))
                except (OSError, ValueError):
                    missing.append(row["tally_id"])
            return found, missing

        found, missing = await asyncio.to_thread(_read_all)
        return {"tallies": found, "missing": missing}

    if action == "commit":
        _need(session_id, "session_id", action)
        tally_doc, path = await _commit(ctx, session_id, label)
        return {
            **_public(tally_doc),
            "path": str(path),
            "dropped": (tally_doc["meta"] or {}).get("dropped", []),
        }

    if action == "facts":
        doc, _, committed, notes = await _resolve(ctx, tally_id, session_id, label, action)
        wanted = set(ids or [])
        rows = [
            {k: f.get(k) for k in _FACT_VIEW}
            for f in doc["facts"]
            if (not kind or f["kind"] == kind) and (not wanted or f["entity"] in wanted)
        ]
        return {"tally_id": doc["tally_id"], "committed": committed, **notes, "facts": rows}

    _need(tally_id, "tally_id", action)
    doc, path = await _load(ctx, tally_id)

    if action == "root":
        return _public(doc)

    if action == "verify":
        _need(claims, "claims", action)
        return verify_claims(doc, claims, narrative)

    if action == "prove":
        _need(fact_ids, "fact_ids", action)
        facts = [{k: v for k, v in f.items() if k != "leaf"} for f in doc["facts"]]
        index = {f["id"]: i for i, f in enumerate(facts)}
        for fid in fact_ids:
            if fid not in index:
                close = difflib.get_close_matches(fid, list(index), n=3, cutoff=0.6)
                hint = f" Close matches: {close}." if close else ""
                raise _bad(f"Unknown fact id '{fid}' in {tally_id}.{hint}")
        salt = bytes.fromhex(doc["salt"])
        levels, _ = merkle.commit_facts(salt, facts)
        disclosures = [merkle.disclose(salt, facts, index[fid], levels) for fid in fact_ids]
        stem = path.name.removesuffix(".tally.json")
        files = [path.with_name(f"{stem}.proof.{n}.json") for n in range(1, len(fact_ids) + 1)]

        def _write_all() -> None:
            for file, disclosure in zip(files, disclosures):
                _write_json(file, disclosure)

        await asyncio.to_thread(_write_all)
        commands = [
            f"python -m openswmm_mcp.ledger verify-proof --root {doc['root']} "
            f"--count {doc['fact_count']} --proof {file}"
            for file in files
        ]
        return {
            "tally_id": doc["tally_id"],
            "root": doc["root"],
            "fact_count": doc["fact_count"],
            "disclosures": disclosures,
            "proof_files": [str(f) for f in files],
            "commands": commands,
        }

    raise _bad(f"Unknown action '{action}'.")


TOOLS = (tally,)
