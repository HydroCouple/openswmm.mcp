"""Ledger tool set (``OPENSWMM_MCP_TOOLSETS=core,ledger``): ``tally``, ``sources``, ``locate``.

``tally`` commits the facts of a finished run to a salted hash tree, lists them for
citation, verifies claims against them and discloses single facts for reviewers.
The tally JSON file under the session folder is the source of truth; the salt in it
is never returned by any action.

``sources`` registers the files a model was built from (bitemporal, append-only rows),
links their features to model elements and answers lineage and mention questions.
``locate`` reads everything the ledger knows near an element or point; it never writes.
Exact geometry needs the ``ledger`` extra (shapely, pyproj), imported only inside
functions; without it ``locate`` runs in a degraded, lineage-only mode.
"""

from __future__ import annotations

import asyncio
import difflib
import json
import math
import os
import re
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastmcp import Context

from openswmm_mcp._util.validation import resolve_path
from openswmm_mcp.dependencies import get_ledger_store, get_session_manager, require_spatial
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.ledger import ingest, merkle
from openswmm_mcp.ledger.common import (
    ELEMENT_KINDS,
    MAP_UNITS,
    ROLES,
    SOURCE_ID,
    CrsRefusal,
    canonical_time,
    check_model_crs,
    crs_class,
    crs_label,
    crs_verdict,
    resolve_model_crs,
    utc_now,
)
from openswmm_mcp.ledger.facts import extract_facts
from openswmm_mcp.ledger.model_geometry import model_elements
from openswmm_mcp.ledger.schema import FACT_SCHEMA, SCHEMA_VERSION
from openswmm_mcp.ledger.store import LedgerError, LedgerStore
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
        "sources_root": tally.get("sources_root"),
        "sources": tally.get("sources") or [],
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
        "sources": [],
    }
    return tally, salt


def _fingerprint(out_path: str) -> dict | None:
    """Identify the run that wrote the .out file (same path every run, so use mtime+size)."""
    try:
        st = os.stat(out_path)
    except OSError:
        return None
    return {"out_path": out_path, "out_mtime_ns": st.st_mtime_ns, "out_size": st.st_size}


