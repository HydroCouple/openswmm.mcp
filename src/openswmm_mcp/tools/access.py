"""Catalog-driven access tools: ``describe``, ``find``, ``get``, ``set``, ``call``.

Every engine property and method listed in ``openswmm.engine.catalog`` is reachable
through these five tools, so new engine capabilities need no new tool code.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastmcp import Context
from openswmm.engine import (
    GeoPackage,
    HotStart,
    ModelBuilder,
    OutputReader,
    XSectionGeometry,
    _enums,
)
from openswmm.engine import _dates as dates_functions
from openswmm.engine import _datetime as datetime_functions

from openswmm_mcp import catalog as cat
from openswmm_mcp.dependencies import get_session, get_session_manager
from openswmm_mcp.errors import ErrorCode, ToolError
from openswmm_mcp.session import SimSession

_OUTPUT_ENUMS = {
    "node": "OutNodeVar",
    "link": "OutLinkVar",
    "subcatchment": "OutSubcatchVar",
    "system": "OutSystemVar",
}
_FUNCTION_MODULES = {"datetime": datetime_functions, "dates": dates_functions}
_STRUCTURAL = re.compile(r"^(add|delete|remove|split|fuse|convert|pop)(_|$)")
_WHERE = re.compile(r"^\s*([\w.]+)\s*(<=|>=|==|!=|<|>)\s*(.+?)\s*$")
_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}
_DOC_CHARS = 160


# ---------------------------------------------------------------------------
# describe
# ---------------------------------------------------------------------------
def _short(doc: str) -> str:
    return doc if len(doc) <= _DOC_CHARS else doc[: _DOC_CHARS - 1] + "…"


def _signature(member: dict) -> str:
    parts = []
    for p in member.get("params", []):
        text = f"{p['name']}: {p['type']}" if p["type"] else p["name"]
        parts.append(text if p["required"] else f"{text} = …")
    return f"({', '.join(parts)}) -> {member.get('returns') or 'None'}"


def _units_context(solver: Any) -> tuple[str | None, str | None]:
    if solver is None:
        return None, None
    try:
        return solver.unit_system, _enums.FlowUnits(int(solver.flow_units)).name
    except Exception:
        return None, None


def _describe_target(name: str, solver: Any) -> dict[str, Any]:
    entry = cat.require_target(name)
    system, flow = _units_context(solver)
    fields, methods = [], []
    for m in cat.members(name).values():
        if m["form"] == "property":
            f = {
                "name": m["name"],
                "type": m["type"],
                "access": m["access"],
                "doc": _short(m["doc"]),
            }
            if "units" in m:
                f["units"] = cat.unit_label(m["units"], system, flow)
            if m.get("phases"):
                f["phases"] = m["phases"]
            fields.append(f)
        else:
            methods.append({"name": m["name"], "signature": _signature(m), "doc": _short(m["doc"])})
    out: dict[str, Any] = {
        "target": name,
        "doc": _short(entry["doc"]),
        "fields": fields,
        "methods": methods,
    }
    for key in ("collection", "element", "key", "subtypes", "subtype_field", "construct"):
        if key in entry:
            out[key] = entry[key]
    subs = {
        t[len(name) + 1 :]: sorted(
            m["name"] for m in cat.members(t).values() if m["form"] == "property"
        )
        for t in cat.targets()
        if t.startswith(name + ".") and "." not in t[len(name) + 1 :]
    }
    if subs:
        out["subviews"] = subs
    if "collection" in entry:
        out["address"] = (
            f"get(kind='{name}', fields=[...]); call(target='{name}:<id>', ...); "
            f"sub-view fields as '<subview>.<field>'"
        )
    elif "construct" not in entry:
        out["address"] = f"get(kind='{name}', fields=[...]) or call(target='{name}', ...)"
    return out


def _describe_overview(solver: Any) -> dict[str, Any]:
    targets = cat.targets()
    kinds = {}
    for name, entry in cat.element_kinds().items():
        info: dict[str, Any] = {"collection": entry["collection"]}
        if solver is not None:
            try:
                info["count"] = len(cat.resolve(solver, entry["collection"], None))
            except Exception:
                pass
        kinds[name] = info
    services = sorted(
        n
        for n, e in targets.items()
        if "collection" not in e and "construct" not in e and e.get("parent") in (None, "solver")
    )
    return {
        "element_kinds": kinds,
        "services": services,
        "standalone": {n: e["construct"]["args"] for n, e in targets.items() if "construct" in e},
        "functions": sorted(_FUNCTION_MODULES),
        "enums": sorted(cat.catalog()["enums"]),
        "how_to": (
            "describe('<kind or service>') lists fields and methods; get/set read and "
            "write fields of an element kind (sub-view fields as 'stats.max_depth') or "
            "of a service; call runs any listed method on a service ('forcing'), an "
            "element ('node:J1', 'node:OUT1.outfall') or a standalone target; enum "
            "values are passed by name; describe('output') lists time-series variables."
        ),
    }


async def describe(ctx: Context, topic: str = "", session_id: str | None = None) -> dict:
    """Discover everything the engine exposes.

    "" lists element kinds, services, standalone targets and enums; "<kind>" or "<service>"
    (e.g. "node", "link", "surface2d.groundwater", "forcing") lists fields and methods with
    type, units, read/write and lifecycle phases; "<target>.<member>" gives full detail;
    "enum:<Name>" lists enum members; "output" lists time-series variables. With
    session_id, units resolve to the model's unit system and element counts are shown.
    """
    session = await get_session(ctx, session_id) if session_id else None
    solver = session.solver if session else None

    def _run() -> dict:
        if not topic:
            return _describe_overview(solver)
        if topic.startswith("enum:"):
            name = topic[5:]
            if name not in cat.catalog()["enums"]:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Unknown enum '{name}'."
                    f"{cat.suggest(name, cat.catalog()['enums'])}"
                )
            return {"enum": name, "members": cat.catalog()["enums"][name]}
        if topic == "output":
            return {
                kind: [m.lower() for m in cat.catalog()["enums"][enum]]
                for kind, enum in _OUTPUT_ENUMS.items()
            } | {"note": "Pollutant concentrations: variable 'quality:<pollutant id>'."}
        if topic in cat.targets():
            return _describe_target(topic, solver)
        target, _, name = topic.rpartition(".")
        member = cat.members(target).get(name) if target else None
        if member is None:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown topic '{topic}'."
                f"{cat.suggest(topic, cat.targets())}"
            )
        out = dict(member)
        if "units" in out:
            out["units"] = cat.unit_label(out["units"], *_units_context(solver))
        if "params" in out:
            out["signature"] = _signature(member)
        return out

    if session is None:
        return _run()
    return await session.call(_run)


# ---------------------------------------------------------------------------
# find / get / set
# ---------------------------------------------------------------------------
def _ids(solver: Any, kind: str) -> list[str]:
    collection = cat.resolve(solver, cat.require_kind(kind)["collection"], None)
    if len(collection) == 0:
        return []  # the bulk ID getters refuse a zero count
    ids = getattr(collection, "ids", None)
    if ids is not None and not callable(ids):
        return [str(i) for i in ids]
    return [str(e.id) for e in collection]


def _builder(session: SimSession) -> ModelBuilder:
    """The session's model builder, created on first use."""
    if "builder" not in session.readers:
        session.readers["builder"] = ModelBuilder()
    return session.readers["builder"]


