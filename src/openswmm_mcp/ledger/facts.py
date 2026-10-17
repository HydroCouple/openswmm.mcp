"""Extract the committed facts of a finished run into ledger fact dicts."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import openswmm
from openswmm.engine import get_report_snapshot

from openswmm_mcp import catalog as cat
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.ledger.schema import FACT_SCHEMA, SCHEMA_VERSION, bin_value, fact_id
from openswmm_mcp.session import SimSession
from openswmm_mcp.tools.access import _units_context

__all__ = ["extract_facts"]

_ELEMENT_KINDS = ("node", "link", "subcatchment")
_FIXED_UNITS = {"%": "%", "s": "s", "h": "h", "count": ""}
_DIMENSIONLESS = {"fraction", "none", ""}
# Snapshot section and field for every system / pollutant fact.
_SNAPSHOT_SRC = {
    "runoff_continuity_error_pct": ("runoff_continuity", "continuity_error_pct"),
    "routing_continuity_error_pct": ("routing_continuity", "continuity_error_pct"),
    "pct_not_converged": ("routing_diagnostics", "pct_not_converged"),
    "n_steps_not_converged": ("routing_diagnostics", "n_steps_not_converged"),
    "min_time_step": ("routing_diagnostics", "min_time_step"),
    "max_time_step": ("routing_diagnostics", "max_time_step"),
    "total_flooding": ("routing_continuity", "total_flooding"),
    "total_outflow": ("routing_continuity", "total_outflow"),
}
_VOLUME_FACTS = ("total_flooding", "total_outflow")
# .rpt "Flow Routing Continuity" first-column header and its size in model volume units.
_RPT_VOLUME = {"US": ("acre-feet", 43560.0), "SI": ("hectare-m", 10000.0)}
_MIN_CONFIRM_VOLUME = 1000.0
_RPT_INFLOW_LINES = (
    "Dry Weather Inflow",
    "Wet Weather Inflow",
    "Groundwater Inflow",
    "RDII Inflow",
    "External Inflow",
)
_SNAPSHOT_INFLOW = (
    "dry_weather_inflow",
    "wet_weather_inflow",
    "groundwater_inflow",
    "rdii_inflow",
    "external_inflow",
)


def _unit(unit_kind: str, catalog_kind: str | None, units: tuple) -> str:
    """The unit string of a fact; ``""`` when dimensionless."""
    if unit_kind in _FIXED_UNITS:
        return _FIXED_UNITS[unit_kind]
    label = cat.unit_label(catalog_kind if unit_kind == "catalog" else unit_kind, *units)
    if label is None or label in _DIMENSIONLESS:
        return ""
    return str(label)


def _routing_block(rpt_path: str) -> tuple[str, dict[str, float]] | None:
    """The first-column unit header and values of the .rpt "Flow Routing Continuity" block."""
    try:
        lines = Path(rpt_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for i, line in enumerate(lines):
        if "Flow Routing Continuity" not in line:
            continue
        header = line.replace("Flow Routing Continuity", "").split()
        if not header:
            return None
        values: dict[str, float] = {}
        for row in lines[i + 2 : i + 40]:
            m = re.match(r"\s*(.+?)\s*\.{2,}\s*(-?\d+\.?\d*)", row)
            if m:
                values[m.group(1)] = float(m.group(2))
            elif not row.strip():
                break
        return header[0], values
    return None


def _volume_confirmed(session: SimSession, routing: dict, system: str | None) -> str | None:
    """None when the snapshot volumes are confirmed to be in model units, else the reason."""
    if system not in _RPT_VOLUME:
        return f"unit system {system!r} has no known .rpt volume unit"
    block = _routing_block(session.rpt_path)
    if block is None:
        return "no 'Flow Routing Continuity' block in the .rpt"
    header, values = block
    label, factor = _RPT_VOLUME[system]
    if header != label:
        return f".rpt volume unit {header!r} is not the expected {label!r}"
    rpt_total = sum(values.get(k, 0.0) for k in _RPT_INFLOW_LINES)
    snap_total = sum(float(routing.get(k) or 0.0) for k in _SNAPSHOT_INFLOW)
    # The threshold is in snapshot units: below it the .rpt's three decimals cannot discriminate.
    if snap_total < _MIN_CONFIRM_VOLUME:
        return f"inflow volume {snap_total:g} is too small to confirm the snapshot volume unit"
    # The .rpt prints three decimals per line, so allow 0.0005 per line plus 0.5 %.
    if abs(snap_total / factor - rpt_total) > 0.0025 + 0.005 * rpt_total:
        return (
            f"snapshot inflow {snap_total:g} does not match the .rpt inflow {rpt_total:g} "
            f"{label}; the snapshot volumes are not in the model's {system} volume unit"
        )
    return None


def _scalar(value: Any) -> Any:
    value = cat.to_json(value)
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _fact(kind: str, entity: str, attr: str, raw: Any, unit: str) -> dict | None:
    entry = FACT_SCHEMA[f"{kind}.{attr}"]
    value = bin_value(raw, entry["policy"]) if raw is not None else None
    if value is None:
        return None
    return {
        "id": fact_id(kind, entity, attr),
        "scope": entry["scope"],
        "entity": entity,
        "kind": kind,
        "attr": attr,
        "value": value,
        "unit": unit,
        "time": None,
    }


def _option(solver: Any, key: str) -> Any:
    return cat.resolve(solver, "options", None)[key]


def _sha256(path: str) -> str | None:
    """Hex digest of the model file, or None when the session has no file on disk."""
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None
    except OSError:
        return None


def _meta(session: SimSession, solver: Any, units: tuple, dropped: list) -> dict:
    meta: dict[str, Any] = {
        "session_id": session.session_id,
        "inp_path": session.inp_path,
        "inp_sha256": _sha256(session.inp_path),
        "engine_version": getattr(openswmm, "__version__", "unknown"),
        "unit_system": units[0],
        "flow_units": units[1],
        "committed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "dropped": dropped,
    }
    unavailable: list[str] = []
    options = cat.resolve(solver, "options", None)
    readers = {
        "routing_model": lambda: str(_option(solver, "FLOW_ROUTING")),
        "start": lambda: cat.to_json(options.start_datetime),
        "end": lambda: cat.to_json(options.end_datetime),
        # REPORT_STEP is stored as a seconds string, e.g. "300.000000".
        "report_step": lambda: float(_option(solver, "REPORT_STEP")),
    }
    for name, read in readers.items():
        try:
            value = read()
        except Exception:
            value = None
        if value is None:
            unavailable.append(name)
        else:
            meta[name] = value
    meta["unavailable"] = unavailable
    return meta


def extract_facts(session: SimSession) -> tuple[list[dict], dict]:
    """Read the committed facts of a finished run.

    Run as ``await session.call(extract_facts, session)``.

    Parameters
    ----------
    session : SimSession
        A session whose run has ended.

    Returns
    -------
    tuple[list[dict], dict]
        Facts sorted by id (binned values, ``time`` always ``None``) and the metadata
        of the extraction (``dropped`` and ``unavailable`` list what was left out).

    Raises
    ------
    ToolError
        ``[INVALID_STATE]`` unless the session state is ``ENDED``.
    """
    if session.state != "ENDED":
        raise ToolError(
            f"[{ErrorCode.INVALID_STATE}] Session '{session.session_id}' has no finished "
            f"results (state {session.state}); run it with run(until='end') first."
        )
    solver = session.require_solver()
    units = _units_context(solver)
    facts: list[dict] = []
    dropped: list[dict] = []
    try:
        raw_snapshot = get_report_snapshot(solver)
        snapshot: dict[str, Any] | None = {
            k: cat.to_json(getattr(raw_snapshot, k))
            for k in (
                "routing_diagnostics",
                "runoff_continuity",
                "routing_continuity",
                "quality_continuity",
            )
        }
    except Exception as exc:  # e.g. BadParamError on a model without subcatchments
        snapshot = None
        snapshot_reason = f"report snapshot unavailable: {type(exc).__name__}: {exc}"
    counts = {"nodes_flooded": 0, "links_surcharged": 0}

    for kind in _ELEMENT_KINDS:
        target = f"{kind}.stats"
        members = cat.members(target)
        attrs = [e["attr"] for e in FACT_SCHEMA.values() if e["kind"] == kind]
        collection = cat.resolve(solver, cat.require_kind(kind)["collection"], None)
        if len(collection) == 0:
            continue  # the bulk ID getters refuse a zero count
        ids = [str(i) for i in collection.ids]
        for entity in ids:
            obj = cat.resolve(solver, target, entity)
            for attr in attrs:
                if attr not in members:
                    continue
                raw = _scalar(getattr(obj, attr))
                entry = FACT_SCHEMA[f"{kind}.{attr}"]
                unit = _unit(entry["unit_kind"], members[attr].get("units"), units)
                fact = _fact(kind, entity, attr, raw, unit)
                if fact is not None:
                    facts.append(fact)
                if raw is not None and raw > 0:
                    if (kind, attr) == ("node", "vol_flooded"):
                        counts["nodes_flooded"] += 1
                    elif (kind, attr) == ("link", "surcharge_time"):
                        counts["links_surcharged"] += 1

    for entry in FACT_SCHEMA.values():
        if entry["kind"] != "pollutant":
            continue
        if snapshot is None:
            dropped.append({"attr": f"pollutant.{entry['attr']}", "reason": snapshot_reason})
            continue
        for row in snapshot["quality_continuity"] or []:
            fact = _fact(
                "pollutant",
                str(row["pollutant_id"]),
                entry["attr"],
                _scalar(row.get(entry["attr"])),
                _unit(entry["unit_kind"], None, units),
            )
            if fact is not None:
                facts.append(fact)

    volume_problem = (
        snapshot_reason
        if snapshot is None
        else _volume_confirmed(session, snapshot["routing_continuity"], units[0])
    )
    for entry in FACT_SCHEMA.values():
        if entry["kind"] != "system":
            continue
        attr = entry["attr"]
        if attr in counts:
            raw = counts[attr]
        elif attr in _SNAPSHOT_SRC:
            if snapshot is None:
                dropped.append({"attr": f"system.{attr}", "reason": snapshot_reason})
                continue
            if attr in _VOLUME_FACTS and volume_problem:
                dropped.append({"attr": f"system.{attr}", "reason": volume_problem})
                continue
            section, field = _SNAPSHOT_SRC[attr]
            raw = _scalar((snapshot[section] or {}).get(field))
        else:
            continue
        fact = _fact("system", "system", attr, raw, _unit(entry["unit_kind"], None, units))
        if fact is not None:
            facts.append(fact)

    facts.sort(key=lambda f: f["id"])
    ids_seen = [f["id"] for f in facts]
    if len(ids_seen) != len(set(ids_seen)):
        raise ToolError(f"[{ErrorCode.ENGINE_ERROR}] duplicate fact ids in the extraction")
    return facts, _meta(session, solver, units, dropped)
