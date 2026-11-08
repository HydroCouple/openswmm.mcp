"""HDF5 object names stay distinct from filesystem names on every platform."""

from __future__ import annotations

import shutil
from pathlib import PureWindowsPath

import pytest

from openswmm_mcp import catalog as cat
from openswmm_mcp.session import SimSession
from openswmm_mcp.tools.access import _bind

from .conftest import DATA


class _WindowsPath(PureWindowsPath):
    """Use Windows drive/root rules without accessing a Windows filesystem."""

    def expanduser(self):
        return self

    def resolve(self):
        return self


@pytest.fixture(params=["host", "windows"])
def path_base(request, output_dir, monkeypatch):
    if request.param == "windows":
        monkeypatch.setattr(cat, "Path", _WindowsPath)
        return _WindowsPath("D:/mcp-work")
    return output_dir


@pytest.mark.parametrize("method", ["attributes", "shape", "read", "strings", "unlimited_axes"])
@pytest.mark.parametrize("object_path", ["/", "/Mesh2_face_depth", "Mesh2_face_depth"])
def test_hdf5_object_paths_pass_through(method, object_path, path_base):
    session = SimSession("objects", path_base)
    positional, keywords = _bind(
        cat.members("surface_output")[method], {"path": object_path}, session, path_base
    )
    assert positional == [object_path]
    assert keywords == {}


def test_filesystem_paths_still_resolve_from_the_working_directory(path_base):
    session = SimSession("files", path_base)
    positional, keywords = _bind(
        cat.members("builder")["write"], {"path": "model.inp"}, session, path_base
    )
    assert positional == [str(path_base / "model.inp")]
    assert keywords == {}


async def test_relative_and_absolute_hdf5_names_read_the_same_session_output(tools, output_dir):
    inp = output_dir / "surface.inp"
    shutil.copy(DATA / "twod_example.inp", inp)
    await tools("open_model", path=str(inp), session_id="objects")
    await tools("run", session_id="objects")
    for method, extra in [("shape", {}), ("read", {"selection": 0}), ("unlimited_axes", {})]:
        results = []
        for path in ["/Mesh2_face_depth", "Mesh2_face_depth"]:
            result = await tools(
                "call",
                session_id="objects",
                target="surface_output",
                method=method,
                args={"path": path, **extra},
            )
            results.append(result["result"])
        assert results[0] == results[1], method
    attributes = await tools(
        "call",
        session_id="objects",
        target="surface_output",
        method="attributes",
        args={"path": "/"},
    )
    assert "Conventions" in attributes["result"]