def _owner(session: SimSession, target: str, key: str | None) -> Any:
    """The live object behind a catalog target (the session's builder for ``builder``)."""
    if target == "builder":
        return _builder(session)
    return cat.resolve(session.require_solver(), target, key)


def _read(session: SimSession, member: dict, key: str | None) -> Any:
    owner = _owner(session, member["target"], key)
    value = getattr(owner, member["name"])
    if member.get("type", "").startswith("ref:"):
        return getattr(value, "id", value)
    return value


def _parse_literal(text: str) -> Any:
    text = text.strip().strip("'\"")
    try:
        return float(text)
    except ValueError:
        return text


async def find(
    ctx: Context,
    session_id: str,
    kind: str,
    pattern: str | None = None,
    type: str | None = None,
    where: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """List object IDs of a kind, filtered by an ID regex, a subtype (e.g. "STORAGE", "PUMP"),
    and/or a field predicate such as "stats.max_depth > 2"."""
    session = await get_session(ctx, session_id)
    solver = session.require_solver()
    entry = cat.require_kind(kind)
    try:
        regex = re.compile(pattern) if pattern else None
    except re.error as exc:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] Bad pattern '{pattern}': {exc}.") from None
    predicate = None
    if where:
        m = _WHERE.match(where)
        if not m:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] where must look like "
                "'<field> <op> <value>' with op one of < <= > >= == !=."
            )
        predicate = (cat.field(kind, m.group(1)), _OPS[m.group(2)], _parse_literal(m.group(3)))
    subtype_member = (
        cat.members(kind).get(entry["subtype_field"]) if entry.get("subtype_field") else None
    )

    def _run() -> dict:
        matched, types = [], []
        for key in _ids(solver, kind):
            if regex and not regex.search(key):
                continue
            subtype = cat.to_json(_read(session, subtype_member, key)) if subtype_member else None
            if type and (subtype or "").upper() != type.upper():
                continue
            if predicate:
                member, op, value = predicate
                try:
                    current = cat.to_json(_read(session, member, key))
                    if current is None or not op(current, value):
                        continue
                except (TypeError, AttributeError):
                    continue
            matched.append(key)
            types.append(subtype)
        page = slice(offset, offset + limit)
        out: dict[str, Any] = {"kind": kind, "total": len(matched), "ids": matched[page]}
        if subtype_member:
            out["types"] = types[page]
        if offset + limit < len(matched):
            out["next_offset"] = offset + limit
        return out

    return await session.call(_run, context="find")


