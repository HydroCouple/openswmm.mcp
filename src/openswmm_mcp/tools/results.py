"""Result tools: ``timeseries``, ``report``, ``compare``, ``export``."""

from __future__ import annotations

import asyncio
import csv
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

import numpy as np
from fastmcp import Context
from openswmm.engine import (
    EngineError,
    OutLinkVar,
    OutNodeVar,
    OutputReader,
    OutSubcatchVar,
    OutSystemVar,
    get_report_snapshot,
)

from openswmm_mcp import catalog as cat
from openswmm_mcp._util.validation import resolve_path
from openswmm_mcp.dependencies import get_session, get_session_manager
from openswmm_mcp.errors import ErrorCode, ToolError, engine_error
from openswmm_mcp.tools.model import summary

_VARS = {
    "node": OutNodeVar,
    "link": OutLinkVar,
    "subcatchment": OutSubcatchVar,
    "system": OutSystemVar,
}
_SERIES = {"node": "node_series", "link": "link_series", "subcatchment": "subcatchment_series"}
_IDS = {"node": "node_ids", "link": "link_ids", "subcatchment": "subcatchment_ids"}
_SNAPSHOT_TABLES = {
    "flooding": ("node_flooding", "total_flood_volume"),
    "capacity": ("link_flow_summary", "max_filling"),
    "storage": ("storage_summary", "max_depth"),
    "pumps": ("pump_summary", "total_volume"),
    "subcatchments": ("subcatchment_summary", "total_runoff_vol"),
}
_METRICS = ("peak", "volume", "rmse", "nse", "bias")
T = TypeVar("T")


# ---------------------------------------------------------------------------
# Output reading
# ---------------------------------------------------------------------------
async def _with_reader(ctx: Context, source: str, fn: Callable[[OutputReader], T]) -> T:
    """Run ``fn(reader)`` in a worker thread on a fresh reader of *source*'s output.

    *source* is a session ID or an ``.out`` path. A session's output is read under
    the session lock, and only once its run has ended.
    """
    sm = get_session_manager(ctx)
    session = await sm.get(source) if await sm.exists(source) else None
    if session is None:
        path = resolve_path(source, str(sm.working_dir))
        if not path.is_file():
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] '{source}' is neither a session nor a file."
            )

    def _run() -> T:
        if session is not None and session.state != "ENDED":
            raise ToolError(
                f"[{ErrorCode.INVALID_STATE}] Session '{source}' has no finished results "
                f"(state {session.state}); run it with run(until='end') first."
            )
        reader = OutputReader(session.out_path if session is not None else str(path))
        try:
            return fn(reader)
        finally:
            reader.close()

    if session is not None:
        return await session.call(_run, context=f"results of {source}")
    try:
        return await asyncio.to_thread(_run)
    except ToolError:
        raise
    except Exception as exc:
        raise engine_error(exc, f"results of {source}") from exc


def _variable(kind: str, variable: str, reader: OutputReader) -> int:
    if kind not in _VARS:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] kind must be one of {sorted(_VARS)}.")
    enum = _VARS[kind]
    name = variable.strip()
    if name.lower().startswith(("quality:", "concentration:")):
        pollutant = name.split(":", 1)[1]
        if pollutant not in reader.pollutant_ids:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown pollutant '{pollutant}'; "
                f"output has {reader.pollutant_ids}."
            )
        return int(enum.POLLUT_BASE) + list(reader.pollutant_ids).index(pollutant)
    try:
        return int(enum[name.upper()])
    except KeyError:
        valid = [m.lower() for m in enum.__members__ if m != "POLLUT_BASE"]
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown {kind} variable '{variable}'. "
            f"Valid: {valid} or 'quality:<pollutant>'."
        ) from None


def _time(text: str) -> np.datetime64:
    try:
        return np.datetime64(datetime.fromisoformat(text.strip()))
    except ValueError:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] '{text}' is not an ISO datetime "
            "(e.g. 2024-06-01T12:00)."
        ) from None


