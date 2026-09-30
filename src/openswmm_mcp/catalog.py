"""The engine catalog as the MCP tools use it: addressing, coercion and JSON.

``openswmm.engine.catalog`` lists every target the Python API exposes
(services such as ``forcing``, element kinds such as ``node``, sub-views such
as ``node.stats``) and every property and method on them. The generic tools
(``describe``, ``find``, ``get``, ``set``, ``call``) are thin layers over this
module, so a new engine capability reaches the server without new tool code.
"""

from __future__ import annotations

import dataclasses
import difflib
import enum
import math
import re
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from openswmm.engine import _enums

try:
    from openswmm.engine import catalog as _engine_catalog
except ImportError as exc:  # pragma: no cover - depends on the installed engine
    raise ImportError(
        "openswmm.mcp needs an openswmm engine build that ships openswmm.engine.catalog "
        "(built from openswmm.engine after 2026-09-30); upgrade or rebuild the openswmm package."
    ) from exc

from openswmm_mcp.errors import ErrorCode, ToolError

# Lifecycle and handle-level Solver members are driven by the session tools;
# dispatching them through ``call`` would desynchronise session state.
SOLVER_DENY = frozenset(
    {
        "create",
        "open",
        "initialize",
        "start",
        "step",
        "stride",
        "end",
        "report",
        "close",
        "destroy",
        "run",
        "steps",
        "until",
        "set_lenient_open",
        "write_staged",
        # Python callables cannot cross MCP.
        "set_progress_callback",
        "set_step_begin_callback",
        "set_step_end_callback",
        "set_warning_callback",
    }
)
# Parameters holding file paths; resolved against the session working directory.
_PATH_PARAMS = re.compile(r"(^|_)(path|file|filename|inp|rpt|out|dir)$")
MAX_ARRAY = 2000


# ---------------------------------------------------------------------------
# Catalog views
# ---------------------------------------------------------------------------
def catalog() -> dict[str, Any]:
    return _engine_catalog.load()


def targets() -> dict[str, dict[str, Any]]:
    return catalog()["targets"]


@lru_cache(maxsize=1)
def _members_by_target() -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for m in catalog()["members"]:
        out.setdefault(m["target"], {})[m["name"]] = m
    return out


def members(target: str) -> dict[str, dict[str, Any]]:
    return _members_by_target().get(target, {})


def element_kinds() -> dict[str, dict[str, Any]]:
    """Element targets addressable by id (``node``, ``link``, ``species``, ...)."""
    return {name: t for name, t in targets().items() if "collection" in t}


def suggest(word: str, choices: Any) -> str:
    close = difflib.get_close_matches(word, list(choices), n=4, cutoff=0.5)
    return f" Did you mean: {', '.join(close)}?" if close else ""


def require_target(name: str) -> dict[str, Any]:
    entry = targets().get(name)
    if entry is None:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown target '{name}'."
            f"{suggest(name, targets())} Use describe() to list targets."
        )
    return entry


def require_kind(kind: str) -> dict[str, Any]:
    entry = require_target(kind)
    if "collection" not in entry:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] '{kind}' is not an element kind. "
            f"Element kinds: {', '.join(sorted(element_kinds()))}."
        )
    return entry


def element_of(target: str) -> str | None:
    """The element kind a target belongs to (``node`` for ``node.stats``), if any."""
    entry = targets()[target]
    while "collection" not in entry:
        parent = entry.get("parent")
        if parent is None:
            return None
        target, entry = parent, targets()[parent]
    return target


def field(kind: str, name: str) -> dict[str, Any]:
    """Resolve ``stats.max_depth`` on kind ``node`` to its property entry."""
    target, _, attr = f"{kind}.{name}".rpartition(".")
    entry = members(target).get(attr) if target in targets() else None
    if entry is None or entry["form"] != "property":
        fields = [f for f in field_names(kind)]
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] '{name}' is not a field of '{kind}'."
            f"{suggest(name, fields)} Use describe('{kind}') for its fields."
        )
    return entry


def field_names(kind: str) -> list[str]:
    """All readable fields of a kind, including those on its sub-views."""
    names = []
    for target in targets():
        if target == kind or target.startswith(kind + "."):
            prefix = target[len(kind) + 1 :]
            for m in members(target).values():
                if m["form"] == "property":
                    names.append(f"{prefix}.{m['name']}" if prefix else m["name"])
    return names


def parse_target(text: str) -> tuple[str, str | None]:
    """``node:J1.outfall`` -> (``node.outfall``, ``J1``); ``forcing`` -> (``forcing``, None).

    IDs may contain dots (``node:MH.12``): only a trailing known sub-view name
    (``.outfall``, ``.stats``, ...) is split off.
    """
    if ":" not in text:
        return text, None
    kind, _, rest = text.partition(":")
    require_kind(kind)
    subs = [t[len(kind) + 1 :] for t in targets() if t.startswith(kind + ".")]
    for sub in sorted(subs, key=len, reverse=True):
        if rest.endswith("." + sub) and len(rest) > len(sub) + 1:
            return f"{kind}.{sub}", rest[: -len(sub) - 1]
    return kind, rest


