"""Model and session tools: ``open_model``, ``run``, ``session``, ``save``, ``edit``."""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastmcp import Context
from openswmm.engine import (
    GEOPACKAGE_PLUGIN_ID,
    EngineState,
    HotStart,
    InpProfile,
    Solver,
)

from openswmm_mcp import catalog as cat
from openswmm_mcp._util.validation import resolve_path
from openswmm_mcp.dependencies import get_session, get_session_manager
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

HDF5_PLUGIN_ID = "org.hydrocouple.openswmm.plugins.hdf5"
_ACTIVE = (EngineState.STARTED, EngineState.RUNNING)
_SUMMARY_OPTIONS = (
    "FLOW_UNITS",
    "FLOW_ROUTING",
    "INFILTRATION",
    "ROUTING_STEP",
    "REPORT_STEP",
    "LINK_OFFSETS",
)
_NEW_MODEL = """[TITLE]
New model created by openswmm.mcp

[OPTIONS]
FLOW_UNITS           CFS
FLOW_ROUTING         DYNWAVE
START_DATE           01/01/2026
START_TIME           00:00:00
END_DATE             01/01/2026
END_TIME             06:00:00
"""
# ModelEditor method stems per element kind (delete_<stem>, analyze_<stem>_impact).
_EDITOR_STEM = {"subcatchment": "subcatch"}


# ---------------------------------------------------------------------------
# Helpers shared with the other tool modules
# ---------------------------------------------------------------------------
def summary(session: SimSession) -> dict[str, Any]:
    """Counts, key options, time window and file paths for a session (worker thread)."""
    solver = session.require_solver()
    counts = {}
    for kind, entry in cat.element_kinds().items():
        if "." in entry["collection"]:
            continue
        try:
            counts[kind] = len(cat.resolve(solver, entry["collection"], None))
        except Exception:
            pass
    options = {}
    for key in _SUMMARY_OPTIONS:
        try:
            options[key] = solver.options[key]
        except Exception:
            pass
    out = {
        "session_id": session.session_id,
        "state": session.state,
        "counts": {k: v for k, v in counts.items() if v},
        "options": options,
        "unit_system": solver.unit_system,
        "start": cat.to_json(solver.start_datetime),
        "end": cat.to_json(solver.end_datetime),
        "files": {"inp": session.inp_path, "rpt": session.rpt_path, "out": session.out_path},
    }
    if int(solver.state) in _ACTIVE:
        out["current_time"] = cat.to_json(solver.current_datetime)
    return out


def _new_solver(inp: str, rpt: str, out: str, lenient: bool) -> Solver:
    """Open a solver on *inp*; on failure destroy it and raise with its parse errors."""
    solver = Solver(inp, rpt, out)
    if lenient:
        solver.set_lenient_open(True)
    try:
        solver.open()
    except Exception:
        errors = []
        try:
            errors = list(solver.open_errors)
        except Exception:
            pass
        solver.destroy()
        if errors:
            raise ToolError(
                f"[{ErrorCode.ENGINE_ERROR}] Could not open {inp}: " + "; ".join(errors[:20])
            ) from None
        raise
    return solver


def _open_solver(session: SimSession, lenient: bool) -> dict[str, Any]:
    session.solver = _new_solver(session.inp_path, session.rpt_path, session.out_path, lenient)
    session.structure_edited = False
    out = summary(session)
    for name in ("open_errors", "open_warnings"):
        messages = list(getattr(session.solver, name, []) or [])
        if messages:
            out[name.split("_")[1]] = messages[:50]
    return out


def _write_model(session: SimSession, dest: Path) -> str:
    """Write the session's current model, every edit included, to *dest*."""
    session.require_solver().write(str(dest))
    return str(dest)


def _reopen(session: SimSession) -> dict[str, Any]:
    """Reopen the session on its current model (edits included) at the simulation start.

    The written model is test-opened first, so a model the engine refuses leaves
    the session exactly as it was.
    """
    stem = Path(session.inp_path).stem.removesuffix("_edited")
    edited = _write_model(session, session.working_dir / f"{stem}_edited.inp")
    probe = _new_solver(
        edited,
        str(session.working_dir / "_check.rpt"),
        str(session.working_dir / "_check.out"),
        lenient=False,
    )
    probe.close()
    probe.destroy()
    session.cleanup()
    session.inp_path = edited
    return _open_solver(session, lenient=False)