def _window(
    reader: OutputReader, start: str | None, end: str | None
) -> tuple[np.ndarray, int, int]:
    times = np.asarray(reader.period_times)
    lo, hi = 0, len(times) - 1
    if start:
        lo = int(np.searchsorted(times, _time(start), "left"))
    if end:
        hi = int(np.searchsorted(times, _time(end), "right")) - 1
    if lo > hi:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] No reporting periods between {start} and {end}."
        )
    return times, lo, hi


def _series(reader: OutputReader, kind: str, key: str | None, var: int, lo: int, hi: int):
    if kind == "system":
        return np.array([reader.system_result(p, var) for p in range(lo, hi + 1)], dtype=float)
    return np.asarray(getattr(reader, _SERIES[kind])(key, var, start=lo, end=hi), dtype=float)


def _output_series(reader: OutputReader, kind: str, variable: str, keys: list[str] | None):
    """Yield ``(id, (times, values))`` for *keys* (all when None) that the output has."""
    var = _variable(kind, variable, reader)
    times, lo, hi = _window(reader, None, None)
    if kind == "system":
        keys = ["system"] if keys is None or "system" in keys else []
    else:
        have = list(getattr(reader, _IDS[kind]))
        keys = have if keys is None else [k for k in keys if k in set(have)]
    for key in keys:
        values = _series(reader, kind, None if kind == "system" else key, var, lo, hi)
        yield key, (times[lo : hi + 1], values)


