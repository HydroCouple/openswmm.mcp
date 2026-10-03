"""Opt-in ``run_python`` tool: Python with a session's Solver in scope.

Registered only when ``OPENSWMM_MCP_ENABLE_PYTHON=true`` and the transport is
stdio. It executes arbitrary code with the server's permissions, so it is an
escape hatch for a local user, never for a shared server.
"""

from __future__ import annotations

import contextlib
import io
from typing import Any

import numpy as np
import openswmm.engine as engine
from fastmcp import Context

from openswmm_mcp import catalog as cat
from openswmm_mcp.dependencies import get_session


async def run_python(ctx: Context, session_id: str, code: str) -> dict:
    """Run Python against a session. In scope: solver (openswmm.engine.Solver), engine
    (the openswmm.engine module), catalog, np. Assign to `result` to return a value;
    printed output is returned too. Runs with the server's permissions."""
    session = await get_session(ctx, session_id)
    solver = session.require_solver()

    def _run() -> dict[str, Any]:
        scope: dict[str, Any] = {
            "solver": solver,
            "engine": engine,
            "np": np,
            "catalog": engine.catalog,
            "result": None,
        }
        stdout = io.StringIO()
        session.structure_edited = True  # the code may change anything, even if it then fails
        with contextlib.redirect_stdout(stdout):
            exec(compile(code, "<run_python>", "exec"), scope)  # noqa: S102 - opt-in by design
        return {"result": cat.to_json(scope.get("result")), "stdout": stdout.getvalue()[-20000:]}

    return await session.call(_run, context="run_python")