def _target_time(solver: Solver, until: str) -> datetime | None:
    if until == "end":
        return None
    m = re.fullmatch(r"\+\s*([\d.]+)\s*([smhd]?)", until.strip())
    if m:
        seconds = float(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return solver.current_datetime + timedelta(seconds=seconds)
    try:
        return datetime.fromisoformat(until)
    except ValueError:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] until must be 'end', an ISO datetime "
            f"or '+<n>[s|m|h|d]', got '{until}'."
        ) from None


def _start(solver: Solver) -> None:
    if int(solver.state) == EngineState.OPENED:
        solver.initialize()
    solver.start()


def _continuity(solver: Solver) -> dict[str, Any]:
    mb = solver.mass_balance
    out = {"runoff": mb.runoff_continuity_error, "routing": mb.routing_continuity_error}
    quality = {}
    for i, pollutant in enumerate(solver.pollutants):
        try:
            quality[pollutant.id] = mb.quality_continuity_error(i)
        except Exception:
            pass
    if quality:
        out["quality"] = quality
    return {k: cat.to_json(v) for k, v in out.items()}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
async def open_model(
    ctx: Context, path: str | None = None, session_id: str = "default", lenient: bool = False
) -> dict:
    """Open a SWMM .inp model into a session, or start an empty model when path is omitted.

    Returns object counts, key options, the simulation window, file paths and any parse
    warnings/errors. lenient=True keeps loading past recoverable errors so they can be
    listed and fixed with `set`/`edit`. Report and output files go to the session's
    working folder. An empty model starts with default options; change them with
    call(session_id, "options", "set_item", {"key": "FLOW_UNITS", "value": "CMS"}).
    """
    sm = get_session_manager(ctx)
    folder = sm.session_dir(session_id)
    if await sm.exists(session_id):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Session '{session_id}' already exists; "
            "close it or choose another session_id."
        )
    if path is None:
        inp = folder / "model.inp"
        inp.write_text(_NEW_MODEL, encoding="utf-8")
    else:
        inp = resolve_path(path, str(sm.working_dir))
        if not inp.is_file():
            raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Model file not found: {inp}")
        if inp.suffix.lower() != ".inp":
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] open_model reads .inp files; "
                "GeoPackage result databases are read with call(session, "
                "'geopackage', ...)."
            )
    session = SimSession(
        session_id,
        folder,
        inp_path=str(inp),
        rpt_path=str(folder / f"{inp.stem}.rpt"),
        out_path=str(folder / f"{inp.stem}.out"),
    )
    await sm.add(session)
    try:
        return await session.call(_open_solver, session, lenient, context="open_model")
    except Exception:
        await sm.remove(session_id)
        raise


async def run(
    ctx: Context, session_id: str = "default", until: str = "end", max_steps: int | None = None
) -> dict:
    """Advance the simulation, starting the engine if needed.

    until: "end" (run to completion and write the report), an ISO datetime, or
    "+<n>[s|m|h|d]" of simulated time. max_steps caps routing steps for step-wise
    control. Returns current time, progress, state and, when finished, continuity errors.
    """
    session = await get_session(ctx, session_id)
    solver = session.require_solver()
    state = await session.call(lambda: int(solver.state))
    if state == EngineState.ENDED:
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] The simulation has finished; use "
            "session(action='reset') to run it again."
        )
    if state == EngineState.OPENED and session.structure_edited:
        await session.call(_reopen, session, context="rebuilding the edited model")
        solver = session.require_solver()
    if state in (EngineState.OPENED, EngineState.INITIALIZED):
        await session.call(_start, solver, context="starting")
    target = await session.call(_target_time, solver, until)
    start, end = await session.call(lambda: (solver.start_datetime, solver.end_datetime))
    total = max((end - start).total_seconds(), 1.0)
    wall, steps = time.monotonic(), 0

    def chunk() -> int:
        n = 0
        limit = 500 if max_steps is None else min(500, max_steps - steps)
        while n < limit and int(solver.state) in _ACTIVE:
            if target is not None and solver.current_datetime >= target:
                break
            solver.step()
            n += 1
        return n

    while True:
        done = await session.call(chunk, context="stepping")
        steps += done
        now_state = await session.call(lambda: int(solver.state))
        if now_state not in _ACTIVE or done == 0 or (max_steps is not None and steps >= max_steps):
            break
        now = await session.call(lambda: solver.current_datetime)
        await ctx.report_progress(min((now - start).total_seconds() / total, 0.99), 1.0)
        if target is not None and now >= target:
            break

    finished = await session.call(lambda: int(solver.state)) not in _ACTIVE
    out: dict[str, Any] = {
        "session_id": session_id,
        "steps": steps,
        "wall_seconds": round(time.monotonic() - wall, 3),
    }
    if finished:
        await session.call(lambda: (solver.end(), solver.report()), context="finishing")
        await ctx.report_progress(1.0, 1.0)
        out.update(
            finished=True,
            continuity_errors=await session.call(_continuity, solver),
            continuity_note="fractions: 0.001 means 0.1 %",
            report_file=session.rpt_path,
            output_file=session.out_path,
        )
    else:
        now = await session.call(lambda: solver.current_datetime)
        out.update(
            finished=False,
            current_time=now.isoformat(),
            progress=round(min((now - start).total_seconds() / total, 1.0), 4),
        )
    out["state"] = session.state
    return out


