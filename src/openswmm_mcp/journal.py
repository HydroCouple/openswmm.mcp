"""The tool-call journal: one JSON line per tool call the server answers.

Each server process appends to ``<working_dir>/journal/<journal id>.jsonl``. A line names the
tool, the effect the call was allowed to have, the SHA-256 of its arguments and of its result
as canonical JSON (never their content), when it started and ended, and its tool call id.
``platform_run`` passes that id to the platform, which writes it into the attempt manifest's
actor, so a run can be traced back to the call that submitted it. With the platform toolset,
each line also names the platform agent session and the person it acts for, the identity the
water ontology's ``ToolCall`` and ``AgentSession`` rows are built from. Without it, the
journal is the session and the operating-system login is the person.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from openswmm_mcp import __version__
from openswmm_mcp.provenance import local_identity

#: The tool call id of the call being answered; ``platform_run`` sends it to the platform.
CURRENT_CALL: ContextVar[str | None] = ContextVar("openswmm_mcp_tool_call", default=None)
#: The journal recording the call being answered; ``platform_finding`` cites its calls.
ACTIVE: ContextVar[Journal | None] = ContextVar("openswmm_mcp_journal", default=None)

_READ = frozenset(
    {
        "describe",
        "find",
        "get",
        "inspect",
        "timeseries",
        "report",
        "compare",
        "locate",
        "gym_describe",
        "platform_targets",
        "platform_estimate",
        "platform_results",
    }
)
_FILE = frozenset({"open_model", "save", "export"})
_BY_ACTION = {
    "tally": {
        "list": "read",
        "root": "read",
        "verify": "read",
        "facts": "file",
        "commit": "file",
        "prove": "file",
    },
    "sources": {"list": "read", "lineage": "read", "mentions": "read", "register": "file"},
    "session": {"list": "read", "state": "read"},
    "platform_session": {"list": "read"},
    "platform_model": {"get": "read", "upload": "file"},
    "platform_job": {"status": "read", "list": "read"},
}
_CODE = re.compile(r"^\[([A-Z_]+)\]")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def effect_of(tool: str, arguments: dict[str, Any]) -> str:
    """``read``, ``write``, ``structural`` or ``file``: what the call was allowed to change.

    ``call`` takes the effect the engine catalog gives the method; a tool no rule places is
    ``write``, so an unclassified call is never recorded as harmless.
    """
    if tool in _READ:
        return "read"
    if tool in _FILE:
        return "file"
    if tool == "edit":
        return "structural"
    if tool in _BY_ACTION:
        return _BY_ACTION[tool].get(str(arguments.get("action", "")), "write")
    if tool == "call":
        return _method_effect(str(arguments.get("target", "")), str(arguments.get("method", "")))
    return "write"


def _method_effect(target: str, method: str) -> str:
    from openswmm_mcp import catalog as cat

    try:
        functions = cat.catalog()["functions"].get(f"_{target}")
        if functions is not None:
            return functions[method]["effect"]
        name, _ = cat.parse_target(target)
        return cat.members(name)[method]["effect"]
    except Exception:
        return "write"  # an unknown method: the call refuses it anyway


def _result_json(result: Any) -> Any:
    if result.structured_content is not None:
        return result.structured_content
    return [block.model_dump(mode="json") for block in result.content]


class Journal(Middleware):
    """Records every tool call. ``read_only`` names the tools that declare they change nothing."""

    def __init__(self, working_dir: str, read_only: frozenset[str]):
        self.journal_id = f"jr_{uuid.uuid4().hex[:16]}"
        self.path = (
            Path(working_dir).expanduser().resolve() / "journal" / f"{self.journal_id}.jsonl"
        )
        self.read_only = read_only
        self._sequence = 0
        self._identity: dict[str, Any] | None = None

    async def _platform_identity(self, context: MiddlewareContext) -> dict[str, Any]:
        """The agent session and person of the platform toolset, asked once."""
        if self._identity is not None:
            return self._identity
        ctx = context.fastmcp_context
        client = (ctx.lifespan_context or {}).get("platform") if ctx is not None else None
        if client is None:  # a local session: this journal, for the operating-system login
            self._identity = {"agent_session_id": self.journal_id, "acting_for": local_identity()}
            return self._identity
        try:
            me = await asyncio.to_thread(client.me)
        except Exception:
            return {}  # the platform is unreachable now: ask again on the next call
        self._identity = {
            "agent_session_id": me.get("agent_session_id"),
            "acting_for": me["subject"],
        }
        return self._identity

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool = context.message.name
        arguments = dict(context.message.arguments or {})
        self._sequence += 1
        line: dict[str, Any] = {
            "journal_id": self.journal_id,
            "sequence": self._sequence,
            "tool_call_id": f"tc_{uuid.uuid4().hex}",
            "tool": tool,
            "effect": effect_of(tool, arguments),
            "read_only": tool in self.read_only,
            "arguments_sha256": sha256(arguments),
            "server": "openswmm.mcp",
            "server_version": __version__,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        token, active = CURRENT_CALL.set(line["tool_call_id"]), ACTIVE.set(self)
        try:
            result = await call_next(context)
        except Exception as exc:
            match = _CODE.match(str(exc))
            line |= {
                "ok": False,
                "error": match.group(1) if match else type(exc).__name__,
                "result_sha256": sha256(str(exc)),
            }
            raise
        else:
            line |= {"ok": True, "result_sha256": sha256(_result_json(result))}
            return result
        finally:
            CURRENT_CALL.reset(token)
            ACTIVE.reset(active)
            line["ended_at"] = datetime.now(timezone.utc).isoformat()
            line |= await self._platform_identity(context)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(canonical(line).decode() + "\n")