async def get(
    ctx: Context,
    session_id: str,
    kind: str,
    fields: list[str],
    ids: list[str] | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    """Read fields for objects of one kind as a table (one column per field).

    ids=None reads all objects using bulk reads. fields are catalog paths from `describe`,
    e.g. ["depth", "stats.max_depth", "storage.shape"]. A service such as "mass_balance" or
    "surface2d" is read the same way without ids. Values are in model units; units are
    returned alongside.
    """
    session = await get_session(ctx, session_id)
    solver = session.require_solver()
    entry = cat.require_target(kind)
    members = {f: cat.field(kind, f) for f in fields}
    system, flow = await session.call(_units_context, solver)
    units = {
        f: cat.unit_label(m["units"], system, flow) for f, m in members.items() if "units" in m
    }

    if "collection" not in entry:

        def _service() -> dict:
            values, errors = {}, {}
            for f, m in members.items():
                try:
                    values[f] = cat.to_json(_read(session, m, None))
                except Exception as exc:
                    errors[f] = f"{type(exc).__name__}: {exc}"
            out = {"target": kind, "values": values, "units": units}
            if errors:
                out["errors"] = errors
            return out

        return await session.call(_service, context="get")

    def _elements() -> dict:
        all_ids = _ids(solver, kind) if ids is None else list(ids)
        page = all_ids[offset : offset + limit]
        columns, errors = {}, {}
        for f, m in members.items():
            if ids is None and m.get("bulk"):
                bulk = cat.lookup_bulk(solver, m["bulk"])
                columns[f] = cat.to_json(bulk[offset : offset + limit], limit=limit)
                continue
            values = []
            for key in page:
                try:
                    values.append(cat.to_json(_read(session, m, key)))
                except Exception as exc:
                    values.append(None)
                    errors.setdefault(f, f"{type(exc).__name__}: {exc}")
            columns[f] = values
        out: dict[str, Any] = {
            "kind": kind,
            "ids": page,
            "columns": columns,
            "units": units,
            "total": len(all_ids),
        }
        if offset + limit < len(all_ids):
            out["next_offset"] = offset + limit
        if errors:
            out["errors"] = errors
        return out

    return await session.call(_elements, context="get")


async def set_fields(
    ctx: Context, session_id: str, kind: str, changes: list[dict[str, Any]]
) -> dict:
    """Write fields: changes = [{"id": "J1", "field": "max_depth", "value": 12.0}, ...].

    Omit "id" for a service such as "options.ext" or "climate". Each change is validated
    (writable, type, subtype, lifecycle state) and reported individually; valid changes
    apply even when others fail.
    """
    session = await get_session(ctx, session_id)
    base = get_session_manager(ctx).working_dir
    cat.require_target(kind)

    def _run() -> dict:
        results = []
        for change in changes:
            key, name = change.get("id"), change.get("field", "")
            result: dict[str, Any] = {"id": key, "field": name}
            try:
                member = cat.field(kind, name)
                if member["access"] != "rw":
                    raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] '{kind}.{name}' is read-only.")
                owner = _owner(session, member["target"], key)
                value = cat.coerce(change.get("value"), member["type"], member["name"], base)
                setattr(owner, member["name"], value)
                result["ok"] = True
                result["value"] = cat.to_json(_read(session, member, key))
            except Exception as exc:
                result["ok"] = False
                result["error"] = (
                    str(exc) if isinstance(exc, ToolError) else f"{type(exc).__name__}: {exc}"
                )
            results.append(result)
        return {"kind": kind, "results": results, "applied": sum(1 for r in results if r["ok"])}

    return await session.call(_run, context="set")