async def session(
    ctx: Context,
    action: Literal["list", "state", "close", "clone", "reset"],
    session_id: str = "default",
    new_session_id: str | None = None,
) -> dict:
    """Manage sessions: list all, report one session's state, close it, clone it into
    new_session_id (its current model and, mid-run, its time and hydraulic state), or
    reset it: reopen its current model, edits included, at the simulation start."""
    sm = get_session_manager(ctx)
    if action == "list":
        return {
            "sessions": [
                {"session_id": s.session_id, "state": s.state, "inp": s.inp_path}
                for s in await sm.sessions()
            ]
        }
    if action == "close":
        await sm.close(session_id)
        return {"session_id": session_id, "closed": True}
    source = await sm.get(session_id)
    if action == "state":
        return await source.call(summary, source)
    if action == "reset":
        return await source.call(_reopen, source, context="reset")
    if not new_session_id:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] clone needs new_session_id.")
    if await sm.exists(new_session_id):  # checked before its folder is written to
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Session '{new_session_id}' exists.")
    folder = sm.session_dir(new_session_id)
    stem = Path(source.inp_path).stem.removesuffix("_edited")
    hotstart, model = folder / "clone.hsf", folder / f"{stem}.inp"

    def _snapshot() -> datetime | None:
        solver = source.require_solver()
        _write_model(source, model)
        if int(solver.state) not in _ACTIVE:
            return None
        HotStart.save_from(solver, str(hotstart))
        return solver.current_datetime

    now = await source.call(_snapshot, context="snapshotting the source")
    clone = SimSession(
        new_session_id,
        folder,
        inp_path=str(model),
        rpt_path=str(folder / f"{stem}.rpt"),
        out_path=str(folder / f"{stem}.out"),
    )
    await sm.add(clone)

    def _apply() -> dict:
        out = _open_solver(clone, lenient=False)
        if now is not None:
            # Continue from the source's current time and hydraulic state.
            clone.solver.start_datetime = now
            clone.solver.initialize()
            with HotStart.open(str(hotstart)) as state:
                state.apply(clone.solver)
        out.update(state=clone.state, start=cat.to_json(clone.solver.start_datetime))
        return out

    try:
        return await clone.call(_apply, context="cloning")
    except Exception:
        await sm.remove(new_session_id)
        clone.cleanup()
        raise


async def save(
    ctx: Context,
    session_id: str,
    path: str,
    format: Literal["inp", "gpkg", "hdf5", "hotstart", "rpt"] | None = None,
    profile: str | None = None,
) -> dict:
    """Write the session's model or state to path. format defaults from the extension;
    hotstart saves current state; rpt copies the text report; profile selects an .inp
    dialect for compatibility writers ("SWMM5" or "SWMM5_STOCK")."""
    sm = get_session_manager(ctx)
    session = await sm.get(session_id)
    solver = session.require_solver()
    dest = resolve_path(path, str(sm.working_dir))
    fmt = format or {
        ".inp": "inp",
        ".gpkg": "gpkg",
        ".h5": "hdf5",
        ".hdf5": "hdf5",
        ".hsf": "hotstart",
        ".hot": "hotstart",
        ".rpt": "rpt",
    }.get(dest.suffix.lower())
    if fmt is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Cannot infer the format of '{dest.name}'; pass format."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)

    def _write() -> None:
        if fmt == "inp" and profile:
            solver.write_compat(str(dest), cat.coerce(profile, "InpProfile"))
        elif fmt == "inp":
            solver.write(str(dest))
        elif fmt == "gpkg":
            solver.write_with_plugin(str(dest), GEOPACKAGE_PLUGIN_ID)
        elif fmt == "hdf5":
            solver.write_with_plugin(str(dest), HDF5_PLUGIN_ID)
        elif fmt == "hotstart":
            HotStart.save_from(solver, str(dest))
        else:
            dest.write_bytes(Path(session.rpt_path).read_bytes())

    await session.call(_write, context=f"saving {fmt}")
    return {
        "session_id": session_id,
        "path": str(dest),
        "format": fmt,
        "profiles": [p.name for p in InpProfile] if fmt == "inp" else None,
    }


