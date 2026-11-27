"""Fact schema for the tally ledger: attribute table, binning and policy matching."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

__all__ = [
    "FACT_SCHEMA",
    "SCHEMA_VERSION",
    "bin_value",
    "fact_id",
    "fact_schema_entry",
    "within_policy",
]

SCHEMA_VERSION = "1"

_SIGFIG3: dict[str, Any] = {"type": "sigfig", "n": 3}
_ABS001: dict[str, Any] = {"type": "abs", "tol": 0.01}
_EXACT: dict[str, Any] = {"type": "exact"}

# (kind, attr, src, unit_kind, scope, policy)
_ROWS: list[tuple[str, str, str, str, str, dict[str, Any]]] = [
    ("node", "max_depth", "node.stats", "length", "output", _SIGFIG3),
    ("node", "max_overflow", "node.stats", "flow", "diagnostic", _SIGFIG3),
    ("node", "time_flooded", "node.stats", "s", "diagnostic", _SIGFIG3),
    ("node", "vol_flooded", "node.stats", "volume", "diagnostic", _SIGFIG3),
    ("link", "max_flow", "link.stats", "catalog", "output", _SIGFIG3),
    ("link", "max_velocity", "link.stats", "catalog", "output", _SIGFIG3),
    ("link", "max_filling", "link.stats", "catalog", "output", _SIGFIG3),
    ("link", "vol_flow", "link.stats", "catalog", "output", _SIGFIG3),
    ("link", "surcharge_time", "link.stats", "h", "diagnostic", _SIGFIG3),
    ("subcatchment", "precip", "subcatchment.stats", "catalog", "output", _SIGFIG3),
    ("subcatchment", "runoff_vol", "subcatchment.stats", "catalog", "output", _SIGFIG3),
    ("subcatchment", "max_runoff", "subcatchment.stats", "catalog", "output", _SIGFIG3),
    (
        "pollutant",
        "continuity_error_pct",
        "snapshot quality_continuity[i]",
        "%",
        "diagnostic",
        _ABS001,
    ),
    (
        "system",
        "runoff_continuity_error_pct",
        "snapshot runoff_continuity.continuity_error_pct",
        "%",
        "diagnostic",
        _ABS001,
    ),
    (
        "system",
        "routing_continuity_error_pct",
        "snapshot routing_continuity.continuity_error_pct",
        "%",
        "diagnostic",
        _ABS001,
    ),
    ("system", "pct_not_converged", "snapshot routing_diagnostics", "%", "diagnostic", _ABS001),
    (
        "system",
        "n_steps_not_converged",
        "snapshot routing_diagnostics",
        "count",
        "diagnostic",
        _EXACT,
    ),
    ("system", "min_time_step", "snapshot routing_diagnostics", "s", "output", _SIGFIG3),
    ("system", "max_time_step", "snapshot routing_diagnostics", "s", "output", _SIGFIG3),
    (
        "system",
        "nodes_flooded",
        "derived: count of nodes with vol_flooded > 0",
        "count",
        "diagnostic",
        _EXACT,
    ),
    (
        "system",
        "links_surcharged",
        "derived: count of links with surcharge_time > 0",
        "count",
        "diagnostic",
        _EXACT,
    ),
    ("system", "total_flooding", "snapshot routing_continuity", "volume", "output", _SIGFIG3),
    ("system", "total_outflow", "snapshot routing_continuity", "volume", "output", _SIGFIG3),
]

FACT_SCHEMA: dict[str, dict] = {
    f"{kind}.{attr}": {
        "kind": kind,
        "attr": attr,
        "src": src,
        "unit_kind": unit_kind,
        "scope": scope,
        "policy": dict(policy),
    }
    for kind, attr, src, unit_kind, scope, policy in _ROWS
}


def fact_schema_entry(kind: str, attr: str) -> dict:
    """Return the schema entry for ``kind.attr``.

    Parameters
    ----------
    kind : str
        Fact kind (``node``, ``link``, ``subcatchment``, ``pollutant``, ``system``).
    attr : str
        Attribute name.

    Returns
    -------
    dict
        Entry with ``kind, attr, src, unit_kind, scope, policy``.

    Raises
    ------
    KeyError
        If the pair is not in the schema.
    """
    return FACT_SCHEMA[f"{kind}.{attr}"]


def fact_id(kind: str, entity: str, attr: str, time: str | None = None) -> str:
    """Build a fact id ``f:{kind}:{entity}:{attr}[:{time}]``."""
    base = f"f:{kind}:{entity}:{attr}"
    return f"{base}:{time}" if time else base


def _decimals(tol: float) -> int:
    exp = Decimal(repr(tol)).normalize().as_tuple().exponent
    return max(0, -exp) if isinstance(exp, int) else 0


def bin_value(value: Any, policy: dict) -> int | float | None:
    """Bin a value by policy. Returns None for None/NaN/inf; ``-0.0`` becomes ``0.0``."""
    try:
        return _bin_value(value, policy)
    except ArithmeticError:
        return None


def _bin_value(value: Any, policy: dict) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    kind = policy["type"]
    if kind == "exact":
        out: int | float = int(round(value))
    elif kind == "sigfig":
        out = float(f"{value:.{int(policy['n'])}g}")
        if isinstance(value, int) and not isinstance(value, bool):
            out = int(out)
    elif kind == "abs":
        tol = float(policy["tol"])
        out = round(round(value / tol) * tol, _decimals(tol) + 2)
    else:
        raise ValueError(f"unknown policy type: {kind!r}")
    if out == 0:
        return 0 if isinstance(out, int) else 0.0
    return out


def within_policy(expected: Any, claimed: Any, policy: dict) -> bool:
    """True when ``claimed`` matches ``expected`` under ``policy``.

    sigfig: both bin to the same value; abs: ``|a - b| <= tol``; exact: equal.
    """
    if expected is None or claimed is None:
        return False
    try:
        if isinstance(expected, float) and not math.isfinite(expected):
            return False
        if isinstance(claimed, float) and not math.isfinite(claimed):
            return False
        kind = policy["type"]
        if kind == "abs":
            tol = float(policy["tol"])
            return abs(expected - claimed) <= tol + 1e-12 * max(1.0, tol)
        if kind == "exact":
            return int(round(expected)) == int(round(claimed))
        return bin_value(expected, policy) == bin_value(claimed, policy)
    except (TypeError, ValueError, ArithmeticError):
        return False
