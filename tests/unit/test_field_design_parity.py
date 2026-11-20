"""A gymnasium FieldDesign write is the same write as MCP ``set`` (ecosystem plan 2.1).

Both reach the engine through the catalog path, so the field reads back the same and the
engine writes the same ``.inp``. Files land in ``tests/_output/test_field_design_parity/``.
"""

from __future__ import annotations

import shutil

import numpy as np
import pytest

pytest.importorskip("openswmm_gymnasium")

from openswmm_gymnasium._engine import SolverAdapter  # noqa: E402
from openswmm_gymnasium.spaces.design import FieldDesign  # noqa: E402

CHANGES = [
    ("subcatchment", "subcatchment.width", "S1", "width", 321.5),
    ("node", "node.invert_elev", "J3", "invert_elev", 101.25),
    ("link", "link.barrels", "C2", "barrels", 2),
]


async def test_a_field_design_writes_what_set_writes(tools, inp_path, output_dir):
    await tools("open_model", path=inp_path, session_id="p")
    for kind, _path, element, field, value in CHANGES:
        result = await tools(
            "set",
            session_id="p",
            kind=kind,
            changes=[{"id": element, "field": field, "value": value}],
        )
        assert result["applied"] == 1, result
    by_set = output_dir / "by_set.inp"
    await tools("save", session_id="p", path=str(by_set))

    source = output_dir / "design_source.inp"
    shutil.copy(inp_path, source)
    adapter = SolverAdapter(source, output_dir / "design.rpt", output_dir / "design.out")
    adapter.open()
    try:
        for kind, path, element, _field, value in CHANGES:
            factory = FieldDesign(path, [element], low=0.0, high=1e6)
            factory.bind(adapter)
            factory.apply(adapter, np.array([value], dtype=np.float64))
            got = await tools("get", session_id="p", kind=kind, fields=[_field], ids=[element])
            assert adapter.read(path, adapter.index(kind, element)) == got["columns"][_field][0]
        by_design = output_dir / "by_design.inp"
        adapter.solver.write(str(by_design))
    finally:
        adapter.close()
    assert by_design.read_text() == by_set.read_text()