# ---------------------------------------------------------------------------
# call
# ---------------------------------------------------------------------------
def _bind(member: dict, args: dict[str, Any], session: SimSession, base: Path) -> tuple[list, dict]:
    """Coerce JSON *args* to the catalog signature: positional up to the first gap."""
    params = member.get("params", [])
    known = {p["name"] for p in params}
    unknown = set(args) - known
    if unknown:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] Unknown argument(s) {sorted(unknown)} for "
            f"{member['path']}{_signature(member)}."
        )
    positional, keywords, gap = [], {}, False
    for p in params:
        if "Solver" in p["type"] and p["name"] not in args:
            value = session.require_solver()  # e.g. HotStart.apply(solver)
        elif p["name"] not in args:
            if p["required"]:
                raise ToolError(
                    f"[{ErrorCode.VALIDATION_ERROR}] Missing argument '{p['name']}' "
                    f"for {member['path']}{_signature(member)}."
                )
            gap = True
            continue
        else:
            value = cat.coerce(args[p["name"]], p["type"], p["name"], base)
        if gap or p.get("keyword_only"):
            keywords[p["name"]] = value
        else:
            positional.append(value)
    return positional, keywords


def _item(obj: Any, name: str, member: dict, args: dict[str, Any]) -> Any:
    params = member.get("params", [])
    for p in params:
        if p["required"] and p["name"] not in args:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Missing argument '{p['name']}' "
                f"for {member['path']}{_signature(member)}."
            )
    key_type = params[0]["type"] if params else ""
    if name == "items":
        return list(obj.items()) if hasattr(obj, "items") else list(enumerate(obj))
    key = cat.coerce(args.get("key"), key_type)
    if name == "get_item":
        return obj[key]
    if name == "set_item":
        obj[key] = cat.coerce(args.get("value"), member["params"][1]["type"])
        return obj[key]
    del obj[key]
    return None


_ROOT_CLASSES = {
    "output": OutputReader,
    "geopackage": GeoPackage,
    "hotstart": HotStart,
    "xsect": XSectionGeometry,
    "builder": ModelBuilder,
}
# Objects the session caches and closes itself.
_CACHED = {"output", "geopackage", "builder"}


def _xsect(session: SimSession, args: dict[str, Any], base: Path) -> XSectionGeometry:
    """Build the section a kernel call works on: constructor args, or {"from": {...}}."""
    spec = args.pop("from", None)
    if spec is not None:
        spec = dict(spec)
        via = spec.pop("method", "")
        member = cat.members("xsect").get(via)
        if member is None or not member.get("static"):
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] 'from.method' must be one of "
                "from_curve, from_link, from_street, from_transect."
            )
        if via == "from_link":
            spec["link"] = session.require_solver().links[spec["link"]]
        positional, keywords = _bind(member, spec, session, base)
        return getattr(XSectionGeometry, via)(*positional, **keywords)
    missing = [a for a in ("shape", "geom1", "units") if a not in args]
    if missing:
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] xsect needs {missing} (or a "
            '\'from\' spec such as {"method": "from_street", ...}).'
        )
    shape = cat.coerce(args.pop("shape"), "XSectShape")
    geoms = [float(args.pop(g, 0.0)) for g in ("geom1", "geom2", "geom3", "geom4")]
    return XSectionGeometry(shape, *geoms, units=args.pop("units"))


