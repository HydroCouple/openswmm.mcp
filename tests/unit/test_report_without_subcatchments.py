"""Hydraulic-only models support report snapshots and empty subcatchment statistics."""

from __future__ import annotations

from pathlib import Path

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
