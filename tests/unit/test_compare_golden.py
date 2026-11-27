"""compare reports the numbers it reported before its fit metrics moved to gymnasium (plan 3.3).

``data/compare_golden.json`` was written by the MCP's own metric code at e935393, before the
move, from ``data/compare_observed.csv`` and a run of ``site_drainage_model.inp``. The same
comparison must give the same values; KGE is new.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).parent / "data"


async def test_compare_reports_the_numbers_it_reported_before(tools, inp_path):
    golden = json.loads((DATA / "compare_golden.json").read_text())
    await tools("open_model", path=inp_path, session_id="g")
    await tools("run", session_id="g")
    result = await tools(
        "compare", a=str(DATA / "compare_observed.csv"), b="g", kind="link", variable="flow"
    )
    rows = result["rows"]
    assert [r["id"] for r in rows] == [r["id"] for r in golden]
    for row, before in zip(rows, golden, strict=True):
        assert {k: v for k, v in row.items() if k != "kge"} == before
        assert -1.0 < row["kge"] < 1.0
