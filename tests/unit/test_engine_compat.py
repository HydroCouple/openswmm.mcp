"""Workarounds for engine bugs fixed upstream but not yet released (``_util/engine_compat``)."""

from __future__ import annotations

from pathlib import Path

from openswmm_mcp._util.engine_compat import report_snapshot

DATA = Path(__file__).parent / "data"
_SUBCATCHMENT_SECTIONS = {
    "SUBCATCHMENTS",
    "SUBAREAS",
    "INFILTRATION",
    "POLYGONS",
    "COVERAGES",
    "LOADINGS",
}


def _without_subcatchments(folder: Path) -> str:
    lines, keep = [], True
    for line in (DATA / "site_drainage_model.inp").read_text().splitlines():
        if line.startswith("["):
            keep = line.strip("[] ").upper() not in _SUBCATCHMENT_SECTIONS
        if keep:
            lines.append(line)
    path = folder / "nosub.inp"
    path.write_text("\n".join(lines) + "\n")
    return str(path)


async def test_report_on_a_model_without_subcatchments(tools, output_dir):
    await tools("open_model", path=_without_subcatchments(output_dir), session_id="nosub")
    assert (await tools("run", session_id="nosub"))["finished"]
    mass = await tools("report", session_id="nosub", name="mass_balance")
    assert mass["routing_continuity"]["continuity_error_pct"] is not None
    subs = await tools("report", session_id="nosub", name="subcatchments")
    assert subs["rows"] == []
    stats = await tools("report", session_id="nosub", name="statistics", kind="subcatchment")
    assert stats["rows"] == []


async def test_snapshot_unchanged_when_subcatchments_exist(session_manager, inp_path):
    """On a normal model the workaround returns exactly the engine's own snapshot."""
    from openswmm.engine import get_report_snapshot

    from openswmm_mcp import catalog as cat

    from .test_ledger_facts import _finish, _open

    session = await _open(session_manager, Path(inp_path).parent)
    await session.call(_finish, session)
    solver = session.require_solver()

    def _both():
        return cat.to_json(report_snapshot(solver)), cat.to_json(get_report_snapshot(solver))

    ours, engine = await session.call(_both)
    assert isinstance(ours, dict) and ours["routing_continuity"]["total_outflow"] > 0
    assert ours == engine
