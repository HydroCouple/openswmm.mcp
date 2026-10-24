"""Engine surfaces added since the 2026-10-03 sync, through the generic tools.

* ``report`` reads the 2D services of a meshed model under their current
  catalog names.

Real engine, no mocks; output under ``tests/_output/`` (CLAUDE.md §4.1).
"""

from __future__ import annotations

import shutil

DATA_2D = "twod_example.inp"


async def _open_2d(tools, output_dir, session_id="two"):
    from tests.unit.conftest import DATA

    inp = output_dir / DATA_2D
    shutil.copy(DATA / DATA_2D, inp)
    await tools("open_model", path=str(inp), session_id=session_id)
    return session_id


async def test_2d_reports_on_a_meshed_model(tools, output_dir):
    session = await _open_2d(tools, output_dir, "two_report")
    await tools("run", session_id=session)
    for name in ("2d", "groundwater"):
        report = await tools("report", session_id=session, name=name)
        assert report["active"] and report["values"], name
