"""Unit tests for the controls MCP tool surface.

Covers rule lifecycle management (count / get / add / clear / list) and the
two runtime-only direct-action tools (set_link_setting / set_link_status).
Mirrors the pattern from ``test_inflows.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):  # noqa: ARG002
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _opened_session(session_manager, inp_path: str, session_id: str = "ctl"):
    """Open the reference .inp via lifecycle.open_model and return the ctx."""
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    return ctx


async def _step_to_running(ctx, session_manager, session_id: str):
    """Advance an opened session into the RUNNING state.

    The set_link_setting / set_link_status tools require state='running'.
    """
    from openswmm_mcp.tools.lifecycle import step_simulation

    # Single step transitions initialized -> running.
    await step_simulation(ctx, session_id=session_id, num_steps=1)


# Sample rule used across several tests. Targets J1 and pumps don't exist
# in site_drainage_example.inp, but the engine accepts the rule text as
# long as it parses — the rule simply never fires.
SAMPLE_RULE = (
    "RULE TEST_R1\n"
    "IF NODE J1 DEPTH > 5.0\n"
    "THEN PUMP P1 STATUS = ON"
)


# ===========================================================================
# Rule management (any non-closed state)
# ===========================================================================


class TestRuleCount:
    async def test_count_zero_on_clean_model(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import count

        ctx = await _opened_session(session_manager, inp_path, "ctl_count")
        result = await count(ctx, session_id="ctl_count")
        assert result["count"] == 0


class TestAddRule:
    async def test_add_rule_increments_count(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, count

        ctx = await _opened_session(session_manager, inp_path, "ctl_add")
        result = await add_rule(ctx, session_id="ctl_add", rule_text=SAMPLE_RULE)
        assert result["status"] == "ok"
        assert result["total_rules"] == 1
        assert result["rule_index"] == 0

        c = await count(ctx, session_id="ctl_add")
        assert c["count"] == 1

    async def test_add_rule_empty_text_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule

        ctx = await _opened_session(session_manager, inp_path, "ctl_empty")
        with pytest.raises(ToolError, match="rule_text must not be empty"):
            await add_rule(ctx, session_id="ctl_empty", rule_text="")

    async def test_add_rule_whitespace_only_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule

        ctx = await _opened_session(session_manager, inp_path, "ctl_ws")
        with pytest.raises(ToolError, match="rule_text must not be empty"):
            await add_rule(ctx, session_id="ctl_ws", rule_text="  \n\t  ")


class TestGetRule:
    async def test_get_rule_roundtrips_text(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, get_rule

        ctx = await _opened_session(session_manager, inp_path, "ctl_get")
        await add_rule(ctx, session_id="ctl_get", rule_text=SAMPLE_RULE)
        result = await get_rule(ctx, session_id="ctl_get", rule_index=0)
        # The engine may normalise whitespace / casing; check key tokens
        # are present rather than full equality.
        assert "RULE" in result["text"].upper()
        assert "TEST_R1" in result["text"].upper()
        assert "DEPTH" in result["text"].upper()


class TestListRules:
    async def test_list_returns_all_rules(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, list_rules

        ctx = await _opened_session(session_manager, inp_path, "ctl_list")
        await add_rule(ctx, session_id="ctl_list", rule_text=SAMPLE_RULE)
        await add_rule(
            ctx,
            session_id="ctl_list",
            rule_text=(
                "RULE TEST_R2\n"
                "IF NODE J1 DEPTH < 1.0\n"
                "THEN PUMP P1 STATUS = OFF"
            ),
        )
        result = await list_rules(ctx, session_id="ctl_list")
        assert result["count"] == 2
        assert {r["index"] for r in result["rules"]} == {0, 1}
        names = [r["text"].upper() for r in result["rules"]]
        assert any("TEST_R1" in n for n in names)
        assert any("TEST_R2" in n for n in names)


class TestClearRules:
    async def test_clear_empties_rule_list(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, clear_rules, count

        ctx = await _opened_session(session_manager, inp_path, "ctl_clear")
        await add_rule(ctx, session_id="ctl_clear", rule_text=SAMPLE_RULE)
        assert (await count(ctx, session_id="ctl_clear"))["count"] == 1

        result = await clear_rules(ctx, session_id="ctl_clear")
        assert result["status"] == "ok"
        assert result["remaining"] == 0
        assert (await count(ctx, session_id="ctl_clear"))["count"] == 0


# ===========================================================================
# Direct control actions (RUNNING state only)
# ===========================================================================


class TestSetLinkSetting:
    async def test_set_link_setting_in_running(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_setting

        ctx = await _opened_session(session_manager, inp_path, "ctl_setting_run")
        await _step_to_running(ctx, session_manager, "ctl_setting_run")

        result = await set_link_setting(
            ctx, session_id="ctl_setting_run", link_id="C1", setting=0.5,
        )
        assert result["status"] == "ok"
        assert result["setting"] == 0.5
        assert result["link_index"] >= 0

    async def test_set_link_setting_rejected_outside_running(
        self, session_manager, inp_path
    ):
        from openswmm_mcp.tools.controls import set_link_setting

        ctx = await _opened_session(session_manager, inp_path, "ctl_setting_op")
        with pytest.raises(ToolError, match="state.*running|requires.*running"):
            await set_link_setting(
                ctx, session_id="ctl_setting_op", link_id="C1", setting=0.5,
            )

    async def test_set_link_setting_unknown_link_raises(
        self, session_manager, inp_path
    ):
        from openswmm_mcp.tools.controls import set_link_setting

        ctx = await _opened_session(session_manager, inp_path, "ctl_setting_bad")
        await _step_to_running(ctx, session_manager, "ctl_setting_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await set_link_setting(
                ctx, session_id="ctl_setting_bad", link_id="NOPE", setting=0.5,
            )


class TestSetLinkStatus:
    async def test_set_link_status_open(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_status

        ctx = await _opened_session(session_manager, inp_path, "ctl_status_open")
        await _step_to_running(ctx, session_manager, "ctl_status_open")

        result = await set_link_status(
            ctx, session_id="ctl_status_open", link_id="C1", open=True,
        )
        assert result["status"] == "ok"
        assert result["open"] is True

    async def test_set_link_status_closed(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_status

        ctx = await _opened_session(session_manager, inp_path, "ctl_status_close")
        await _step_to_running(ctx, session_manager, "ctl_status_close")

        result = await set_link_status(
            ctx, session_id="ctl_status_close", link_id="C1", open=False,
        )
        assert result["open"] is False

    async def test_set_link_status_rejected_outside_running(
        self, session_manager, inp_path
    ):
        from openswmm_mcp.tools.controls import set_link_status

        ctx = await _opened_session(session_manager, inp_path, "ctl_status_op")
        with pytest.raises(ToolError, match="state.*running|requires.*running"):
            await set_link_status(
                ctx, session_id="ctl_status_op", link_id="C1", open=True,
            )


# ===========================================================================
# Backend guard
# ===========================================================================


class TestLegacyGuard:
    async def test_legacy_backend_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import count

        session = await session_manager.create_session(
            session_id="legacy_ctl",
            inp_path=inp_path,
            engine="legacy",
        )
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(session_manager)
        with pytest.raises(ToolError, match="not supported|legacy"):
            await count(ctx, session_id="legacy_ctl")
