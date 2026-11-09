"""Validate native-peer signatures before rejecting non-JSON Python objects."""

import pytest
from fastmcp.exceptions import ToolError


@pytest.mark.parametrize(
    "target,method,missing",
    [
        ("heat", "edit_atomic", "callback"),
        ("solver", "set_phase_callback", "callback"),
        ("split", "set_callback", "callback"),
        ("split_frame", "node", "index"),
        ("split_frame", "source", "index"),
        ("split_frame", "outfall_return", "subcatchment"),
    ],
)
async def test_peer_missing_arguments_are_named(tools, inp_path, target, method, missing):
    await tools("open_model", path=inp_path, session_id="peer")
    with pytest.raises(ToolError, match=f"Missing argument '{missing}'"):
        await tools("call", session_id="peer", target=target, method=method)


@pytest.mark.parametrize("target", ["heat", "solver", "split"])
async def test_python_callbacks_are_still_refused(tools, inp_path, target):
    await tools("open_model", path=inp_path, session_id="peer")
    method = {"heat": "edit_atomic", "solver": "set_phase_callback", "split": "set_callback"}[
        target
    ]
    with pytest.raises(ToolError, match="NOT_SUPPORTED.*Python callback"):
        await tools(
            "call",
            session_id="peer",
            target=target,
            method=method,
            args={"callback": "not-a-python-callable"},
        )


@pytest.mark.parametrize("method,args", [("close", {}), ("node", {"index": 0})])
async def test_native_frames_do_not_request_a_file_path(tools, inp_path, method, args):
    await tools("open_model", path=inp_path, session_id="peer")
    with pytest.raises(ToolError, match="NOT_SUPPORTED.*Native split frames"):
        await tools("call", session_id="peer", target="split_frame", method=method, args=args)