async def edit(
    ctx: Context,
    session_id: str,
    action: Literal["add", "delete", "preview_delete", "rename", "convert"],
    kind: str,
    ids: list[str],
    type: str | None = None,
    new_id: str | None = None,
    properties: dict[str, Any] | None = None,
) -> dict:
    """Change model structure: add objects (type = subtype, properties = initial field values;
    links take from_node/to_node); delete them, cascading or nullifying references;
    preview_delete reports impact without changing anything; rename an ID; convert a
    node/link subtype in place."""
    session = await get_session(ctx, session_id)
    solver = session.require_solver()
    entry = cat.require_kind(kind)
    stem = _EDITOR_STEM.get(kind, kind)

    def _do() -> dict:
        editor = solver.editor
        collection = cat.resolve(solver, entry["collection"], None)
        results: list[Any] = []
        if action == "add":
            add = cat.members(entry["collection"]).get("add")
            if add is None:
                raise ToolError(
                    f"[{ErrorCode.NOT_SUPPORTED}] '{kind}' has no add(); see "
                    f"describe('{entry['collection']}') for its methods."
                )
            _check_properties(kind, properties or {})
            session.structure_edited = True  # before mutating: a later failure still rebuilds
            for key in ids:
                args = [key]
                if len(add["params"]) > 1 and (type or add["params"][1]["required"]):
                    args.append(cat.coerce(type, add["params"][1]["type"]))
                collection.add(*args)
                results.append(_apply_properties(solver, kind, key, properties or {}))
        elif action in ("delete", "preview_delete"):
            name = f"delete_{stem}" if action == "delete" else f"analyze_{stem}_impact"
            if not hasattr(editor, name):
                raise ToolError(f"[{ErrorCode.NOT_SUPPORTED}] Cannot {action} '{kind}' objects.")
            if action == "delete":
                session.structure_edited = True
            for key in ids:
                results.append({"id": key, "impact": cat.to_json(getattr(editor, name)(key))})
        elif action == "rename":
            if len(ids) != 1 or not new_id:
                raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] rename takes one id and new_id.")
            collection.rename(ids[0], new_id)
            results.append({"id": ids[0], "new_id": new_id})
        else:
            convert = getattr(editor, f"convert_{kind}", None)
            if convert is None or type is None:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] convert needs a node or link "
                    "kind and the new type."
                )
            subtype = cat.coerce(type, "NodeType" if kind == "node" else "LinkType")
            session.structure_edited = True
            for key in ids:
                results.append({"id": key, "result": cat.to_json(convert(key, int(subtype)))})
        return {"session_id": session_id, "action": action, "kind": kind, "results": results}

    return await session.call(_do, context=f"edit {action}")


def _check_properties(kind: str, properties: dict[str, Any]) -> None:
    """Refuse bad initial properties before anything is added."""
    if kind == "link" and not {"from_node", "to_node"} <= set(properties):
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] A new link needs properties 'from_node' and 'to_node'."
        )
    for name in set(properties) - {"from_node", "to_node"}:
        if cat.field(kind, name)["access"] != "rw":
            raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] '{kind}.{name}' is read-only.")


def _apply_properties(solver: Solver, kind: str, key: str, properties: dict[str, Any]) -> dict:
    element = cat.resolve(solver, kind, key)
    props = dict(properties)
    if "from_node" in props or "to_node" in props:
        element.set_nodes(props.pop("from_node"), props.pop("to_node"))
    applied = {}
    for name, value in props.items():
        member = cat.field(kind, name)
        owner = cat.resolve(solver, member["target"], key)
        setattr(owner, member["name"], cat.coerce(value, member["type"]))
        applied[name] = value
    return {"id": key, "set": applied}
