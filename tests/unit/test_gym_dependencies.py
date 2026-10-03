"""Unit tests for the require_gymnasium dependency guard (plan Phase 1)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastmcp")  # dependencies.py imports fastmcp at module level


def _gym_installed() -> bool:
    try:
        import openswmm_gymnasium  # noqa: F401

        return True
    except ImportError:
        return False


def test_require_gymnasium_passes_when_installed():
    if not _gym_installed():
        pytest.skip("openswmm.gymnasium not installed")
    from openswmm_mcp.dependencies import require_gymnasium

    require_gymnasium("test feature")  # must not raise


def test_require_gymnasium_raises_actionable_error_when_missing():
    if _gym_installed():
        pytest.skip("openswmm.gymnasium installed; missing-path not testable")
    from openswmm_mcp.dependencies import require_gymnasium
    from openswmm_mcp.errors import ToolError

    with pytest.raises(ToolError) as excinfo:
        require_gymnasium("gym_run_episode")
    msg = str(excinfo.value)
    assert "DEPENDENCY_MISSING" in msg
    assert "gym_run_episode" in msg
    assert "openswmm.mcp[gym]" in msg  # actionable install hint