def resolve(solver: Any, target: str, key: str | int | None) -> Any:
    try:
        return _engine_catalog.resolve(solver, target, key)
    except ValueError as exc:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] {exc}") from None


def lookup_bulk(solver: Any, path: str) -> Any:
    """Read a collection array such as ``nodes.depths``."""
    target, _, name = path.rpartition(".")
    return getattr(resolve(solver, target, None), name)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
_US = {
    "length": "ft",
    "volume": "ft3",
    "velocity": "ft/s",
    "area": "ft2 (subcatchment: ac)",
    "rain_rate": "in/hr",
    "evap_rate": "in/day",
    "rain_depth": "in",
    "user_temperature": "degF",
    "wind_speed": "mph",
}
_SI = {
    "length": "m",
    "volume": "m3",
    "velocity": "m/s",
    "area": "m2 (subcatchment: ha)",
    "rain_rate": "mm/hr",
    "evap_rate": "mm/day",
    "rain_depth": "mm",
    "user_temperature": "degC",
    "wind_speed": "km/hr",
}


def unit_label(kind: str | None, unit_system: str | None, flow_units: str | None) -> str | None:
    if kind is None or unit_system is None:
        return kind
    if kind == "flow":
        return flow_units or kind
    table = _SI if unit_system == "SI" else _US
    return table.get(kind, kind)


# ---------------------------------------------------------------------------
# Coercion (JSON -> engine) and serialization (engine -> JSON)
# ---------------------------------------------------------------------------
def _enum_names(type_str: str) -> list[str]:
    return [n for n in re.findall(r"\b([A-Z]\w+)\b", type_str) if n in catalog()["enums"]]


def coerce(value: Any, type_str: str, name: str = "", working_dir: Path | None = None) -> Any:
    """Convert a JSON value to what an engine parameter/property expects."""
    if value is None:
        return None
    t = type_str or ""
    for enum_name in _enum_names(t):
        if isinstance(value, str) and not value.lstrip("-").isdigit():
            cls = getattr(_enums, enum_name)
            try:
                return cls[value.strip().upper()]
            except KeyError:
                if "str" not in t:
                    raise ToolError(
                        f"[{ErrorCode.VALIDATION_ERROR}] '{value}' is not a "
                        f"{enum_name}. Valid: {', '.join(cls.__members__)}."
                    ) from None
        elif isinstance(value, (int, float)) and "Union" not in t and "|" not in t:
            return getattr(_enums, enum_name)(int(value))
    if (
        working_dir is not None
        and isinstance(value, str)
        and ("PathLike" in t or _PATH_PARAMS.search(name))
    ):
        path = Path(value).expanduser()
        return str(path if path.is_absolute() else (working_dir / path).resolve())
    if isinstance(value, str) and re.search(r"\bdatetime\b", t):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    if isinstance(value, (int, float)) and "timedelta" in t:
        return timedelta(seconds=float(value))
    if isinstance(value, list):
        if "NDArray" in t or "Iterable[float]" in t:
            return np.asarray(value, dtype=float)
        if re.match(r"(?i)tuple", t):
            return tuple(value)
    return value


def to_json(value: Any, limit: int = MAX_ARRAY) -> Any:
    """Convert an engine value to plain JSON (enums by name, arrays capped)."""
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return v if math.isfinite(v) else None
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, np.datetime64):
        return str(value)
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, np.ndarray):
        if value.ndim == 0:
            return to_json(value.item(), limit)
        if value.dtype.names:
            value = [dict(zip(value.dtype.names, row)) for row in value[:limit]]
            return [to_json(v, limit) for v in value]
        flat = value if value.ndim <= 1 else value.reshape(len(value), -1)
        out = [to_json(v, limit) for v in flat[:limit].tolist()]
        return out
    if hasattr(value, "_asdict"):
        return {k: to_json(v, limit) for k, v in value._asdict().items()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_json(getattr(value, f.name), limit) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(to_json(k)): to_json(v, limit) for k, v in list(value.items())[:limit]}
    if isinstance(value, (list, tuple, set)):
        return [to_json(v, limit) for v in list(value)[:limit]]
    if hasattr(value, "id") and hasattr(value, "index"):
        return value.id  # an element wrapper: report it by id
    fields = {}
    for k in dir(value):
        if k.startswith("_"):
            continue
        try:
            v = getattr(value, k)
        except Exception:
            continue
        if not callable(v):
            fields[k] = v
    if fields:
        return {k: to_json(v, limit) for k, v in fields.items()}
    return repr(value)
