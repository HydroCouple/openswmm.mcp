"""Unit tests for the inflows MCP tool surface.

Covers [INFLOWS], [DWF], [RDII], [HYDROGRAPHS], and [RDII_DECAY] design-time
configuration. Mirrors the pattern from ``test_tables.py``: ``MockContext`` +
``session_manager`` fixture, with sessions either opened from the real
``site_drainage_example.inp`` or constructed via ``building.create_model``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openswmm.engine")

from openswmm_mcp.errors import ToolError

# ---------------------------------------------------------------------------
# Mock MCP Context (mirrors test_tables / test_building)
# ---------------------------------------------------------------------------


class MockContext:
    def __init__(self, session_manager):
        self.lifespan_context = {"session_manager": session_manager}

    async def report_progress(self, current, total):  # noqa: ARG002
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _opened_session(session_manager, inp_path: str, session_id: str = "inf"):
    """Open the reference .inp via lifecycle.open_model and return the ctx."""
    from openswmm_mcp.tools.lifecycle import open_model

    ctx = MockContext(session_manager)
    await open_model(ctx, inp_path=inp_path, session_id=session_id)
    return ctx


# ===========================================================================
# Read-only counts on a freshly-opened reference model (0 across the board)
# ===========================================================================


class TestCounts:
    async def test_all_counts_zero_on_clean_model(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            dwf_count,
            ext_inflow_count,
            hydrograph_count,
            hydrograph_gage_count,
            rdii_count,
            rdii_decay_count,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_counts")
        for tool in (
            ext_inflow_count,
            dwf_count,
            rdii_count,
            hydrograph_count,
            hydrograph_gage_count,
            rdii_decay_count,
        ):
            result = await tool(ctx, session_id="inf_counts")
            assert result["count"] == 0


# ===========================================================================
# [INFLOWS] add_external
# ===========================================================================


class TestExternalInflow:
    async def test_add_external_by_node_id(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_external, ext_inflow_count

        ctx = await _opened_session(session_manager, inp_path, "inf_ext")
        result = await add_external(
            ctx,
            session_id="inf_ext",
            node_id="J1",
            constituent="FLOW",
        )
        assert result["status"] == "ok"
        assert result["node_id"] == "J1"
        assert result["node_index"] >= 0

        c = await ext_inflow_count(ctx, session_id="inf_ext")
        assert c["count"] == 1

    async def test_add_external_unknown_node_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_external

        ctx = await _opened_session(session_manager, inp_path, "inf_ext_bad")
        with pytest.raises(ToolError, match="ELEMENT_NOT_FOUND|not found"):
            await add_external(
                ctx,
                session_id="inf_ext_bad",
                node_id="NOPE",
                constituent="FLOW",
            )

    async def test_add_external_empty_node_id_raises(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_external

        ctx = await _opened_session(session_manager, inp_path, "inf_ext_empty")
        with pytest.raises(ToolError, match="node_id must not be empty"):
            await add_external(ctx, session_id="inf_ext_empty", node_id="")


# ===========================================================================
# [DWF]
# ===========================================================================


class TestDryWeatherFlow:
    async def test_add_dwf(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_dwf, dwf_count

        ctx = await _opened_session(session_manager, inp_path, "inf_dwf")
        result = await add_dwf(
            ctx,
            session_id="inf_dwf",
            node_id="J1",
            constituent="FLOW",
            avg_value=0.5,
        )
        assert result["status"] == "ok"
        assert result["avg_value"] == 0.5

        c = await dwf_count(ctx, session_id="inf_dwf")
        assert c["count"] == 1


# ===========================================================================
# [HYDROGRAPHS] + [RDII] together (RDII depends on a hydrograph group existing)
# ===========================================================================


class TestHydrographsAndRDII:
    async def test_add_hydrograph_then_rdii_roundtrip(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph,
            add_rdii,
            get_rdii,
            hydrograph_count,
            rdii_count,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_uh")
        # Create a hydrograph group with one ALL/SHORT row.
        await add_hydrograph(
            ctx,
            session_id="inf_uh",
            uh_name="UH1",
            month="all",
            response="short",
            r=0.1,
            t=2.0,
            k=2.0,
        )
        h = await hydrograph_count(ctx, session_id="inf_uh")
        assert h["count"] == 1

        # Assign it to a node as an RDII inflow.
        await add_rdii(
            ctx,
            session_id="inf_uh",
            node_id="J1",
            uh_name="UH1",
            area=1.5,
        )
        r = await rdii_count(ctx, session_id="inf_uh")
        assert r["count"] == 1

        got = await get_rdii(ctx, session_id="inf_uh", entry_index=0)
        assert got["uh_name"] == "UH1"
        assert got["area"] == pytest.approx(1.5)
        assert got["node_index"] >= 0

    async def test_invalid_month_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_hydrograph

        ctx = await _opened_session(session_manager, inp_path, "inf_uh_bad")
        with pytest.raises(ToolError, match="Unknown month"):
            await add_hydrograph(
                ctx,
                session_id="inf_uh_bad",
                uh_name="U",
                month="bogus",
                response="short",
                r=0.1,
                t=2.0,
                k=2.0,
            )

    async def test_invalid_response_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_hydrograph

        ctx = await _opened_session(session_manager, inp_path, "inf_uh_rbad")
        with pytest.raises(ToolError, match="Unknown response"):
            await add_hydrograph(
                ctx,
                session_id="inf_uh_rbad",
                uh_name="U",
                month="all",
                response="forever",
                r=0.1,
                t=2.0,
                k=2.0,
            )

    async def test_k_below_one_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_hydrograph

        ctx = await _opened_session(session_manager, inp_path, "inf_uh_kbad")
        with pytest.raises(ToolError, match=r"k.*>= 1\.0"):
            await add_hydrograph(
                ctx,
                session_id="inf_uh_kbad",
                uh_name="U",
                month="all",
                response="short",
                r=0.1,
                t=2.0,
                k=0.5,
            )

    async def test_empty_uh_name_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_rdii

        ctx = await _opened_session(session_manager, inp_path, "inf_rdii_empty")
        with pytest.raises(ToolError, match="uh_name must not be empty"):
            await add_rdii(
                ctx,
                session_id="inf_rdii_empty",
                node_id="J1",
                uh_name="",
                area=1.0,
            )


# ===========================================================================
# [HYDROGRAPHS] gage assignment
# ===========================================================================


class TestHydrographGage:
    async def test_assign_gage_to_uh_group(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph,
            add_hydrograph_gage,
            get_hydrograph_gage,
            hydrograph_gage_count,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_gage")
        await add_hydrograph(
            ctx,
            session_id="inf_gage",
            uh_name="UH_G",
            month="all",
            response="short",
            r=0.1,
            t=2.0,
            k=2.0,
        )
        await add_hydrograph_gage(
            ctx,
            session_id="inf_gage",
            uh_name="UH_G",
            gage_name="RainGage",
        )
        c = await hydrograph_gage_count(ctx, session_id="inf_gage")
        assert c["count"] == 1
        got = await get_hydrograph_gage(ctx, session_id="inf_gage", entry_index=0)
        assert got["uh_name"] == "UH_G"
        assert got["gage_name"] == "RainGage"


# ===========================================================================
# [RDII_DECAY] exponential decay model
# ===========================================================================


class TestRDIIDecay:
    async def test_add_decay_after_hydrograph(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph,
            add_rdii_decay,
            get_rdii_decay,
            rdii_decay_count,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_decay")
        # The hydrograph row for (UH_D, SHORT) must exist before the decay row.
        await add_hydrograph(
            ctx,
            session_id="inf_decay",
            uh_name="UH_D",
            month="all",
            response="short",
            r=0.1,
            t=2.0,
            k=2.0,
        )
        await add_rdii_decay(
            ctx,
            session_id="inf_decay",
            uh_name="UH_D",
            response="short",
            k_dep=0.05,
            k_0=0.02,
            k_T=0.01,
            T_ref=10.0,
            theta_rec=0.07,
            T_freeze=0.0,
        )
        c = await rdii_decay_count(ctx, session_id="inf_decay")
        assert c["count"] == 1
        got = await get_rdii_decay(ctx, session_id="inf_decay", entry_index=0)
        assert got["entry"]["uh_name"] == "UH_D"
        assert got["entry"]["response"] == 0  # SHORT


# ===========================================================================
# [HYDROGRAPHS] group enumeration (DA-ENG-01)
# ===========================================================================


class TestHydrographGroupEnumeration:
    """``hydrograph_group_count`` + ``list_hydrograph_groups``.

    These tools de-duplicate the raw per-(group, month, response) entries
    so a GUI Object Browser shows one row per group rather than N rows of
    mostly-empty placeholders.
    """

    async def test_empty_model_has_zero_groups(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import hydrograph_group_count

        ctx = await _opened_session(session_manager, inp_path, "inf_uhg_empty")
        result = await hydrograph_group_count(ctx, session_id="inf_uhg_empty")
        assert result["count"] == 0

    async def test_twelve_monthly_rows_count_as_one_group(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph,
            hydrograph_count,
            hydrograph_group_count,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_uhg_one")
        for m in (
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ):
            await add_hydrograph(
                ctx,
                session_id="inf_uhg_one",
                uh_name="SanSewer",
                month=m,
                response="short",
                r=0.05,
                t=1.0,
                k=2.0,
            )
        entries = await hydrograph_count(ctx, session_id="inf_uhg_one")
        assert entries["count"] == 12
        groups = await hydrograph_group_count(ctx, session_id="inf_uhg_one")
        assert groups["count"] == 1

    async def test_list_groups_first_occurrence_order(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_hydrograph, list_hydrograph_groups

        ctx = await _opened_session(session_manager, inp_path, "inf_uhg_list")
        for uh, month in [
            ("Combined", "jan"),
            ("Sanitary", "jan"),
            ("Combined", "feb"),
            ("Storm", "jan"),
            ("Sanitary", "feb"),
        ]:
            await add_hydrograph(
                ctx,
                session_id="inf_uhg_list",
                uh_name=uh,
                month=month,
                response="short",
                r=0.1,
                t=1.0,
                k=2.0,
            )

        result = await list_hydrograph_groups(ctx, session_id="inf_uhg_list")
        assert result["count"] == 3
        names = [g["name"] for g in result["groups"]]
        assert names == ["Combined", "Sanitary", "Storm"]
        indices = [g["index"] for g in result["groups"]]
        assert indices == [0, 1, 2]

    async def test_gage_only_groups_appear_in_list(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph,
            add_hydrograph_gage,
            list_hydrograph_groups,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_uhg_mix")
        # Gage-only group (no parameter rows yet) must still surface.
        await add_hydrograph_gage(
            ctx,
            session_id="inf_uhg_mix",
            uh_name="GageOnly",
            gage_name="RainGage",
        )
        await add_hydrograph(
            ctx,
            session_id="inf_uhg_mix",
            uh_name="Params",
            month="all",
            response="short",
            r=0.1,
            t=1.0,
            k=2.0,
        )
        result = await list_hydrograph_groups(ctx, session_id="inf_uhg_mix")
        assert result["count"] == 2
        names = [g["name"] for g in result["groups"]]
        # Parameter-entry groups come before gage-only groups.
        assert names == ["Params", "GageOnly"]


# ===========================================================================
# Gap-closure (2026-06-14): external / DWF read-remove-rescale accessors
# ===========================================================================


class TestExternalAccessors:
    async def test_external_get_remove_roundtrip(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_external,
            ext_inflow_count,
            get_external,
            remove_external,
            set_external_baseline,
            set_external_scale,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_ext_acc")
        await add_external(
            ctx,
            session_id="inf_ext_acc",
            node_id="J1",
            constituent="FLOW",
            ts_name="",
            inflow_type="FLOW",
            m_factor=2.0,
            baseline=0.3,
        )
        got = await get_external(ctx, session_id="inf_ext_acc", entry_index=0)
        assert got["node_index"] >= 0
        assert got["constituent"] == "FLOW"
        assert got["m_factor"] == pytest.approx(2.0)
        assert got["baseline"] == pytest.approx(0.3)

        await set_external_baseline(ctx, session_id="inf_ext_acc", entry_index=0, baseline=0.7)
        await set_external_scale(ctx, session_id="inf_ext_acc", entry_index=0, scale=3.5)
        got2 = await get_external(ctx, session_id="inf_ext_acc", entry_index=0)
        assert got2["baseline"] == pytest.approx(0.7)
        # set_external_scale updates s_factor (the time-series scale); the
        # add-time m_factor multiplier is left unchanged.
        assert got2["s_factor"] == pytest.approx(3.5)
        assert got2["m_factor"] == pytest.approx(2.0)

        await remove_external(ctx, session_id="inf_ext_acc", entry_index=0)
        c = await ext_inflow_count(ctx, session_id="inf_ext_acc")
        assert c["count"] == 0


class TestDWFAccessors:
    async def test_dwf_get_remove_roundtrip(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_dwf,
            dwf_count,
            get_dwf,
            remove_dwf,
            set_dwf_baseline,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_dwf_acc")
        await add_dwf(
            ctx, session_id="inf_dwf_acc", node_id="J1", constituent="FLOW", avg_value=0.4
        )
        got = await get_dwf(ctx, session_id="inf_dwf_acc", entry_index=0)
        assert got["node_index"] >= 0
        assert got["avg_value"] == pytest.approx(0.4)
        assert len(got["patterns"]) == 4

        await set_dwf_baseline(ctx, session_id="inf_dwf_acc", entry_index=0, avg_value=0.9)
        got2 = await get_dwf(ctx, session_id="inf_dwf_acc", entry_index=0)
        assert got2["avg_value"] == pytest.approx(0.9)

        await remove_dwf(ctx, session_id="inf_dwf_acc", entry_index=0)
        c = await dwf_count(ctx, session_id="inf_dwf_acc")
        assert c["count"] == 0


# ===========================================================================
# Gap-closure (2026-06-14): hydrograph edit / remove + RDII remove
# ===========================================================================


class TestHydrographEdits:
    async def _seed(self, ctx, sid):
        from openswmm_mcp.tools.inflows import add_hydrograph

        await add_hydrograph(
            ctx,
            session_id=sid,
            uh_name="UHE",
            month="all",
            response="short",
            r=0.1,
            t=2.0,
            k=2.0,
        )

    async def test_set_rtk_and_ia_in_place(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            get_hydrograph,
            hydrograph_count,
            set_hydrograph_ia,
            set_hydrograph_rtk,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_he_set")
        await self._seed(ctx, "inf_he_set")
        await set_hydrograph_rtk(
            ctx,
            session_id="inf_he_set",
            uh_name="UHE",
            month="all",
            response="short",
            r=0.25,
            t=3.0,
            k=2.5,
        )
        await set_hydrograph_ia(
            ctx,
            session_id="inf_he_set",
            uh_name="UHE",
            month="all",
            response="short",
            dmax=0.5,
            drecov=0.1,
            dinit=0.0,
        )
        # Edits should not create new rows.
        assert (await hydrograph_count(ctx, session_id="inf_he_set"))["count"] == 1
        entry = (await get_hydrograph(ctx, session_id="inf_he_set", entry_index=0))["entry"]
        assert entry["r"] == pytest.approx(0.25)
        assert entry["k"] == pytest.approx(2.5)
        assert entry["dmax"] == pytest.approx(0.5)

    async def test_remove_entry_and_group(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph,
            hydrograph_count,
            hydrograph_group_count,
            remove_hydrograph_entry,
            remove_hydrograph_group,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_he_rm")
        for m in ("jan", "feb"):
            await add_hydrograph(
                ctx,
                session_id="inf_he_rm",
                uh_name="UHR",
                month=m,
                response="short",
                r=0.1,
                t=2.0,
                k=2.0,
            )
        await remove_hydrograph_entry(
            ctx, session_id="inf_he_rm", uh_name="UHR", month="jan", response="short"
        )
        assert (await hydrograph_count(ctx, session_id="inf_he_rm"))["count"] == 1
        await remove_hydrograph_group(ctx, session_id="inf_he_rm", uh_name="UHR")
        assert (await hydrograph_group_count(ctx, session_id="inf_he_rm"))["count"] == 0

    async def test_rename_group(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            list_hydrograph_groups,
            rename_hydrograph_group,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_he_ren")
        await self._seed(ctx, "inf_he_ren")
        await rename_hydrograph_group(ctx, session_id="inf_he_ren", group_index=0, new_id="UHE2")
        names = [
            g["name"]
            for g in (await list_hydrograph_groups(ctx, session_id="inf_he_ren"))["groups"]
        ]
        assert names == ["UHE2"]

    async def test_set_gage_replaces(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph_gage,
            get_hydrograph_gage,
            set_hydrograph_gage,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_he_gage")
        await self._seed(ctx, "inf_he_gage")
        await add_hydrograph_gage(
            ctx, session_id="inf_he_gage", uh_name="UHE", gage_name="RainGage"
        )
        await set_hydrograph_gage(
            ctx, session_id="inf_he_gage", uh_name="UHE", gage_name="RainGage"
        )
        got = await get_hydrograph_gage(ctx, session_id="inf_he_gage", entry_index=0)
        assert got["uh_name"] == "UHE"
        assert got["gage_name"] == "RainGage"

    async def test_clear_group_months_keeps_all_row(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph,
            clear_hydrograph_group_months,
            hydrograph_count,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_he_clr")
        await add_hydrograph(
            ctx,
            session_id="inf_he_clr",
            uh_name="UHC",
            month="all",
            response="short",
            r=0.1,
            t=2.0,
            k=2.0,
        )
        await add_hydrograph(
            ctx,
            session_id="inf_he_clr",
            uh_name="UHC",
            month="jan",
            response="short",
            r=0.1,
            t=2.0,
            k=2.0,
        )
        await clear_hydrograph_group_months(ctx, session_id="inf_he_clr", uh_name="UHC")
        # The ALL-months row survives; the month-specific row is cleared.
        assert (await hydrograph_count(ctx, session_id="inf_he_clr"))["count"] == 1

    async def test_remove_rdii(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import add_rdii, rdii_count, remove_rdii

        ctx = await _opened_session(session_manager, inp_path, "inf_rdii_rm")
        await self._seed(ctx, "inf_rdii_rm")
        await add_rdii(ctx, session_id="inf_rdii_rm", node_id="J1", uh_name="UHE", area=1.0)
        assert (await rdii_count(ctx, session_id="inf_rdii_rm"))["count"] == 1
        await remove_rdii(ctx, session_id="inf_rdii_rm", entry_index=0)
        assert (await rdii_count(ctx, session_id="inf_rdii_rm"))["count"] == 0


class TestRDIIDecayEdits:
    async def test_set_and_remove_decay(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import (
            add_hydrograph,
            add_rdii_decay,
            get_rdii_decay,
            rdii_decay_count,
            remove_rdii_decay,
            set_rdii_decay,
        )

        ctx = await _opened_session(session_manager, inp_path, "inf_decay_edit")
        await add_hydrograph(
            ctx,
            session_id="inf_decay_edit",
            uh_name="UHD",
            month="all",
            response="short",
            r=0.1,
            t=2.0,
            k=2.0,
        )
        await add_rdii_decay(
            ctx,
            session_id="inf_decay_edit",
            uh_name="UHD",
            response="short",
            k_dep=0.05,
            k_0=0.02,
            k_T=0.01,
            T_ref=10.0,
            theta_rec=0.07,
            T_freeze=0.0,
        )
        await set_rdii_decay(
            ctx,
            session_id="inf_decay_edit",
            uh_name="UHD",
            response="short",
            k_dep=0.09,
            k_0=0.02,
            k_T=0.01,
            T_ref=10.0,
            theta_rec=0.07,
            T_freeze=0.0,
        )
        got = await get_rdii_decay(ctx, session_id="inf_decay_edit", entry_index=0)
        assert got["entry"]["k_dep"] == pytest.approx(0.09)

        await remove_rdii_decay(ctx, session_id="inf_decay_edit", uh_name="UHD", response="short")
        assert (await rdii_decay_count(ctx, session_id="inf_decay_edit"))["count"] == 0


# ===========================================================================
# Backend guard: legacy engine is unsupported
# ===========================================================================


class TestLegacyGuard:
    async def test_legacy_backend_rejected(self, session_manager, inp_path):
        from openswmm_mcp.tools.inflows import ext_inflow_count

        session = await session_manager.create_session(
            session_id="legacy_inf",
            inp_path=inp_path,
            engine="legacy",
        )
        session.backend.solver.open()
        session.state = "opened"

        ctx = MockContext(session_manager)
        with pytest.raises(ToolError, match="not supported|legacy"):
            await ext_inflow_count(ctx, session_id="legacy_inf")
