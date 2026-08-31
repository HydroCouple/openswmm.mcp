"""Unit tests for the reaction-system MCP tools (tools/reactions.py).

Two groups run unconditionally because they need no open model at all: the
static vocabulary tools (``reactions_hydraulic_variables`` /
``reactions_functions`` are engine-less statics) and the enum-token validation
paths.

The bundled ``site_drainage_model.inp`` declares no ``[REACTION_*]`` sections,
so the CRUD round-trips are guarded by a probe that skips with an explicit
message when the reaction system is not available on the model.
"""

from __future__ import annotations

import unittest

from openswmm_mcp.errors import ToolError
from tests.unit._base import EngineToolTestCase, MockContext


class _ReactionsTestCase(EngineToolTestCase):
    async def _opened(self, session_id: str) -> MockContext:
        """Open the reference model in the editable 'opened' state.

        Reaction mutation is BUILDING/OPENED only -- every mutator recompiles
        the whole system, and rows only seed state at initialize().
        """
        from openswmm_mcp.tools.lifecycle import open_model

        ctx = MockContext(self.session_manager)
        await open_model(
            ctx, inp_path=self.inp_path, session_id=session_id, lenient_open=True
        )
        return ctx

    async def _rxn_or_skip(self, session_id: str) -> MockContext:
        from openswmm_mcp.tools.reactions import list_species

        ctx = await self._opened(session_id)
        try:
            await list_species(ctx, session_id=session_id)
        except ToolError as exc:
            self.skipTest(f"reaction system unavailable on the model (list_species: {exc})")
        return ctx


# ===========================================================================
# Static vocabulary -- no session, no open model
# ===========================================================================


class TestStaticVocabulary(EngineToolTestCase):
    """``hydraulic_variables`` / ``functions`` are engine-less statics.

    They must work with no session at all -- that is the whole point of them
    for an agent building an expression before anything is loaded.
    """

    async def test_hydraulic_variables_needs_no_session(self):
        from openswmm_mcp.tools.reactions import hydraulic_variables

        r = await hydraulic_variables(MockContext(self.session_manager))
        self.assertGreater(r["count"], 0)
        self.assertEqual(r["count"], len(r["hydraulic_variables"]))
        for row in r["hydraulic_variables"]:
            self.assertIn("name", row)
            self.assertIn("description", row)
        # The documented built-ins (D Q U RE US FF AV HRT DT).
        names = {row["name"].upper() for row in r["hydraulic_variables"]}
        self.assertIn("D", names)
        self.assertIn("Q", names)

    async def test_functions_needs_no_session(self):
        from openswmm_mcp.tools.reactions import functions

        r = await functions(MockContext(self.session_manager))
        self.assertGreater(r["count"], 0)
        self.assertEqual(r["count"], len(r["functions"]))
        by_name = {row["name"].upper(): row["arity"] for row in r["functions"]}
        self.assertEqual(by_name.get("EXP"), 1)
        # MIN / MAX / POW are the documented arity-2 functions.
        self.assertEqual(by_name.get("POW"), 2)


# ===========================================================================
# Expression validation
# ===========================================================================


class TestValidateExpression(_ReactionsTestCase):
    async def test_valid_expression_reports_no_diagnostic(self):
        from openswmm_mcp.tools.reactions import validate_expression

        ctx = await self._rxn_or_skip("rx_valid")
        # A pure hydraulic-variable expression needs no declared species.
        r = await validate_expression(ctx, session_id="rx_valid", expression="D * 2.0")
        self.assertTrue(r["valid"])
        self.assertEqual(r["message"], "")

    async def test_invalid_expression_reports_a_column(self):
        from openswmm_mcp.tools.reactions import validate_expression

        ctx = await self._rxn_or_skip("rx_invalid")
        r = await validate_expression(
            ctx, session_id="rx_invalid", expression="NOT_A_SYMBOL * 2.0"
        )
        self.assertFalse(r["valid"])
        self.assertTrue(r["message"])
        self.assertIsInstance(r["column"], int)

    async def test_validation_changes_nothing(self):
        from openswmm_mcp.tools.reactions import list_species, validate_expression

        ctx = await self._rxn_or_skip("rx_pure")
        before = (await list_species(ctx, session_id="rx_pure"))["count"]
        await validate_expression(ctx, session_id="rx_pure", expression="!!! bad !!!")
        after = (await list_species(ctx, session_id="rx_pure"))["count"]
        self.assertEqual(before, after)

    async def test_empty_expression_raises(self):
        from openswmm_mcp.tools.reactions import validate_expression

        ctx = await self._opened("rx_empty")
        with self.assertRaisesRegex(ToolError, "Provide an expression"):
            await validate_expression(ctx, session_id="rx_empty", expression="")

    async def test_unknown_scope_raises(self):
        from openswmm_mcp.tools.reactions import validate_expression

        ctx = await self._opened("rx_scope")
        with self.assertRaisesRegex(ToolError, "Unknown reaction scope"):
            await validate_expression(
                ctx, session_id="rx_scope", expression="D", scope="not_a_scope"
            )