def _standalone(
    session: SimSession, target: str, member: dict, args: dict[str, Any], base: Path
) -> Any:
    """The object a standalone target's method runs on (constructed or cached)."""
    if member.get("static"):
        if target == "xsect" and isinstance(args.get("link"), str):
            args["link"] = session.require_solver().links[args["link"]]
        return _ROOT_CLASSES[target]
    if target in _CACHED and member["name"] == "close":
        raise ToolError(
            f"[{ErrorCode.NOT_SUPPORTED}] The session opens and closes '{target}' readers itself."
        )
    if target == "xsect":
        return _xsect(session, args, base)
    if target == "builder":
        if member["name"] == "to_solver":
            raise ToolError(
                f"[{ErrorCode.NOT_SUPPORTED}] A built model cannot run in place: write it with "
                "call(target='builder', method='write', args={'path': ...}) and open_model it."
            )
        return _builder(session)
    path = args.pop("path", None)
    if target == "output":
        return session.output_reader(
            str(cat.coerce(path, "PathLike", "path", base)) if path else None
        )
    if path is None:
        raise ToolError(f"[{ErrorCode.VALIDATION_ERROR}] '{target}' needs a 'path' argument.")
    path = str(cat.coerce(path, "PathLike", "path", base))
    if target == "geopackage":
        key = f"gpkg:{path}"
        if key not in session.readers:
            session.readers[key] = GeoPackage(path)
        return session.readers[key]
    if target == "hotstart":
        return HotStart.open(path)
    raise ToolError(f"[{ErrorCode.NOT_SUPPORTED}] '{target}' has no MCP handler.")


async def call(
    ctx: Context, session_id: str, target: str, method: str, args: dict[str, Any] | None = None
) -> dict:
    """Invoke any engine method listed by `describe`.

    target is a service ("forcing", "tables", "controls", "surface2d", "reactions", ...),
    an element "<kind>:<id>[.<subview>]" such as "node:OUT1.outfall", a standalone target
    ("output", "xsect", "geopackage", "hotstart" with their constructor args; "builder" for a
    model authored from scratch, then write it and open_model it) or a function module
    ("datetime"). args are keyword arguments matching the catalog signature,
    e.g. call("s", "forcing", "node_lat_inflow", {"node": "J1", "value": 2.5, "persist": true}).
    """
    session = await get_session(ctx, session_id)
    base = get_session_manager(ctx).working_dir
    args = dict(args or {})
    if target in _FUNCTION_MODULES:
        functions = cat.catalog()["functions"].get(f"_{target}", {})
        if method not in functions:
            raise ToolError(
                f"[{ErrorCode.VALIDATION_ERROR}] Unknown function '{method}'."
                f"{cat.suggest(method, functions)}"
            )
        member = dict(functions[method], path=f"{target}.{method}")
        positional, keywords = _bind(member, args, session, base)
        result = getattr(_FUNCTION_MODULES[target], method)(*positional, **keywords)
        return {"target": target, "method": method, "result": cat.to_json(result)}

    name, key = cat.parse_target(target)
    entry = cat.require_target(name)
    if name == "solver" and method in cat.SOLVER_DENY:
        raise ToolError(
            f"[{ErrorCode.NOT_SUPPORTED}] Use the run/session/save tools for solver.{method}."
        )
    member = cat.members(name).get(method)
    if member is None or member["form"] == "property":
        methods = [m for m, e in cat.members(name).items() if e["form"] != "property"]
        hint = " It is a field: use get/set." if member else cat.suggest(method, methods)
        raise ToolError(
            f"[{ErrorCode.VALIDATION_ERROR}] '{method}' is not a method of '{name}'.{hint}"
        )

    def _run() -> Any:
        if "construct" in entry:
            obj = _standalone(session, name, member, args, base)
        else:
            obj = cat.resolve(session.require_solver(), name, key)
        try:
            if member["form"] == "item":
                return cat.to_json(_item(obj, method, member, args))
            positional, keywords = _bind(member, args, session, base)
            if name == "editor" or _STRUCTURAL.match(method):
                session.structure_edited = True  # before mutating
            return cat.to_json(getattr(obj, method)(*positional, **keywords))
        finally:
            if name == "hotstart" and not member.get("static"):
                obj.close()

    result = await session.call(_run, context=f"{target}.{method}")
    return {"target": target, "method": method, "result": result}
