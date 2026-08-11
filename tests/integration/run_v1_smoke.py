"""Standalone v1 migration smoke runner.

Runs end-to-end against the real ``openswmm.engine`` and dumps a JSON
report to ``tests/integration/reports/v1_smoke_<timestamp>.json``.
This is the **Phase 5 verification artefact** from
``docs/developer/V1_MIGRATION_PLAN.md`` §7 — it gives you a single file
you can open after a run to see whether every migrated tool surface
behaves correctly against your local engine wheel.

Usage::

    cd openswmm.mcp
    python tests/integration/run_v1_smoke.py

Optional arguments::

    --inp PATH        Path to an .inp file (default: in-tree
                      tests/data/site_drainage_example.inp).
    --report PATH     Where to write the JSON report (default: a
                      timestamped file under tests/integration/reports/).
    --engine NAME     ``openswmm`` (default) or ``legacy`` to drive the
                      v1-facade legacy adapter end to end.

Per ``CLAUDE.md`` §4.1, the report is written under the repo so the user
can open it after the run completes; it is never placed in a temp dir.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INP = REPO_ROOT / "tests" / "data" / "site_drainage_example.inp"
DEFAULT_REPORT_DIR = REPO_ROOT / "tests" / "integration" / "reports"

# Make ``openswmm_mcp`` importable when this script is run directly.
sys.path.insert(0, str(REPO_ROOT / "src"))


# ---------------------------------------------------------------------------
# Report record
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """One smoke-check entry."""

    name: str
    module: str
    status: str  # "pass" | "fail" | "skipped"
    detail: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# ---------------------------------------------------------------------------
# Context double
# ---------------------------------------------------------------------------


def _make_ctx(session_manager) -> Any:
    """Build a minimal Context double for direct tool invocation."""
    ctx = MagicMock()
    ctx.lifespan_context = {"session_manager": session_manager}

    async def _report_progress(value: int, total: int) -> None:
        return None

    ctx.report_progress = _report_progress
    return ctx


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


async def _check_nodes(session, ctx) -> CheckResult:
    """Phase 2: v1 container protocol on nodes."""
    nodes = session.nodes
    n = len(nodes)
    first = nodes[0]
    return CheckResult(
        name="nodes.container_protocol",
        module="tools/nodes.py",
        status="pass",
        detail={"node_count": n, "first_id": first.id, "first_depth": float(first.depth)},
    )


async def _check_links(session, ctx) -> CheckResult:
    """Phase 2: v1 property access on link wrapper."""
    links = session.links
    n = len(links)
    first = links[0]
    return CheckResult(
        name="links.property_access",
        module="tools/links.py",
        status="pass",
        detail={
            "link_count": n,
            "first_id": first.id,
            "from_node_id": first.from_node.id,
            "to_node_id": first.to_node.id,
            "first_flow": float(first.flow),
        },
    )


async def _check_subcatchments(session, ctx) -> CheckResult:
    """Phase 2: subcatchments collection len + property access."""
    subs = session.subcatchments
    n = len(subs)
    if n == 0:
        return CheckResult(
            name="subcatchments.property_access",
            module="tools/subcatchments.py",
            status="skipped",
            detail={"reason": "model has no subcatchments"},
        )
    first = subs[0]
    return CheckResult(
        name="subcatchments.property_access",
        module="tools/subcatchments.py",
        status="pass",
        detail={"subcatchment_count": n, "first_id": first.id, "first_area": float(first.area)},
    )


async def _check_query(session, ctx) -> CheckResult:
    """Phase 2: query.get_system_summary."""
    from openswmm_mcp.tools.query import get_system_summary

    result = await get_system_summary(ctx, session_id="v1_smoke")
    return CheckResult(
        name="query.get_system_summary",
        module="tools/query.py",
        status="pass",
        detail={
            "node_count": result.node_count,
            "link_count": result.link_count,
            "subcatchment_count": result.subcatchment_count,
            "flow_units": result.flow_units,
            "route_model": result.route_model,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "routing_step": result.routing_step,
        },
    )


async def _check_lifecycle_stride(session, ctx) -> CheckResult:
    """Phase 4: Solver.stride convenience."""
    from openswmm_mcp.tools.lifecycle import stride

    result = await stride(ctx, session_id="v1_smoke", num_steps=5)
    return CheckResult(
        name="lifecycle.stride",
        module="tools/lifecycle.py (Phase 4)",
        status="pass",
        detail={
            "steps_taken": result.steps_taken,
            "current_time_days": result.current_time,
            "elapsed_days": result.elapsed,
            "completed": result.completed,
        },
    )


async def _check_lifecycle_until_elapsed(session, ctx) -> CheckResult:
    """Phase 4: Solver.until(timedelta) convenience."""
    from openswmm_mcp.tools.lifecycle import until_elapsed

    result = await until_elapsed(ctx, session_id="v1_smoke", seconds=300.0)
    return CheckResult(
        name="lifecycle.until_elapsed",
        module="tools/lifecycle.py (Phase 4)",
        status="pass",
        detail={
            "current_time_days": result.current_time,
            "elapsed_days": result.elapsed,
            "completed": result.completed,
        },
    )


async def _check_model_options(session, ctx) -> CheckResult:
    """Phase 3a: model.get_option via the v1 options mapping."""
    from openswmm_mcp.tools.model import get_option

    result = await get_option(ctx, session_id="v1_smoke", key="FLOW_UNITS")
    return CheckResult(
        name="model.get_option",
        module="tools/model.py",
        status="pass",
        detail=result,
    )


async def _check_analysis_statistics(session, ctx) -> CheckResult:
    """Phase 3f: analysis.get_statistics uses v1 stats arrays."""
    from openswmm_mcp.tools.analysis import get_statistics

    first_node_id = session.nodes.get_id(0)
    result = await get_statistics(
        ctx,
        session_id="v1_smoke",
        element_type="node",
        element_id=first_node_id,
    )
    return CheckResult(
        name="analysis.get_statistics",
        module="tools/analysis.py",
        status="pass",
        detail=result,
    )


async def _check_mass_balance(session, ctx) -> CheckResult:
    """Phase 1+2: mass_balance property access."""
    mb = session.mass_balance
    return CheckResult(
        name="mass_balance.continuity_errors",
        module="tools/lifecycle.py + backends/legacy.py",
        status="pass",
        detail={
            "runoff_continuity_error": float(mb.runoff_continuity_error),
            "routing_continuity_error": float(mb.routing_continuity_error),
        },
    )


async def _check_forcing_persistent(session, ctx) -> CheckResult:
    """Phase 4: set_persistent_forcing convenience."""
    from openswmm_mcp.tools.forcing import set_persistent_forcing

    first_node_id = session.nodes.get_id(0)
    result = await set_persistent_forcing(
        ctx,
        session_id="v1_smoke",
        target_type="node",
        element_id=first_node_id,
        variable="lateral_inflow",
        value=0.0,
        mode="replace",
    )
    return CheckResult(
        name="forcing.set_persistent_forcing",
        module="tools/forcing.py (Phase 4)",
        status="pass",
        detail={
            "status": result.status,
            "target_type": result.target_type,
            "element_id": result.element_id,
            "variable": result.variable,
            "persist": result.persist,
        },
    )


async def _check_stale_object_mapping() -> CheckResult:
    """Phase 4: StaleObjectError → ToolError translation helper is present."""
    from openswmm_mcp.errors import (
        ErrorCode,
        raise_stale_object_as_tool_error,
        translate_stale_object,
    )

    assert ErrorCode.STALE_OBJECT == "STALE_OBJECT"
    # Non-stale exception passes through.
    assert translate_stale_object(ValueError("x")) is None
    # Helper doesn't raise for non-stale errors.
    raise_stale_object_as_tool_error(ValueError("x"))
    return CheckResult(
        name="errors.stale_object_mapping",
        module="errors.py (Phase 4)",
        status="pass",
        detail={"error_code": ErrorCode.STALE_OBJECT},
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


CHECKS: list[tuple[str, Any]] = [
    ("nodes", _check_nodes),
    ("links", _check_links),
    ("subcatchments", _check_subcatchments),
    ("query", _check_query),
    ("model", _check_model_options),
    ("lifecycle.stride", _check_lifecycle_stride),
    ("lifecycle.until_elapsed", _check_lifecycle_until_elapsed),
    ("forcing.persistent", _check_forcing_persistent),
    ("analysis", _check_analysis_statistics),
    ("mass_balance", _check_mass_balance),
]


async def _run_all(inp_path: Path, engine: str, working_dir: Path) -> dict[str, Any]:
    """Run every check and return a full report dict."""
    from openswmm_mcp.session import SessionManager

    sm = SessionManager(max_sessions=2, working_dir=str(working_dir))
    session = await sm.create_session(
        "v1_smoke",
        str(inp_path),
        rpt_path=str(working_dir / "v1_smoke.rpt"),
        out_path=str(working_dir / "v1_smoke.out"),
        engine=engine,
    )
    session.solver.open()
    session.solver.initialize()
    session.state = "initialized"
    session.solver.start()
    session.state = "running"

    ctx = _make_ctx(sm)

    results: list[CheckResult] = []

    # Stale-object check doesn't need the session.
    try:
        results.append(await _check_stale_object_mapping())
    except Exception as exc:
        results.append(
            CheckResult(
                name="errors.stale_object_mapping",
                module="errors.py (Phase 4)",
                status="fail",
                error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            )
        )

    for label, check_fn in CHECKS:
        try:
            result = await check_fn(session, ctx)
            results.append(result)
            print(f"  {result.status.upper():7s} {label}")
        except Exception as exc:
            results.append(
                CheckResult(
                    name=label,
                    module="<unknown>",
                    status="fail",
                    error=f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                )
            )
            print(f"  FAIL    {label}: {exc}")

    try:
        await sm.close_session("v1_smoke")
    except Exception:
        pass

    pass_count = sum(1 for r in results if r.status == "pass")
    fail_count = sum(1 for r in results if r.status == "fail")
    skip_count = sum(1 for r in results if r.status == "skipped")
    return {
        "schema_version": 1,
        "run_at": datetime.utcnow().isoformat() + "Z",
        "inp_path": str(inp_path),
        "engine": engine,
        "summary": {
            "total": len(results),
            "pass": pass_count,
            "fail": fail_count,
            "skipped": skip_count,
            "exit_code": 0 if fail_count == 0 else 1,
        },
        "checks": [
            {
                "name": r.name,
                "module": r.module,
                "status": r.status,
                "detail": r.detail,
                "error": r.error,
            }
            for r in results
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="v1 migration smoke runner")
    parser.add_argument(
        "--inp",
        type=Path,
        default=DEFAULT_INP,
        help="Path to the .inp file (default: tests/data/site_drainage_example.inp).",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help=(
            "Path for the JSON report.  Defaults to a timestamped file "
            "under tests/integration/reports/."
        ),
    )
    parser.add_argument(
        "--engine",
        choices=("openswmm", "legacy"),
        default="openswmm",
        help="Backend to drive (default: openswmm).",
    )
    args = parser.parse_args()

    if not args.inp.exists():
        print(f"ERROR: .inp file not found: {args.inp}", file=sys.stderr)
        return 2

    DEFAULT_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    if args.report is None:
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        args.report = DEFAULT_REPORT_DIR / f"v1_smoke_{args.engine}_{ts}.json"
    else:
        args.report = Path(args.report)
        args.report.parent.mkdir(parents=True, exist_ok=True)

    working_dir = DEFAULT_REPORT_DIR / "_workdir"
    working_dir.mkdir(parents=True, exist_ok=True)

    print(f"Running v1 smoke against: {args.inp}")
    print(f"Backend: {args.engine}")
    print(f"Report:  {args.report}")
    print()

    report = asyncio.run(_run_all(args.inp, args.engine, working_dir))

    args.report.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    s = report["summary"]
    print()
    print(f"Done: {s['pass']}/{s['total']} passed, {s['fail']} failed, {s['skipped']} skipped.")
    print(f"Report written to: {args.report}")
    return s["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