class TestCheckText(_ReactionsTestCase):
    async def test_bad_text_is_rejected_without_mutating(self):
        from openswmm_mcp.tools.reactions import check_text, list_species

        ctx = await self._rxn_or_skip("rx_check")
        before = (await list_species(ctx, session_id="rx_check"))["count"]
        r = await check_text(ctx, session_id="rx_check", text="[NOT_A_SECTION]\ngarbage\n")
        self.assertFalse(r["valid"])
        self.assertTrue(r["message"])
        # The whole-file checker reports text without a column.
        self.assertEqual(r["column"], -1)
        self.assertEqual((await list_species(ctx, session_id="rx_check"))["count"], before)

    async def test_empty_text_raises(self):
        from openswmm_mcp.tools.reactions import check_text

        ctx = await self._opened("rx_check_empty")
        with self.assertRaisesRegex(ToolError, "Provide .rxn text to check"):
            await check_text(ctx, session_id="rx_check_empty", text="")


# ===========================================================================
# CRUD
# ===========================================================================


class TestSpecies(_ReactionsTestCase):
    async def test_add_then_list_round_trip(self):
        from openswmm_mcp.tools.reactions import add_species, list_species

        ctx = await self._rxn_or_skip("rx_sp")
        await add_species(ctx, session_id="rx_sp", name="AS1", wall=False, units="MG")

        rows = (await list_species(ctx, session_id="rx_sp"))["species"]
        row = next(r for r in rows if r["name"] == "AS1")
        self.assertFalse(row["is_wall"])
        self.assertEqual(row["units"], "MG")
        self.assertEqual(row["pipe"]["form"], "none")

    async def test_remove_species(self):
        from openswmm_mcp.tools.reactions import add_species, list_species, remove_species

        ctx = await self._rxn_or_skip("rx_sp_rm")
        await add_species(ctx, session_id="rx_sp_rm", name="AS1")
        await remove_species(ctx, session_id="rx_sp_rm", species="AS1")

        names = {r["name"] for r in (await list_species(ctx, session_id="rx_sp_rm"))["species"]}
        self.assertNotIn("AS1", names)

    async def test_expression_round_trip(self):
        from openswmm_mcp.tools.reactions import (
            add_species,
            get_species_expression,
            set_species_expression,
        )

        ctx = await self._rxn_or_skip("rx_expr")
        await add_species(ctx, session_id="rx_expr", name="AS1")
        await set_species_expression(
            ctx, session_id="rx_expr", species="AS1", scope="pipe",
            form="rate", expression="-0.1 * AS1",
        )

        r = await get_species_expression(
            ctx, session_id="rx_expr", species="AS1", scope="pipe"
        )
        self.assertEqual(r["form"], "rate")
        self.assertIn("AS1", r["expression"])

    async def test_clearing_an_expression_with_form_none(self):
        from openswmm_mcp.tools.reactions import (
            add_species,
            get_species_expression,
            set_species_expression,
        )

        ctx = await self._rxn_or_skip("rx_expr_clr")
        await add_species(ctx, session_id="rx_expr_clr", name="AS1")
        await set_species_expression(
            ctx, session_id="rx_expr_clr", species="AS1", scope="pipe",
            form="rate", expression="-0.1 * AS1",
        )
        await set_species_expression(
            ctx, session_id="rx_expr_clr", species="AS1", scope="pipe", form="none"
        )

        r = await get_species_expression(
            ctx, session_id="rx_expr_clr", species="AS1", scope="pipe"
        )
        self.assertEqual(r["form"], "none")

    async def test_uncompilable_expression_is_refused(self):
        from openswmm_mcp.tools.reactions import add_species, set_species_expression

        ctx = await self._rxn_or_skip("rx_expr_bad")
        await add_species(ctx, session_id="rx_expr_bad", name="AS1")
        with self.assertRaises(ToolError):
            await set_species_expression(
                ctx, session_id="rx_expr_bad", species="AS1", scope="pipe",
                form="rate", expression="NOT_A_SYMBOL * 2",
            )

    async def test_missing_species_raises(self):
        from openswmm_mcp.tools.reactions import add_species, remove_species

        ctx = await self._opened("rx_sp_bad")
        with self.assertRaisesRegex(ToolError, "Provide a species name"):
            await add_species(ctx, session_id="rx_sp_bad", name="")
        with self.assertRaisesRegex(ToolError, "Provide a species name or index"):
            await remove_species(ctx, session_id="rx_sp_bad", species="")

    async def test_unknown_expression_form_raises(self):
        from openswmm_mcp.tools.reactions import set_species_expression

        ctx = await self._opened("rx_form_bad")
        with self.assertRaisesRegex(ToolError, "Unknown reaction expression form"):
            await set_species_expression(
                ctx, session_id="rx_form_bad", species="AS1", form="not_a_form"
            )