def _archive_previous(path: Path) -> None:
    """Keep the tally file being replaced as ``<label>.tally.<oldroot[:12]>.json``."""
    if not path.is_file():
        return
    try:
        old_root = str(_read_json(path).get("root") or "")
    except (OSError, ValueError):
        return
    if not old_root:
        return
    stem = path.name.removesuffix(".tally.json")
    archive = path.with_name(f"{stem}.tally.{old_root[:12]}.json")
    if not archive.exists():
        shutil.copy2(path, archive)


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

    inp_sha256 = str(meta.get("inp_sha256", ""))

    def _persist() -> None:
        _archive_previous(path)
        inputs = store.sources_for_model(inp_sha256) if inp_sha256 else []
        tally["sources"] = inputs
        tally["sources_root"] = merkle.sources_root(inputs)
        _write_json(path, tally)
        store.record_tally(
            tally_id=tally_id,
            session_id=session_id,
            label=label,
            root=tally["root"],
            fact_count=tally["fact_count"],
            path=str(path),
            inp_sha256=inp_sha256,
            sources_root=tally["sources_root"],
            run_fingerprint=fingerprint,
            sources=inputs,
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


# ---------------------------------------------------------------------------
# Provenance: sources and locate
# ---------------------------------------------------------------------------
_MAX_BYTES = 200 * 1024 * 1024
_DOCUMENTS = ("md", "txt", "pdf")
_FILE_ONLY = ("gpkg", "shp")
_EVERYWHERE = (-1e300, -1e300, 1e300, 1e300)
_SPATIAL_MISSING = (
    "[{code}] {feature} needs shapely and pyproj. Install them with: pip install "
    "'openswmm.mcp[ledger]'. register, list, id_match/manual links, lineage and mentions "
    "work without them."
)
_DEGRADED = (
    "Buffer search skipped: shapely and pyproj are not installed (pip install "
    "'openswmm.mcp[ledger]'). Only lineage-linked features and mentions are returned."
)
_BBOX_INDEX = (
    "SQLite has no R*Tree module; the spatial prefilter uses bbox columns (same results, "
    "slower on large layers)."
)


def _refuse(code: str, slug: str, message: str) -> ToolError:
    return ToolError(f"[{code}] {slug}: {message}")


def _invalid(slug: str, message: str) -> ToolError:
    return _refuse(ErrorCode.VALIDATION_ERROR, slug, message)


def _from_ledger(exc: LedgerError) -> ToolError:
    return _invalid(exc.slug, str(exc).removeprefix(f"{exc.slug}: "))


def _from_crs(exc: CrsRefusal, source_id: str | None = None) -> ToolError:
    where = f"source '{source_id}': " if source_id else ""
    return _refuse(getattr(ErrorCode, exc.code), exc.slug, where + exc.message)


def _spatial_available() -> bool:
    """True when shapely and pyproj import (tests monkeypatch this to hide them)."""
    try:
        import pyproj  # noqa: F401
        import shapely  # noqa: F401
    except ImportError:
        return False
    return True


def _require_spatial(feature: str) -> None:
    if _spatial_available():
        return
    require_spatial(feature)  # raises when the modules are really missing
    raise ToolError(_SPATIAL_MISSING.format(code=ErrorCode.DEPENDENCY_MISSING, feature=feature))


def _time(value: str | None, name: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return canonical_time(value)
    except ValueError as exc:
        raise _invalid("bad_time", f"{name}: {exc}") from exc


def _before(stamp: str) -> str:
    moment = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return canonical_time(moment - timedelta(microseconds=1))  # type: ignore[return-value]


def _label(crs: str | None) -> str | None:
    if not crs:
        return None
    try:
        return crs_label(crs)
    except ValueError as exc:
        raise _invalid("bad_value", f"crs: {exc}") from exc


def _missing(name: str, action: str) -> ToolError:
    return _invalid("missing_argument", f"action '{action}' needs '{name}'.")


def _close(word: str, options: list[str]) -> str:
    close = difflib.get_close_matches(word, sorted(set(options)), n=3, cutoff=0.4)
    return f" Close matches: {close}." if close else ""


async def _current_source(store: LedgerStore, source_id: str) -> dict:
    """The source's version valid and known now; refuse unknown or retired ids."""
    if not SOURCE_ID.fullmatch(source_id):
        raise _invalid("bad_value", f"bad source_id '{source_id}'.")
    rows = await asyncio.to_thread(store.sources, None, None, None, valid_only=False)
    mine = [r for r in rows if r["source_id"] == source_id]
    if not mine:
        hint = _close(source_id, [r["source_id"] for r in rows])
        raise _invalid("unknown_source", f"no source '{source_id}'.{hint}")
    now = utc_now()
    valid = [
        r
        for r in mine
        if (r["valid_from"] is None or r["valid_from"] <= now)
        and (r["valid_to"] is None or now < r["valid_to"])
    ]
    if not valid:
        raise _invalid("already_retired", f"source '{source_id}' is not valid now (retired).")
    return valid[-1]


def _source_view(row: dict, now: str) -> dict:
    meta = row.get("meta") or {}
    retired = row["valid_to"] is not None and row["valid_to"] <= now
    return {
        "source_id": row["source_id"],
        "path": row["path"],
        "sha256": row["sha256"],
        "role": row["role"],
        "format": row["format"],
        "crs": row["crs"],
        "agency": row["agency"],
        "acquired": row["acquired"],
        "notes": row["notes"],
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "recorded_at": row["recorded_at"],
        "features": int(meta.get("feature_count", 0)),
        "status": "retired" if retired else "active",
    }


async def _model(ctx: Context, session_id: str) -> tuple[Any, list[dict], dict]:
    session = await get_session_manager(ctx).get(session_id)
    elements, meta = await session.call(model_elements, session, context="reading geometry")
    return session, elements, meta


def _model_sha(meta: dict, session_id: str) -> str:
    sha = meta.get("inp_sha256")
    if not sha:
        raise _invalid(
            "bad_value", f"session '{session_id}' has no model file on disk; save it first."
        )
    return str(sha)


def _id_sets(elements: list[dict], meta: dict) -> dict[str, set[str]]:
    ids: dict[str, set[str]] = {k: set() for k in ELEMENT_KINDS}
    for e in elements + list(meta.get("no_geometry") or []):
        ids[e["kind"]].add(str(e["id"]))
    return ids


def _registered_crs(store: LedgerStore, model_sha: str) -> str | None:
    for row in store.sources(roles=["model"]):
        if row["sha256"] == model_sha and crs_class(row["crs"]) in ("local", "declared"):
            return row["crs"]
    return None


def _model_crs(store: LedgerStore, argument: str | None, meta: dict, model_sha: str):
    registered = _registered_crs(store, model_sha)
    try:
        label, source, warnings = resolve_model_crs(
            argument, registered, meta.get("engine_crs"), meta.get("map_units")
        )
        check_model_crs(label)
    except CrsRefusal as exc:
        raise _from_crs(exc) from exc
    except ValueError as exc:
        raise _invalid("bad_value", f"crs: {exc}") from exc
    return label, source, warnings


def _read_text(path: Path, fmt: str) -> str:
    if fmt != "pdf":
        return path.read_text(encoding="utf-8", errors="replace")
    from pypdf import PdfReader  # checked by the caller

    return "\n\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _default_source_id(path: Path, rows: list[dict]) -> str:
    for row in rows:
        if row["path"] == str(path):
            return row["source_id"]
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", path.stem) or "source"
    if not re.match(r"[A-Za-z0-9_]", base):
        base = "_" + base
    taken = {r["source_id"]: r["path"] for r in rows}
    candidate, n = base, 1
    while candidate in taken and taken[candidate] != str(path):
        n += 1
        candidate = f"{base}-{n}"
    return candidate


async def _register(ctx: Context, store: LedgerStore, a: dict) -> dict:
    sm = get_session_manager(ctx)
    path = resolve_path(a["path"], str(sm.working_dir))
    if not path.is_file():
        raise _invalid("path_not_found", f"no file at {path}.")
    size = (await asyncio.to_thread(os.stat, path)).st_size
    if size > _MAX_BYTES:
        raise _invalid("too_large", f"{path.name} is {size} bytes; the limit is 200 MB.")
    try:
        fmt = await asyncio.to_thread(ingest.detect_format, path)
    except ValueError as exc:
        raise _invalid("unsupported_format", str(exc)) from exc
    if fmt == "pdf":
        try:
            import pypdf  # noqa: F401
        except ImportError as exc:
            raise ToolError(
                f"[{ErrorCode.DEPENDENCY_MISSING}] PDF text needs pypdf. Install it with: "
                "pip install pypdf. Register a .txt or .md copy otherwise."
            ) from exc
    crs = _label(a["crs"])
    acquired = _time(a["acquired"], "acquired")
    rows = await asyncio.to_thread(store.sources, None, None, None, valid_only=False)
    source_id = a["source_id"] or _default_source_id(path, rows)
    if not SOURCE_ID.fullmatch(source_id):
        raise _invalid("bad_value", f"bad source_id '{source_id}'.")

    sha = await asyncio.to_thread(ingest.sha256_file, path)
    warnings: list[str] = []
    meta: dict[str, Any] = {"bytes": size}
    features: list[dict] = []
    valid_from = None
    if fmt == "geojson":
        try:
            features, label, notes = await asyncio.to_thread(ingest.read_geojson, path, crs)
        except ValueError as exc:
            raise _invalid("bad_geojson", f"{path.name}: {exc}") from exc
        warnings += notes
        meta["feature_count"] = len(features)
    elif fmt in _FILE_ONLY:
        label = crs or "unknown"
        warnings.append("feature ingest supports GeoJSON only; registered the file only.")
        meta["feature_count"] = 0
    elif fmt == "inp":
        text = await asyncio.to_thread(path.read_text, encoding="utf-8", errors="replace")
        meta["map_units"] = ingest.map_units(text)
        label = crs or "unknown"
    elif fmt == "csv":
        span = await asyncio.to_thread(ingest.csv_span, path)
        meta.update(span)
        valid_from = span.get("start")  # the first timestamp wins over acquired
        label = "none"
    else:
        label = "none"
    meta["warnings"] = list(warnings)

    ids: dict[str, set[str]] | None = None
    if fmt in _DOCUMENTS:
        sessions = [await sm.get(a["session_id"])] if a["session_id"] else await sm.sessions()
        ids = {k: set() for k in ELEMENT_KINDS}
        for session in sessions:
            if session.solver is None:
                continue
            els, mmeta = await session.call(model_elements, session, context="reading ids")
            for kind, found in _id_sets(els, mmeta).items():
                ids[kind] |= found
        if not any(ids.values()):
            warnings.append(
                "No open model to scan for element ids; register again with session_id "
                "after open_model to record mentions."
            )

    previous = next(
        (r for r in rows if r["source_id"] == source_id and r["valid_to"] is None), None
    )
    try:
        row, status = await asyncio.to_thread(
            lambda: store.register_source(
                source_id=source_id,
                path=str(path),
                sha256=sha,
                role=a["role"],
                format=fmt,
                crs=label,
                agency=a["agency"],
                acquired=acquired,
                notes=a["notes"],
                meta=meta,
                valid_from=valid_from,
            )
        )
    except LedgerError as exc:
        raise _from_ledger(exc) from exc
    if status == "unchanged" and row["crs"] != label:
        warnings.append(
            f"File unchanged; kept its registered CRS {row['crs']} (a new CRS needs a new "
            "version of the file)."
        )
    if status != "unchanged" and features:
        await asyncio.to_thread(
            store.add_features,
            source_id,
            sha,
            [
                {
                    "feature_key": f["feature_key"],
                    "geom": f["geometry"],
                    "bbox": f["bbox"],
                    "props": f["props"],
                }
                for f in features
            ],  # fmt: skip
        )
    mention_count = 0
    if ids and any(ids.values()):
        text = await asyncio.to_thread(_read_text, path, fmt)
        found = ingest.scan_mentions(ingest.chunk_text(text), ids)
        mention_count = len(found)
        if found:
            await asyncio.to_thread(store.add_mentions, source_id, sha, found)
    if status == "updated" and previous is not None and previous["sha256"] != sha:
        start = row["valid_from"] or utc_now()
        old = await asyncio.to_thread(
            lambda: store.edges(source_id=source_id, as_of=_before(start))
        )
        stale = sum(1 for e in old if e["source_sha256"] == previous["sha256"])
        if stale:
            warnings.append(f"{stale} lineage edges refer to the previous version; run link again.")
    source = {
        k: row[k]
        for k in ("source_id", "path", "sha256", "role", "format", "crs", "valid_from",
                  "valid_to", "recorded_at")
    }  # fmt: skip
    source["features"] = int((row.get("meta") or {}).get("feature_count", len(features)))
    source["mentions"] = mention_count
    return {"status": status, "source": source, "warnings": warnings}


async def _link(ctx: Context, store: LedgerStore, a: dict) -> dict:
    for name in ("session_id", "source_id", "rule"):
        if not a[name]:
            raise _missing(name, "link")
    rule, params = a["rule"], dict(a["params"] or {})
    kinds = params.get("kinds")
    if kinds is not None and (
        not isinstance(kinds, list) or any(k not in ELEMENT_KINDS for k in kinds)
    ):
        raise _invalid("bad_value", f"params.kinds must be a list of {list(ELEMENT_KINDS)}.")
    if rule == "id_match" and not params.get("key"):
        raise _missing("params.key", "link")
    if rule == "manual" and not isinstance(params.get("edges"), list):
        raise _missing("params.edges", "link")
    if rule == "nearest":
        md = params.get("max_distance")
        if md is None:
            raise _missing("params.max_distance", "link")
        if isinstance(md, bool) or not isinstance(md, (int, float)) or not md > 0:
            raise _invalid("bad_value", "params.max_distance must be a number > 0.")
    source = await _current_source(store, a["source_id"])
    _, elements, meta = await _model(ctx, a["session_id"])
    model_sha = _model_sha(meta, a["session_id"])
    sid, sha = source["source_id"], source["sha256"]
    features = await asyncio.to_thread(store.features_in_bbox, sid, sha, _EVERYWHERE)
    in_scope = [e for e in elements if not kinds or e["kind"] in kinds]
    warnings: list[str] = []
    distances: dict[tuple[str, str, str], float] = {}
    echo: dict[str, Any]

    if rule == "id_match":
        echo = {"key": params["key"], **({"kinds": kinds} if kinds else {})}
        matched, unmatched = ingest.id_match(elements, features, params["key"], kinds)
        edge_params = echo
    elif rule == "manual":
        echo = {"edges": len(params["edges"])}
        matched, errors = ingest.manual_edges(params["edges"], elements, features)
        unmatched = errors
        if errors:
            warnings.append(f"{len(errors)} manual edges were rejected; see unmatched.")
        edge_params = {}
    else:
        model_label, _, notes = _model_crs(store, params.get("crs"), meta, model_sha)
        warnings += notes
        verdict = crs_verdict(model_label, source["crs"])
        if verdict is not None:
            raise _from_crs(verdict, sid)
        _require_spatial("sources(rule='nearest')")
        from openswmm_mcp.ledger import spatial

        anchor = None
        if in_scope and crs_class(model_label) == "declared":
            c = spatial.shape(in_scope[0]["geometry"]).centroid
            anchor = (c.x, c.y)
        try:
            frame = spatial.resolve_frame(model_label, [source["crs"]], anchor)
            pairs = [(e, spatial.element_geometry(e, frame)) for e in in_scope]
            feats = [
                (f["feature_key"], spatial.source_geometry(f["geom"], source["crs"], frame))
                for f in features
                if f["geom"]
            ]
        except CrsRefusal as exc:
            raise _from_crs(exc, sid) from exc
        except Exception as exc:  # unreadable CRS text, failed transform
            raise _invalid("bad_value", f"CRS {source['crs']} or {model_label}: {exc}") from exc
        found = spatial.nearest(pairs, feats, float(params["max_distance"]))
        echo = {
            "max_distance": float(params["max_distance"]),
            "unit": frame.unit,
            "crs": frame.working_crs,
            **({"kinds": kinds} if kinds else {}),
        }
        edge_params = {k: echo[k] for k in ("max_distance", "unit", "crs")}
        matched = []
        for e, fk, d in found:
            matched.append({"element_kind": e["kind"], "element_id": e["id"], "feature_key": fk})
            distances[(e["kind"], e["id"], fk)] = round(d, 6)
        hit = {(m["element_kind"], m["element_id"]) for m in matched}
        unmatched = [
            {"element_kind": e["kind"], "element_id": e["id"]}
            for e in in_scope
            if (e["kind"], e["id"]) not in hit
        ]

    rows = []
    for m in matched:
        p = dict(edge_params)
        key = (m["element_kind"], m["element_id"], m["feature_key"])
        if key in distances:
            p["distance"] = distances[key]
        rows.append(
            {**m, "model_sha256": model_sha, "source_id": sid, "source_sha256": sha,
             "rule": rule, "params": p}
        )  # fmt: skip
    added, skipped = await asyncio.to_thread(store.add_edges, rows)
    if source["crs"] == "unknown" and rule != "nearest":
        warnings.append(f"Source '{sid}' has an unknown CRS; it is left out of locate.")
    return {
        "source_id": sid,
        "rule": rule,
        "params": echo,
        "model_sha256": model_sha,
        "added": added,
        "skipped_existing": skipped,
        "unmatched": {"count": len(unmatched), "sample": unmatched[:20]},
        "edges": [
            {k: r[k] for k in ("element_kind", "element_id", "feature_key")}
            | ({"distance": r["params"]["distance"]} if "distance" in r["params"] else {})
            for r in rows[:50]
        ],
        "warnings": warnings,
    }


def _lineage_rows(store: LedgerStore, model_sha: str, kind: str | None, eid: str | None):
    return [
        {
            "element_kind": r["edge"]["element_kind"],
            "element_id": r["edge"]["element_id"],
            "source_id": r["edge"]["source_id"],
            "source_sha256": r["edge"]["source_sha256"],
            "feature_key": r["edge"]["feature_key"],
            "rule": r["edge"]["rule"],
            "params": r["edge"]["params"],
            "props": r["feature"]["props"],
            "recorded_at": r["edge"]["recorded_at"],
        }
        for r in store.lineage_features(model_sha, element_kind=kind, element_id=eid)
    ]


def _unknown_element(kind: str, eid: str, elements: list[dict], meta: dict) -> ToolError:
    options = [e["id"] for e in elements if e["kind"] == kind] + [
        e["id"] for e in meta.get("no_geometry") or [] if e["kind"] == kind
    ]
    close = difflib.get_close_matches(eid, options, n=3, cutoff=0.4)
    hint = f" Close matches: {close}." if close else ""
    return _refuse(ErrorCode.ELEMENT_NOT_FOUND, "unknown_element", f"no {kind} '{eid}'.{hint}")


async def sources(
    ctx: Context,
    action: Literal["register", "list", "link", "lineage", "mentions", "retire"],
    path: str | None = None,
    role: Literal[
        "network",
        "nodes",
        "survey",
        "asbuilt",
        "rain",
        "flow",
        "scada",
        "document",
        "model",
        "other",
    ]
    | None = None,  # fmt: skip
    crs: str | None = None,
    agency: str | None = None,
    acquired: str | None = None,
    notes: str | None = None,
    source_id: str | None = None,
    session_id: str | None = None,
    rule: Literal["id_match", "nearest", "manual"] | None = None,
    params: dict | None = None,
    element_kind: Literal["node", "link", "subcatchment", "gage"] | None = None,
    element_id: str | None = None,
    valid_to: str | None = None,
) -> dict:
    """Register the files a model was built from (register; a changed file adds a version), list
    them, link source features to model elements by id_match, nearest or manual rule (link), and
    ask what produced an element (lineage) or which documents mention it (mentions). Rows are
    never overwritten; retire ends a source's validity."""
    store = get_ledger_store(ctx)
    args = dict(locals())
    if action == "register":
        if not path:
            raise _missing("path", action)
        if not role:
            raise _missing("role", action)
        return await _register(ctx, store, args)

    if action == "list":
        now = utc_now()
        rows = await asyncio.to_thread(store.sources, now, now, None, valid_only=False)
        return {
            "sources": [_source_view(r, now) for r in rows],
            "ledger_path": str(store.path),
            "index": store.index,
            "warnings": [_BBOX_INDEX] if store.index == "bbox" else [],
        }

    if action == "link":
        return await _link(ctx, store, args)

    if action == "lineage":
        if element_id or element_kind:
            for name, value in (
                ("element_kind", element_kind),
                ("element_id", element_id),
                ("session_id", session_id),
            ):
                if not value:
                    raise _missing(name, action)
        elif not source_id:
            raise _missing("element_kind+element_id+session_id or source_id", action)
        if source_id and not SOURCE_ID.fullmatch(source_id):
            raise _invalid("bad_value", f"bad source_id '{source_id}'.")
        model_sha = None
        if session_id:
            _, elements, meta = await _model(ctx, session_id)
            model_sha = _model_sha(meta, session_id)
            if element_id and (element_kind, element_id) not in {
                (e["kind"], e["id"]) for e in elements + list(meta.get("no_geometry") or [])
            }:
                raise _unknown_element(element_kind, element_id, elements, meta)

        def _query() -> list[dict]:
            shas = [model_sha] if model_sha else sorted(
                {e["model_sha256"] for e in store.edges(source_id=source_id)}
            )  # fmt: skip
            out = []
            for sha in shas:
                out += _lineage_rows(store, sha, element_kind, element_id)
            return [r for r in out if not source_id or r["source_id"] == source_id]

        edges = await asyncio.to_thread(_query)
        return {"model_sha256": model_sha, "count": len(edges), "edges": edges[:200]}

    if action == "mentions":
        if not element_id and not source_id:
            raise _missing("element_id or source_id", action)
        rows = await asyncio.to_thread(
            lambda: store.mentions(
                element_kind=element_kind, element_id=element_id, source_id=source_id
            )
        )
        return {
            "count": len(rows),
            "mentions": [
                {
                    "source_id": m["source_id"],
                    "chunk_index": m["chunk_index"],
                    "element_kind": m["element_kind"],
                    "element_id": m["element_id"],
                    "span": [m["span_start"], m["span_end"]],
                    "text": m["text"],
                }
                for m in rows[:200]
            ],
        }

    if action == "retire":
        if not source_id:
            raise _missing("source_id", action)
        end = _time(valid_to, "valid_to")
        try:
            done = await asyncio.to_thread(store.retire_source, source_id, end)
        except LedgerError as exc:
            if exc.slug == "unknown_source":
                rows = await asyncio.to_thread(store.sources, None, None, None, valid_only=False)
                hint = _close(source_id, [r["source_id"] for r in rows])
                raise _invalid("unknown_source", f"no source '{source_id}'.{hint}") from exc
            raise _from_ledger(exc) from exc
        return {k: done[k] for k in ("source_id", "valid_to", "recorded_at", "superseded")}

    raise _invalid("bad_value", f"Unknown action '{action}'.")


def _fact_index(row: dict | None) -> dict[tuple[str, str], list[str]]:
    """Fact ids by (kind, entity) from the tally file of a tally row (archive if replaced)."""
    if not row:
        return {}
    path = Path(row["path"])
    candidates = [
        path,
        path.with_name(path.name.replace(".tally.json", f".tally.{row['root'][:12]}.json")),
    ]  # noqa: E501
    for candidate in candidates:
        try:
            doc = _read_json(candidate)
        except (OSError, ValueError):
            continue
        if doc.get("root") != row["root"]:
            continue
        out: dict[tuple[str, str], list[str]] = {}
        for f in doc.get("facts") or []:
            out.setdefault((str(f.get("kind")), str(f.get("entity"))), []).append(f["id"])
        return out
    return {}


async def locate(
    ctx: Context,
    session_id: str,
    element_kind: Literal["node", "link", "subcatchment", "gage"] | None = None,
    element_id: str | None = None,
    point: list[float] | None = None,
    crs: str | None = None,
    buffer: float = 25.0,
    as_of: str | None = None,
    as_known: str | None = None,
    roles: list[str] | None = None,
    top: int = 50,
) -> dict:
    """Everything the ledger knows within buffer of a model element or point: elements, source
    features, lineage and document mentions, as true at as_of and known at as_known. buffer is in
    the model CRS's linear unit (local:<map units> when none is declared; crs declares one).
    Sources in an incompatible CRS are left out and listed, never reprojected by guess."""
    # 1. arguments
    has_element = bool(element_kind or element_id)
    if has_element == (point is not None) or (has_element and not (element_kind and element_id)):
        raise _invalid("bad_anchor", "give either element_kind and element_id, or point=[x, y].")
    if point is not None:
        ok = (
            isinstance(point, list)
            and len(point) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in point)
            and all(math.isfinite(float(v)) for v in point)
        )
        if not ok:
            raise _invalid("bad_anchor", "point must be [x, y], two finite numbers.")
        point = [float(point[0]), float(point[1])]
    if not (isinstance(buffer, (int, float)) and math.isfinite(buffer) and buffer >= 0):
        raise _invalid("bad_value", "buffer must be a finite number >= 0.")
    if top < 1:
        raise _invalid("bad_value", "top must be >= 1.")
    for r in roles or []:
        if r not in ROLES:
            raise _invalid("bad_value", f"unknown role '{r}'; roles are {list(ROLES)}.")
    _label(crs)
    now = utc_now()
    t_of = _time(as_of, "as_of") or now
    t_known = _time(as_known, "as_known") or now
    buffer = float(buffer)

    # 2. session and the live model
    store = get_ledger_store(ctx)
    _, elements, meta = await _model(ctx, session_id)
    model_sha = meta.get("inp_sha256") or ""
    anchor_el = None
    if has_element:
        anchor_el = next(
            (e for e in elements if e["kind"] == element_kind and e["id"] == element_id), None
        )
        if anchor_el is None:
            in_meta = any(
                e["kind"] == element_kind and e["id"] == element_id
                for e in meta.get("no_geometry") or []
            )
            if not in_meta:
                raise _unknown_element(element_kind, element_id, elements, meta)
            anchor_el = {"kind": element_kind, "id": element_id, "geometry": None}

    # 3. CRS (stdlib)
    model_label, crs_source, warnings = await asyncio.to_thread(
        _model_crs, store, crs, meta, model_sha
    )
    in_scope = await asyncio.to_thread(store.sources, t_of, t_known, roles)
    spatial_rows = [r for r in in_scope if r["format"] == "geojson" and r["role"] != "model"]
    docs = [r for r in in_scope if r["format"] in _DOCUMENTS]
    incompatible: list[dict] = []
    usable: list[dict] = []
    for r in spatial_rows:
        verdict = crs_verdict(model_label, r["crs"])
        if verdict is None:
            usable.append(r)
            continue
        incompatible.append(
            {"source_id": r["source_id"], "crs": r["crs"], "slug": verdict.slug,
             "reason": verdict.message}
        )  # fmt: skip
        warnings.append(
            f"Source '{r['source_id']}' ({r['crs']}) was left out ({verdict.slug}): "
            f"{verdict.message}"
        )

    # 4. spatial modules
    spatial_ok = _spatial_available()
    if point is not None:
        _require_spatial("locate with a point")
    if anchor_el is not None and anchor_el["geometry"] is None and spatial_ok:
        spatial_ok = False
        warnings.append(f"{element_kind} '{element_id}' has no geometry; buffer search skipped.")
    elif not spatial_ok:
        warnings.append(_DEGRADED)

    def _gather() -> dict:
        tally_row = store.current_tally(session_id, t_known)
        facts = _fact_index(tally_row)
        lineage = (
            store.lineage_features(
                model_sha, element_kind=element_kind, element_id=element_id,
                as_of=t_of, as_known=t_known,
            )
            if model_sha and anchor_el is not None
            else []
        )  # fmt: skip
        counts = store.excluded_counts(t_of, t_known, roles)
        return {"tally": tally_row, "facts": facts, "lineage": lineage, "counts": counts}

    base = await asyncio.to_thread(_gather)
    usable_ids = {r["source_id"]: r for r in usable}
    working_crs: str | None = model_label if crs_class(model_label) == "local" else None
    unit: str | None = MAP_UNITS[model_label.split(":", 1)[1]] if working_crs else None
    reprojected: list[str] = []
    hits: list[dict] = []  # features
    near: list[tuple[dict, float]] = []  # elements
    outside: int | None = None

    if spatial_ok:
        from openswmm_mcp.ledger import spatial

        geom = anchor_el["geometry"] if anchor_el else {"type": "Point", "coordinates": point}
        xy = None
        if crs_class(model_label) == "declared":
            c = spatial.shape(geom).centroid
            xy = (c.x, c.y)
        try:
            frame = spatial.resolve_frame(model_label, [r["crs"] for r in usable], xy)
        except CrsRefusal as exc:
            raise _from_crs(exc) from exc
        except ValueError as exc:
            raise _invalid("bad_value", f"model CRS {model_label}: {exc}") from exc
        working_crs, unit = frame.working_crs, frame.unit
        anchor = spatial.element_geometry({"geometry": geom}, frame)
        minx, miny, maxx, maxy = anchor.bounds
        box = (minx - buffer, miny - buffer, maxx + buffer, maxy + buffer)

        def _search() -> tuple[list[dict], int, list[str], list[dict]]:
            found, missed, moved, dropped = [], 0, [], []
            for r in usable:
                sid, sha, label = r["source_id"], r["sha256"], r["crs"]
                try:
                    env = spatial.envelope_for(label, frame, box)
                    cands = [
                        (f, spatial.source_geometry(f["geom"], label, frame))
                        for f in store.features_in_bbox(sid, sha, env, t_known, t_of)
                        if f["geom"]
                    ]
                    total = sum(
                        1 for f in store.features_in_bbox(sid, sha, _EVERYWHERE, t_known, t_of)
                    )
                except Exception as exc:  # unreadable CRS text
                    dropped.append({"source_id": sid, "crs": label, "slug": "crs_unknown",
                                    "reason": f"CRS could not be read: {exc}"})  # fmt: skip
                    continue
                if label in frame.reprojected:
                    moved.append(sid)
                got = spatial.within(anchor, cands, buffer)
                seen = set()
                for f, d, rel in got:
                    seen.add(f["feature_key"])
                    found.append(_feature_row(f, r, d, rel))
                for x in base["lineage"]:
                    f = x["feature"]
                    if f["source_id"] != sid or f["source_sha256"] != sha:
                        continue
                    if f["feature_key"] in seen:
                        continue
                    seen.add(f["feature_key"])
                    d = None
                    if f["geom"]:
                        d = float(anchor.distance(spatial.source_geometry(f["geom"], label, frame)))
                    found.append(_feature_row(f, r, d, "lineage"))
                missed += max(0, total - len(seen))
            return found, missed, moved, dropped

        hits, outside, reprojected, dropped = await asyncio.to_thread(_search)
        for d in dropped:
            incompatible.append(d)
            usable_ids.pop(d["source_id"], None)
            warnings.append(f"Source '{d['source_id']}' ({d['crs']}) was left out: {d['reason']}")
        cands = [(e, spatial.element_geometry(e, frame)) for e in elements if e["geometry"]]
        near = [(e, d) for e, d, _ in spatial.within(anchor, cands, buffer)]
        if anchor_el is not None and all(e is not anchor_el for e, _ in near):
            near.insert(0, (anchor_el, 0.0))
    else:
        for x in base["lineage"]:
            f = x["feature"]
            r = usable_ids.get(f["source_id"])
            if r is not None and r["sha256"] == f["source_sha256"]:
                hits.append(_feature_row(f, r, None, "lineage"))
        if anchor_el is not None:
            near = [(anchor_el, 0.0)]

    hits.sort(key=lambda h: (h["distance"] is None, h["distance"] or 0.0, h["source_id"],
                             h["feature_key"]))  # fmt: skip
    truncated = max(0, len(hits) - top) + max(0, len(near) - top)
    hits, near = hits[:top], near[:top]
    doc_ids = {r["source_id"] for r in docs}

    def _details() -> tuple[list[dict], list[dict]]:
        out_elements, out_mentions = [], []
        for e, d in near:
            edges = (
                store.edges(model_sha256=model_sha, element_kind=e["kind"], element_id=e["id"],
                            as_of=t_of, as_known=t_known)
                if model_sha
                else []
            )  # fmt: skip
            out_elements.append(
                {
                    "kind": e["kind"],
                    "id": e["id"],
                    "distance": round(d, 6),
                    "fact_ids": base["facts"].get((e["kind"], e["id"]), []),
                    "lineage": [
                        {k: x[k] for k in ("source_id", "source_sha256", "feature_key", "rule")}
                        for x in edges
                    ],
                }
            )
            for m in store.mentions(element_kind=e["kind"], element_id=e["id"], as_of=t_of,
                                    as_known=t_known):  # fmt: skip
                if m["source_id"] in doc_ids:
                    out_mentions.append(
                        {"source_id": m["source_id"], "chunk_index": m["chunk_index"],
                         "element_kind": m["element_kind"], "element_id": m["element_id"],
                         "span": [m["span_start"], m["span_end"]], "text": m["text"]}
                    )  # fmt: skip
        return out_elements, out_mentions

    out_elements, out_mentions = await asyncio.to_thread(_details)
    truncated += max(0, len(out_mentions) - top)
    out_mentions = out_mentions[:top]
    if store.index == "bbox":
        warnings.append(_BBOX_INDEX)
    tally_row = base["tally"]
    searched = list(usable_ids.values()) + docs
    anchor_view = {"kind": element_kind, "id": element_id} if has_element else {"point": point}
    return {
        "query": {
            "anchor": anchor_view,
            "buffer": buffer,
            "buffer_unit": unit,
            "crs": model_label,
            "working_crs": working_crs,
            "as_of": t_of,
            "as_known": t_known,
        },
        "elements": out_elements,
        "features": hits,
        "mentions": out_mentions,
        "excluded": {
            "outside_buffer": outside,
            "not_yet_valid": base["counts"]["not_yet_valid"],
            "retired": base["counts"]["retired"],
            "truncated": truncated,
            "crs_incompatible": incompatible,
        },
        "warnings": warnings,
        "provenance": {
            "buffer": buffer,
            "buffer_unit": unit,
            "crs": model_label,
            "working_crs": working_crs,
            "crs_source": crs_source,
            "reprojected": reprojected,
            "spatial": spatial_ok,
            "index": store.index,
            "sources": [
                {
                    "id": r["source_id"],
                    "path": r["path"],
                    "sha256": r["sha256"],
                    "crs": r["crs"],
                    "role": r["role"],
                }
                for r in searched
            ],  # fmt: skip
            "model_sha256": model_sha or None,
            "tally_id": tally_row["tally_id"] if tally_row else None,
            "tally_root": tally_row["root"] if tally_row else None,
            "ledger_path": str(store.path),
            "generated_at": utc_now(),
            "as_of": t_of,
            "as_known": t_known,
        },
    }


def _feature_row(f: dict, source: dict, distance: float | None, relation: str) -> dict:
    return {
        "source_id": f["source_id"],
        "feature_key": f["feature_key"],
        "distance": None if distance is None else round(distance, 6),
        "relation": relation,
        "props": f["props"],
        "valid_from": source["valid_from"],
        "valid_to": source["valid_to"],
    }


TOOLS = (tally, sources, locate)
