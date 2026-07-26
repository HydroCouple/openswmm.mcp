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
SAMPLE_RULE = "RULE TEST_R1\nIF NODE J1 DEPTH > 5.0\nTHEN PUMP P1 STATUS = ON"


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
            rule_text=("RULE TEST_R2\nIF NODE J1 DEPTH < 1.0\nTHEN PUMP P1 STATUS = OFF"),
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
# remove_rule — delete a single rule by index (BUILDING / OPENED state)
# ===========================================================================


async def _lenient_opened_session(session_manager, inp_path: str, session_id: str):
    """Open leniently so the session stays in the editable 'opened' state.

    ``remove_rule`` wraps the C API which requires BUILDING / OPENED — a
    strict open lands in 'initialized', which the engine rejects.
    """
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id, lenient_open=True)
    return ctx


class TestRemoveRule:
    async def test_remove_rule_drops_one(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, count, remove_rule

        ctx = await _lenient_opened_session(session_manager, inp_path, "ctl_rm")
        await add_rule(ctx, session_id="ctl_rm", rule_text=SAMPLE_RULE)
        await add_rule(
            ctx,
            session_id="ctl_rm",
            rule_text="RULE R2\nIF NODE J1 DEPTH < 1\nTHEN CONDUIT C1 STATUS = CLOSED",
        )
        assert (await count(ctx, session_id="ctl_rm"))["count"] == 2

        result = await remove_rule(ctx, session_id="ctl_rm", rule_index=0)
        assert result["status"] == "ok"
        assert result["rule_index"] == 0
        assert result["remaining"] == 1
        assert (await count(ctx, session_id="ctl_rm"))["count"] == 1

    async def test_remove_rule_out_of_range_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import remove_rule

        ctx = await _lenient_opened_session(session_manager, inp_path, "ctl_rm_oor")
        with pytest.raises(ToolError, match="out of range"):
            await remove_rule(ctx, session_id="ctl_rm_oor", rule_index=5)


# ===========================================================================
# find_references — locate rules referencing an object by name (read-only)
# ===========================================================================


class TestFindReferences:
    async def test_find_references_locates_rule(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, find_references

        ctx = await _opened_session(session_manager, inp_path, "ctl_fr")
        await add_rule(
            ctx,
            session_id="ctl_fr",
            rule_text="RULE R_C1\nIF NODE J1 DEPTH > 5\nTHEN CONDUIT C1 STATUS = CLOSED",
        )
        result = await find_references(ctx, session_id="ctl_fr", object_name="C1")
        assert result["object_name"] == "C1"
        assert 0 in result["rule_indices"]
        assert result["count"] == len(result["rule_indices"])
        assert result["count"] >= 1

    async def test_find_references_none_for_unreferenced(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, find_references

        ctx = await _opened_session(session_manager, inp_path, "ctl_fr_none")
        await add_rule(ctx, session_id="ctl_fr_none", rule_text=SAMPLE_RULE)
        result = await find_references(
            ctx, session_id="ctl_fr_none", object_name="NO_SUCH_OBJECT"
        )
        assert result["rule_indices"] == []
        assert result["count"] == 0

    async def test_find_references_empty_name_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import find_references

        ctx = await _opened_session(session_manager, inp_path, "ctl_fr_empty")
        with pytest.raises(ToolError, match="object_name must not be empty"):
            await find_references(ctx, session_id="ctl_fr_empty", object_name="")


# ===========================================================================
# Direct control actions (RUNNING state only)
# ===========================================================================


class TestSetLinkSetting:
    async def test_set_link_setting_in_running(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_setting

        ctx = await _opened_session(session_manager, inp_path, "ctl_setting_run")
        await _step_to_running(ctx, session_manager, "ctl_setting_run")

        result = await set_link_setting(
            ctx,
            session_id="ctl_setting_run",
            link_id="C1",
            setting=0.5,
        )
        assert result["status"] == "ok"
        assert result["setting"] == 0.5
        assert result["link_index"] >= 0

    async def test_set_link_setting_rejected_outside_running(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_setting

        ctx = await _opened_session(session_manager, inp_path, "ctl_setting_op")
        with pytest.raises(ToolError, match="state.*running|requires.*running"):
            await set_link_setting(
                ctx,
                session_id="ctl_setting_op",
                link_id="C1",
                setting=0.5,
            )

    async def test_set_link_setting_unknown_link_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_setting

        ctx = await _opened_session(session_manager, inp_path, "ctl_setting_bad")
        await _step_to_running(ctx, session_manager, "ctl_setting_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await set_link_setting(
                ctx,
                session_id="ctl_setting_bad",
                link_id="NOPE",
                setting=0.5,
            )


class TestSetLinkStatus:
    async def test_set_link_status_open(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_status

        ctx = await _opened_session(session_manager, inp_path, "ctl_status_open")
        await _step_to_running(ctx, session_manager, "ctl_status_open")

        result = await set_link_status(
            ctx,
            session_id="ctl_status_open",
            link_id="C1",
            open=True,
        )
        assert result["status"] == "ok"
        assert result["open"] is True

    async def test_set_link_status_closed(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_status

        ctx = await _opened_session(session_manager, inp_path, "ctl_status_close")
        await _step_to_running(ctx, session_manager, "ctl_status_close")

        result = await set_link_status(
            ctx,
            session_id="ctl_status_close",
            link_id="C1",
            open=False,
        )
        assert result["open"] is False

    async def test_set_link_status_rejected_outside_running(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import set_link_status

        ctx = await _opened_session(session_manager, inp_path, "ctl_status_op")
        with pytest.raises(ToolError, match="state.*running|requires.*running"):
            await set_link_status(
                ctx,
                session_id="ctl_status_op",
                link_id="C1",
                open=True,
            )


# ===========================================================================
# DA-ENG-02 — get_id (rule-name extraction)
# ===========================================================================


class TestGetId:
    """``get_id`` parses the canonical rule name from the stored text."""

    async def test_canonical_name(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, get_id

        ctx = await _opened_session(session_manager, inp_path, "ctl_id_canonical")
        await add_rule(
            ctx,
            session_id="ctl_id_canonical",
            rule_text=("RULE PumpOnHigh\nIF NODE J1 DEPTH > 5\nTHEN PUMP P1 STATUS = ON"),
        )
        result = await get_id(ctx, session_id="ctl_id_canonical", rule_index=0)
        assert result["name"] == "PumpOnHigh"
        assert result["rule_index"] == 0

    async def test_lowercase_and_mixed_case_keyword(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, get_id

        ctx = await _opened_session(session_manager, inp_path, "ctl_id_case")
        await add_rule(
            ctx,
            session_id="ctl_id_case",
            rule_text="rule WeirBypass\nIF NODE J1 DEPTH < 1\nTHEN PUMP P1 STATUS = OFF",
        )
        await add_rule(
            ctx,
            session_id="ctl_id_case",
            rule_text="Rule TankFill\nIF NODE J1 DEPTH < 2\nTHEN PUMP P1 STATUS = ON",
        )
        a = await get_id(ctx, session_id="ctl_id_case", rule_index=0)
        b = await get_id(ctx, session_id="ctl_id_case", rule_index=1)
        assert a["name"] == "WeirBypass"
        assert b["name"] == "TankFill"

    async def test_malformed_rule_returns_none(self, session_manager, inp_path):
        """Rules with no parseable RULE keyword surface ``name=None``
        so callers can render a sentinel display label."""
        from openswmm_mcp.tools.controls import add_rule, get_id

        ctx = await _opened_session(session_manager, inp_path, "ctl_id_bad")
        # No RULE keyword at all — but engine still accepts the text.
        await add_rule(
            ctx,
            session_id="ctl_id_bad",
            rule_text="IF NODE J1 DEPTH > 5\nTHEN PUMP P1 STATUS = ON",
        )
        result = await get_id(ctx, session_id="ctl_id_bad", rule_index=0)
        assert result["name"] is None


class TestListRulesWithNames:
    """``list_rules`` now includes the parsed ``name`` per rule (DA.1)."""

    async def test_list_includes_name_field(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import add_rule, list_rules

        ctx = await _opened_session(session_manager, inp_path, "ctl_list_named")
        await add_rule(
            ctx,
            session_id="ctl_list_named",
            rule_text="RULE Pump_A\nIF NODE J1 DEPTH > 5\nTHEN PUMP P1 STATUS = ON",
        )
        await add_rule(
            ctx,
            session_id="ctl_list_named",
            rule_text="rule Weir_B\nIF NODE J1 DEPTH < 1\nTHEN PUMP P1 STATUS = OFF",
        )
        # Malformed rule — name should come back as None.
        await add_rule(
            ctx,
            session_id="ctl_list_named",
            rule_text="IF NODE J2 DEPTH > 5\nTHEN PUMP P2 STATUS = ON",
        )

        result = await list_rules(ctx, session_id="ctl_list_named")
        assert result["count"] == 3
        assert result["rules"][0]["name"] == "Pump_A"
        assert result["rules"][1]["name"] == "Weir_B"
        assert result["rules"][2]["name"] is None
        # The text field is still present alongside the name.
        for row in result["rules"]:
            assert "text" in row
            assert "index" in row


# ===========================================================================
# validate_rule — pre-flight rule validation without adding it
# ===========================================================================


class TestValidateRule:
    async def test_valid_rule_reports_valid(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import count, validate_rule

        ctx = await _opened_session(session_manager, inp_path, "ctl_val_ok")
        # validate_rule resolves element references (unlike add_rule, which
        # just stores the text), so the rule must reference elements that
        # exist in the reference model: node J1 and conduit C1 (no pumps).
        result = await validate_rule(
            ctx,
            session_id="ctl_val_ok",
            rule_text="RULE OK_R1\nIF NODE J1 DEPTH > 5.0\nTHEN CONDUIT C1 STATUS = CLOSED",
        )
        assert result["valid"] is True
        assert "message" in result
        # Validation must NOT add the rule to the model.
        assert (await count(ctx, session_id="ctl_val_ok"))["count"] == 0

    async def test_invalid_rule_reports_message(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import validate_rule

        ctx = await _opened_session(session_manager, inp_path, "ctl_val_bad")
        result = await validate_rule(
            ctx,
            session_id="ctl_val_bad",
            rule_text="RULE BAD\nIF NODE J1 BANANA > 5\nTHEN GLORP",
        )
        assert result["valid"] is False
        assert result["message"]

    async def test_empty_text_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.controls import validate_rule

        ctx = await _opened_session(session_manager, inp_path, "ctl_val_empty")
        with pytest.raises(ToolError, match="rule_text must not be empty"):
            await validate_rule(ctx, session_id="ctl_val_empty", rule_text="  ")


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