class TestCoefficients(_ReactionsTestCase):
    async def test_add_set_value_round_trip(self):
        from openswmm_mcp.tools.reactions import (
            add_coefficient,
            list_coefficients,
            set_coefficient_value,
        )

        ctx = await self._rxn_or_skip("rx_co")
        await add_coefficient(ctx, session_id="rx_co", name="K1", parameter=False, value=0.5)
        await set_coefficient_value(ctx, session_id="rx_co", coefficient="K1", value=0.75)

        row = next(
            r for r in (await list_coefficients(ctx, session_id="rx_co"))["coefficients"]
            if r["name"] == "K1"
        )
        self.assertAlmostEqual(row["value"], 0.75, places=6)
        self.assertFalse(row["is_param"])

    async def test_remove_coefficient(self):
        from openswmm_mcp.tools.reactions import (
            add_coefficient,
            list_coefficients,
            remove_coefficient,
        )

        ctx = await self._rxn_or_skip("rx_co_rm")
        await add_coefficient(ctx, session_id="rx_co_rm", name="K1", value=0.5)
        await remove_coefficient(ctx, session_id="rx_co_rm", coefficient="K1")

        names = {
            r["name"] for r in (await list_coefficients(ctx, session_id="rx_co_rm"))["coefficients"]
        }
        self.assertNotIn("K1", names)

    async def test_missing_name_raises(self):
        from openswmm_mcp.tools.reactions import add_coefficient, set_coefficient_value

        ctx = await self._opened("rx_co_bad")
        with self.assertRaisesRegex(ToolError, "Provide a coefficient name"):
            await add_coefficient(ctx, session_id="rx_co_bad", name="")
        with self.assertRaisesRegex(ToolError, "Provide a coefficient name or index"):
            await set_coefficient_value(ctx, session_id="rx_co_bad", coefficient="", value=1.0)


class TestTerms(_ReactionsTestCase):
    async def test_add_and_replace_round_trip(self):
        from openswmm_mcp.tools.reactions import add_term, list_terms, set_term_expression

        ctx = await self._rxn_or_skip("rx_tm")
        await add_term(ctx, session_id="rx_tm", name="T1", expression="D * 2.0")
        await set_term_expression(ctx, session_id="rx_tm", term="T1", expression="D * 3.0")

        row = next(
            r for r in (await list_terms(ctx, session_id="rx_tm"))["terms"] if r["name"] == "T1"
        )
        self.assertIn("3", row["expression"])

    async def test_remove_term(self):
        from openswmm_mcp.tools.reactions import add_term, list_terms, remove_term

        ctx = await self._rxn_or_skip("rx_tm_rm")
        await add_term(ctx, session_id="rx_tm_rm", name="T1", expression="D * 2.0")
        await remove_term(ctx, session_id="rx_tm_rm", term="T1")

        names = {r["name"] for r in (await list_terms(ctx, session_id="rx_tm_rm"))["terms"]}
        self.assertNotIn("T1", names)

    async def test_missing_name_raises(self):
        from openswmm_mcp.tools.reactions import add_term, remove_term

        ctx = await self._opened("rx_tm_bad")
        with self.assertRaisesRegex(ToolError, "Provide a term name"):
            await add_term(ctx, session_id="rx_tm_bad", name="", expression="D")
        with self.assertRaisesRegex(ToolError, "Provide a term name or index"):
            await remove_term(ctx, session_id="rx_tm_bad", term="")


class TestReactionOptions(_ReactionsTestCase):
    async def test_get_option_returns_a_canonical_token(self):
        from openswmm_mcp.tools.reactions import get_option

        ctx = await self._rxn_or_skip("rx_opt")
        r = await get_option(ctx, session_id="rx_opt", key="SOLVER")
        self.assertIsInstance(r["value"], str)

    async def test_missing_key_raises(self):
        from openswmm_mcp.tools.reactions import get_option, set_option

        ctx = await self._opened("rx_opt_bad")
        with self.assertRaisesRegex(ToolError, "Provide a key"):
            await get_option(ctx, session_id="rx_opt_bad", key="")
        with self.assertRaisesRegex(ToolError, "Provide a key"):
            await set_option(ctx, session_id="rx_opt_bad", key="", value="x")


