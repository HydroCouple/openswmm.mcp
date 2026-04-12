"""Tests for openswmm_mcp.prompts.workflows -- guided workflow prompts."""

from __future__ import annotations

from openswmm_mcp.prompts.workflows import (
    analyze_model,
    build_simple_model,
    compare_scenarios,
    design_review,
    diagnose_flooding,
    explain_results,
    what_if,
)

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAnalyzeModelPrompt:
    def test_analyze_model_prompt(self):
        """analyze_model returns a string mentioning SWMM model."""
        result = analyze_model(inp_path="/models/example.inp")

        assert isinstance(result, str)
        assert "SWMM model" in result or "SWMM" in result
        assert "/models/example.inp" in result

    def test_analyze_model_contains_workflow_steps(self):
        """The prompt includes numbered steps."""
        result = analyze_model(inp_path="/data/test.inp")
        assert "1." in result
        assert "open_model" in result or "Open" in result


class TestDiagnoseFloodingPrompt:
    def test_diagnose_flooding_prompt(self):
        """diagnose_flooding returns a string mentioning flooding."""
        result = diagnose_flooding()

        assert isinstance(result, str)
        assert "flooding" in result.lower()

    def test_diagnose_flooding_with_nodes(self):
        """When node_ids are specified, they appear in the output."""
        result = diagnose_flooding(session_id="run1", node_ids="J1, J2, J3")

        assert "J1" in result
        assert "J2" in result
        assert "J3" in result
        assert "run1" in result

    def test_diagnose_flooding_default_session(self):
        """Default session_id appears in the output."""
        result = diagnose_flooding()
        assert "default" in result


class TestCompareScenariosPrompt:
    def test_compare_scenarios_prompt(self):
        """compare_scenarios returns a string containing both session names."""
        result = compare_scenarios(session_a="baseline", session_b="upsized_pipes")

        assert isinstance(result, str)
        assert "baseline" in result
        assert "upsized_pipes" in result

    def test_compare_scenarios_mentions_comparison(self):
        """The prompt includes comparison-related language."""
        result = compare_scenarios(session_a="a", session_b="b")
        assert "compare" in result.lower() or "comparison" in result.lower()


class TestDesignReviewPrompt:
    def test_design_review_prompt(self):
        """design_review returns a design-review workflow string."""
        result = design_review()

        assert isinstance(result, str)
        assert "design" in result.lower() or "review" in result.lower()

    def test_design_review_with_standard(self):
        """When a standard is provided, it appears in the output."""
        result = design_review(session_id="proj1", standard="10-year")

        assert "10-year" in result
        assert "proj1" in result

    def test_design_review_default_session(self):
        """Default session_id appears when none is specified."""
        result = design_review()
        assert "default" in result


class TestWhatIfPrompt:
    def test_what_if_prompt(self):
        """what_if returns a scenario-setup workflow string."""
        result = what_if()

        assert isinstance(result, str)
        assert "what-if" in result.lower() or "scenario" in result.lower()

    def test_what_if_with_description(self):
        """When description is given, it appears in the output."""
        result = what_if(
            session_id="base",
            description="Double all pipe diameters",
        )

        assert "Double all pipe diameters" in result
        assert "base" in result

    def test_what_if_mentions_clone(self):
        """The prompt references clone_session for branching."""
        result = what_if()
        assert "clone_session" in result


class TestBuildSimpleModelPrompt:
    def test_build_simple_model_prompt(self):
        """build_simple_model returns a model-construction workflow string."""
        desc = "three subcatchments draining to a single outfall"
        result = build_simple_model(description=desc)

        assert isinstance(result, str)
        assert desc in result

    def test_build_simple_model_contains_steps(self):
        """The prompt contains step-by-step instructions."""
        result = build_simple_model(description="simple pipe network")

        assert "1." in result
        assert "add_node" in result or "Add nodes" in result
        assert "SWMM" in result or "model" in result.lower()


class TestExplainResultsPrompt:
    def test_explain_results_prompt(self):
        """explain_results returns a plain-language explanation prompt."""
        result = explain_results()

        assert isinstance(result, str)
        assert "result" in result.lower() or "explain" in result.lower()

    def test_explain_results_with_session(self):
        """The session_id is embedded in the output."""
        result = explain_results(session_id="final_run")

        assert "final_run" in result

    def test_explain_results_mentions_stakeholder(self):
        """The prompt targets non-technical audience."""
        result = explain_results()
        assert "stakeholder" in result.lower() or "non-technical" in result.lower()