def _decimate(
    times: np.ndarray, values: np.ndarray, max_points: int
) -> tuple[np.ndarray, np.ndarray]:
    """Keep each bucket's min and max so peaks survive downsampling."""
    if len(values) <= max_points:
        return times, values
    buckets = np.array_split(np.arange(len(values)), max(1, max_points // 2))
    keep = sorted(
        {int(b[np.argmin(values[b])]) for b in buckets if len(b)}
        | {int(b[np.argmax(values[b])]) for b in buckets if len(b)}
    )
    return times[keep], values[keep]


async def timeseries(
    ctx: Context,
    source: str,
    kind: str,
    ids: list[str],
    variable: str,
    start: str | None = None,
    end: str | None = None,
    max_points: int = 500,
) -> dict:
    """Read result time series from a session (source = session_id) or an .out file path.

    kind is node, link, subcatchment or system (ids ignored). variable is an output variable
    ("depth", "flow", "runoff", "quality:TSS", ...; see describe("output")). Longer series
    are downsampled to max_points, preserving peaks.
    """

    def _run(reader: OutputReader) -> dict:
        var = _variable(kind, variable, reader)
        times, lo, hi = _window(reader, start, end)
        keys = [None] if kind == "system" else ids
        series = {}
        for key in keys:
            values = _series(reader, kind, key, var, lo, hi)
            t, v = _decimate(times[lo : hi + 1], values, max_points)
            series[key or "system"] = {
                "times": [str(x) for x in t],
                "values": cat.to_json(v),
                "peak": cat.to_json(values.max()) if len(values) else None,
            }
        return {
            "source": source,
            "kind": kind,
            "variable": variable,
            "periods": hi - lo + 1,
            "series": series,
        }

    return await _with_reader(ctx, source, _run)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------
def _service_values(solver: Any, target: str) -> dict[str, Any]:
    values = {}
    for name, m in cat.members(target).items():
        if m["form"] != "property" or "NDArray" in m.get("type", ""):
            continue
        try:
            values[name] = cat.to_json(getattr(cat.resolve(solver, target, None), name), limit=50)
        except Exception as exc:
            values[name] = f"unavailable: {type(exc).__name__}"
    return values


def _has_surface(solver: Any) -> bool:
    try:
        return bool(solver.surface2d.n_cells)
    except EngineError:
        return False  # built without 2D, or the model has no mesh


async def report(
    ctx: Context,
    session_id: str,
    name: Literal[
        "summary",
        "mass_balance",
        "flooding",
        "capacity",
        "storage",
        "pumps",
        "subcatchments",
        "statistics",
        "quality",
        "2d",
        "groundwater",
    ],
    kind: str | None = None,
    ids: list[str] | None = None,
    top: int = 20,
) -> dict:
    """Curated result summaries: model summary; continuity errors, volume totals and routing
    diagnostics (mass_balance); ranked flooding, capacity, storage, pump and subcatchment
    tables; per-object statistics for kind/ids; pollutant continuity (quality); and 2D and
    groundwater state. Tables are sorted by severity and cut to top rows."""
    session = await get_session(ctx, session_id)
    solver = session.require_solver()

    def _run() -> dict:
        if name == "summary":
            return summary(session)
        if name == "statistics":
            if not kind:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] statistics needs kind "
                    "(node, link, subcatchment)."
                )
            target = f"{kind}.stats"
            fields = sorted(cat.members(target))
            keys = (
                ids
                or [
                    str(i)
                    for i in cat.resolve(solver, cat.require_kind(kind)["collection"], None).ids
                ][:top]
            )
            rows = []
            for key in keys:
                obj = cat.resolve(solver, target, key)
                rows.append({"id": key} | {f: cat.to_json(getattr(obj, f)) for f in fields})
            return {"name": name, "kind": kind, "rows": rows}
        if name in ("2d", "groundwater"):
            target = "surface2d" if name == "2d" else "surface2d.groundwater"
            if not _has_surface(solver):
                return {"name": name, "active": False, "note": "The model has no 2D surface."}
            return {"name": name, "active": True, "values": _service_values(solver, target)}
        snapshot = get_report_snapshot(solver)
        if name == "mass_balance":
            return {
                "name": name,
                "continuity_note": "continuity_error_pct is a percentage",
                **{
                    k: cat.to_json(getattr(snapshot, k))
                    for k in (
                        "routing_diagnostics",
                        "runoff_continuity",
                        "routing_continuity",
                        "quality_continuity",
                    )
                },
            }
        if name == "quality":
            return {
                "name": name,
                "pollutants": [p.id for p in solver.pollutants],
                "quality_continuity": cat.to_json(snapshot.quality_continuity),
            }
        table, key = _SNAPSHOT_TABLES[name]
        rows = [cat.to_json(r) for r in getattr(snapshot, table)]
        rows.sort(key=lambda r: -(r.get(key) or 0) if isinstance(r, dict) else 0)
        return {"name": name, "total": len(rows), "rows": rows[:top], "sorted_by": key}

    return await session.call(_run, context=f"report {name}")


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------
def _observed_csv(path: Path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Wide CSV: first column ISO datetimes, one column per element id."""
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        header, body = rows[0], [r for r in rows[1:] if r]
        times = np.array([np.datetime64(datetime.fromisoformat(r[0].strip())) for r in body])
        return {
            name.strip(): (
                times,
                np.array([float(r[i]) if i < len(r) and r[i].strip() else np.nan for r in body]),
            )
            for i, name in enumerate(header[1:], start=1)
        }
    except (OSError, IndexError, ValueError) as exc:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Cannot read observed CSV '{path}': {exc}. Expected "
            "a header row, ISO datetimes in the first column and one numeric column per id."
        ) from None


def _metrics(ta, a, tb, b) -> dict[str, float | None]:
    """Metrics of b against a (a = baseline / observed), with b interpolated onto a's times."""
    xa, xb = ta.astype("datetime64[s]").astype(float), tb.astype("datetime64[s]").astype(float)
    ok = ~np.isnan(a)
    bi = np.interp(xa[ok], xb, b)
    av = a[ok]
    err = bi - av
    denom = float(np.sum((av - av.mean()) ** 2)) if len(av) else 0.0
    dt = np.diff(xa[ok]) if len(av) > 1 else np.array([0.0])

    def vol(v: np.ndarray) -> float:
        return float(np.sum((v[1:] + v[:-1]) / 2 * dt)) if len(v) > 1 else 0.0

    return {
        "peak_a": float(av.max()) if len(av) else None,
        "peak_b": float(bi.max()) if len(bi) else None,
        "peak_diff": float(bi.max() - av.max()) if len(av) else None,
        "volume_a": vol(av),
        "volume_b": vol(bi),
        "rmse": float(np.sqrt(np.mean(err**2))) if len(err) else None,
        "nse": 1.0 - float(np.sum(err**2)) / denom if denom > 0 else None,
        "bias": float(np.mean(err)) if len(err) else None,
    }


async def compare(
    ctx: Context,
    a: str,
    b: str,
    kind: str,
    variable: str,
    ids: list[str] | None = None,
    metrics: list[str] | None = None,
) -> dict:
    """Compare two result sources: session IDs, .out paths, or observed data (a wide .csv with
    an ISO datetime column then one column per element id). Returns per-object differences
    and fit metrics of b against a (peak, volume, RMSE, NSE, bias)."""
    wanted = set(metrics or _METRICS)
    base = str(get_session_manager(ctx).working_dir)

    async def _read(source: str, keys: list[str] | None, consume: Callable[[Any], T]) -> T:
        """Feed ``(id, (times, values))`` pairs of *source* to *consume* where they are readable."""
        if source.lower().endswith(".csv"):
            data = await asyncio.to_thread(_observed_csv, resolve_path(source, base))
            pairs = data.items() if keys is None else ((k, data[k]) for k in keys if k in data)
            return consume(iter(pairs))
        return await _with_reader(
            ctx, source, lambda reader: consume(_output_series(reader, kind, variable, keys))
        )

    series_a = await _read(a, ids, dict)

    def _score(pairs: Any) -> list[dict[str, Any]]:
        rows = []
        for key, (tb, vb) in pairs:
            ta, va = series_a[key]
            m = _metrics(ta, va, tb, vb)
            rows.append(
                {"id": key}
                | {k: v for k, v in m.items() if k.split("_")[0] in wanted or k in wanted}
            )
        return rows

    rows = await _read(b, list(series_a), _score)
    rows.sort(key=lambda r: -abs(r.get("peak_diff") or 0))
    scored = {r["id"] for r in rows}
    missing = [k for k in (ids or list(series_a)) if k not in scored]
    out: dict[str, Any] = {
        "a": a,
        "b": b,
        "kind": kind,
        "variable": variable,
        "compared": len(rows),
        "rows": rows[:200],
    }
    if missing:
        out["missing"] = missing[:50]
    return out


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
async def export(
    ctx: Context,
    session_id: str,
    path: str,
    kind: str,
    fields: list[str] | None = None,
    variable: str | None = None,
    format: Literal["csv", "json"] | None = None,
) -> dict:
    """Write a table of object fields, or result time series for variable, to a file."""
    from openswmm_mcp.tools.access import get

    sm = get_session_manager(ctx)
    dest = resolve_path(path, str(sm.working_dir))
    fmt = format or ("json" if dest.suffix.lower() == ".json" else "csv")
    if bool(fields) == bool(variable):
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Pass either fields or variable.")
    if fields:
        table = await get(ctx, session_id, kind, fields, limit=10**9)
        if "values" in table:  # a service: one row
            header = list(fields)
            rows = [[table["values"].get(f) for f in fields]]
        else:
            header = ["id", *fields]
            rows = [
                [i, *(table["columns"][f][n] for f in fields)] for n, i in enumerate(table["ids"])
            ]
    else:

        def _table(reader: OutputReader) -> tuple[list[str], list[list[Any]]]:
            pairs = list(_output_series(reader, kind, variable, None))
            times = pairs[0][1][0] if pairs else []
            rows = [
                [str(t), *(cat.to_json(v[n]) for _, (_, v) in pairs)] for n, t in enumerate(times)
            ]
            return ["time", *(k for k, _ in pairs)], rows

        header, rows = await _with_reader(ctx, session_id, _table)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        dest.write_text(json.dumps([dict(zip(header, r)) for r in rows]), encoding="utf-8")
    else:
        with dest.open("w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows([header, *rows])
    return {
        "session_id": session_id,
        "path": str(dest),
        "format": fmt,
        "rows": len(rows),
        "columns": header[:50],
    }