class TestInitialQualityRows(_ReactionsTestCase):
    async def test_global_and_element_round_trip(self):
        from openswmm_mcp.tools.reactions import (
            add_species,
            list_initial_quality,
            set_initial_element,
            set_initial_global,
        )

        ctx = await self._rxn_or_skip("rx_init")
        await add_species(ctx, session_id="rx_init", name="AS1")
        await set_initial_global(ctx, session_id="rx_init", species="AS1", value=2.0)
        await set_initial_element(
            ctx, session_id="rx_init", species="AS1", elem_index=0, is_link=False, value=5.0
        )

        r = await list_initial_quality(ctx, session_id="rx_init")
        row = next(g for g in r["globals"] if g["species"] == "AS1")
        self.assertAlmostEqual(row["value"], 2.0, places=6)
        self.assertEqual(r["count"], 1)
        self.assertAlmostEqual(r["elements"][0]["value"], 5.0, places=6)

    async def test_element_set_is_an_upsert(self):
        from openswmm_mcp.tools.reactions import (
            add_species,
            list_initial_quality,
            set_initial_element,
        )

        ctx = await self._rxn_or_skip("rx_init_up")
        await add_species(ctx, session_id="rx_init_up", name="AS1")
        for value in (1.0, 7.0):
            await set_initial_element(
                ctx, session_id="rx_init_up", species="AS1", elem_index=0,
                is_link=False, value=value,
            )

        r = await list_initial_quality(ctx, session_id="rx_init_up")
        self.assertEqual(r["count"], 1)
        self.assertAlmostEqual(r["elements"][0]["value"], 7.0, places=6)

    async def test_remove_element_row(self):
        from openswmm_mcp.tools.reactions import (
            add_species,
            list_initial_quality,
            remove_initial_element,
            set_initial_element,
        )

        ctx = await self._rxn_or_skip("rx_init_rm")
        await add_species(ctx, session_id="rx_init_rm", name="AS1")
        await set_initial_element(
            ctx, session_id="rx_init_rm", species="AS1", elem_index=0, is_link=False, value=5.0
        )
        await remove_initial_element(ctx, session_id="rx_init_rm", row_index=0)

        self.assertEqual((await list_initial_quality(ctx, session_id="rx_init_rm"))["count"], 0)

    async def test_negative_elem_index_raises(self):
        from openswmm_mcp.tools.reactions import set_initial_element

        ctx = await self._opened("rx_init_bad")
        with self.assertRaisesRegex(ToolError, "non-negative elem_index"):
            await set_initial_element(
                ctx, session_id="rx_init_bad", species="AS1", elem_index=-1
            )

    async def test_negative_row_index_raises(self):
        from openswmm_mcp.tools.reactions import remove_initial_element

        ctx = await self._opened("rx_init_neg")
        with self.assertRaisesRegex(ToolError, "non-negative row_index"):
            await remove_initial_element(ctx, session_id="rx_init_neg", row_index=-1)


class TestSerializeApplyRoundTrip(_ReactionsTestCase):
    async def test_serialize_then_apply_is_stable(self):
        from openswmm_mcp.tools.reactions import add_species, apply_text, serialize

        ctx = await self._rxn_or_skip("rx_txt")
        await add_species(ctx, session_id="rx_txt", name="AS1", units="MG")

        first = (await serialize(ctx, session_id="rx_txt"))["text"]
        await apply_text(ctx, session_id="rx_txt", text=first)
        second = (await serialize(ctx, session_id="rx_txt"))["text"]
        # serialize -> apply_text -> serialize is byte-identical (D-RC6).
        self.assertEqual(first, second)

    async def test_apply_bad_text_leaves_the_system_intact(self):
        from openswmm_mcp.tools.reactions import add_species, apply_text, serialize

        ctx = await self._rxn_or_skip("rx_txt_bad")
        await add_species(ctx, session_id="rx_txt_bad", name="AS1")
        before = (await serialize(ctx, session_id="rx_txt_bad"))["text"]

        with self.assertRaises(ToolError):
            await apply_text(ctx, session_id="rx_txt_bad", text="[NOT_A_SECTION]\ngarbage\n")

        # Staged: on any error the previous system is byte-identical.
        self.assertEqual((await serialize(ctx, session_id="rx_txt_bad"))["text"], before)

    async def test_empty_text_raises(self):
        from openswmm_mcp.tools.reactions import apply_text

        ctx = await self._opened("rx_txt_empty")
        with self.assertRaisesRegex(ToolError, "Provide .rxn text to apply"):
            await apply_text(ctx, session_id="rx_txt_empty", text="")

    async def test_save_without_a_path_or_binding_raises(self):
        from openswmm_mcp.tools.reactions import save

        ctx = await self._rxn_or_skip("rx_save")
        # No [PROCESS_COMPONENTS] config path is bound on the reference model,
        # so there is nowhere to write and the call must be refused.
        with self.assertRaises(ToolError):
            await save(ctx, session_id="rx_save", path="")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
