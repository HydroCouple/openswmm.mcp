"""Guided workflow prompts for common SWMM modelling tasks."""

from __future__ import annotations

from fastmcp import FastMCP

prompts_mcp = FastMCP("prompts")


@prompts_mcp.prompt()
def analyze_model(inp_path: str) -> str:
    """Comprehensive review of a model's structure and settings."""
    return (
        f"Review the SWMM model at '{inp_path}'.\n"
        "1. open_model(path) and read the returned counts, options and warnings.\n"
        "2. describe(session_id='default') to see which element kinds and services it uses.\n"
        "3. get(kind='subcatchment', fields=['area','width','slope','imperv_pct']) and "
        "get(kind='link', fields=['type','length','roughness','slope']) for the network; "
        "find(kind='node', type='STORAGE') for storage units.\n"
        "4. Look for missing data, implausible parameters, disconnected elements and "
        "time-step concerns.\n"
        "Report: overview, network topology, hydrology, hydraulics, potential issues, "
        "recommendations."
    )


@prompts_mcp.prompt()
def diagnose_flooding(session_id: str = "default", node_ids: str | None = None) -> str:
    """Investigate flooding causes and suggest mitigations."""
    focus = f" Focus on nodes: {node_ids}." if node_ids else ""
    return (
        f"Investigate flooding in session '{session_id}'.{focus}\n"
        "1. run(session_id, until='end') if it has not finished.\n"
        "2. report(session_id, 'flooding') for flooded nodes by volume; "
        "report(session_id, 'capacity') for surcharged links.\n"
        "3. timeseries(session_id, 'node', [ids], 'depth') to separate brief surcharge from "
        "sustained overflow; get(kind='link', fields=['from_node','to_node','xsect.g1']) near "
        "flooded nodes to find bottlenecks.\n"
        "4. report(session_id, 'mass_balance') to rule out numerical problems.\n"
        "Report: flooding summary, root causes, hydraulic context, mitigation options."
    )


@prompts_mcp.prompt()
def compare_scenarios(session_a: str, session_b: str) -> str:
    """Side-by-side comparison of two simulation sessions."""
    return (
        f"Compare sessions '{session_a}' (baseline) and '{session_b}'.\n"
        "1. Make sure both have run to the end.\n"
        f"2. compare('{session_a}', '{session_b}', 'node', 'depth') and "
        f"compare('{session_a}', '{session_b}', 'link', 'flow').\n"
        "3. report(..., 'flooding') and report(..., 'mass_balance') for each session.\n"
        "Summarise where and how much the scenarios differ and whether the change helps."
    )


@prompts_mcp.prompt()
def design_review(session_id: str = "default", standard: str | None = None) -> str:
    """Check a model against design standards."""
    std = standard or "common stormwater design practice"
    return (
        f"Review session '{session_id}' against {std}.\n"
        "Run it to the end, then check report(..., 'capacity') for links above full depth, "
        "report(..., 'flooding') for any flooding, get(kind='link', fields=['stats.max_velocity']) "
        "for scour/sedimentation velocities and get(kind='link', fields=['slope']) for minimum "
        "slopes. List each finding with the element, the value and the criterion."
    )


@prompts_mcp.prompt()
def what_if(session_id: str, description: str) -> str:
    """Set up and evaluate a what-if scenario."""
    return (
        f"Evaluate this what-if scenario on session '{session_id}': {description}\n"
        "1. session(action='clone', session_id=..., new_session_id='what_if') to keep the "
        "baseline, or open_model the same file into a new session.\n"
        "2. Apply the change with set (fields), edit (structure) or call (e.g. "
        "call(target='forcing', ...) for runtime forcing); describe() shows what exists.\n"
        "3. Run both to the end and compare(...) them; report the effect in plain terms."
    )


@prompts_mcp.prompt()
def build_simple_model(description: str) -> str:
    """Guided construction of a model from a text description."""
    return (
        f"Build a SWMM model for: {description}\n"
        "1. open_model(session_id='new') with no path starts an empty model.\n"
        "2. call('new', 'options', 'set_item', {'key': ..., 'value': ...}) for options.\n"
        "3. edit(action='add') nodes, then links with properties {'from_node','to_node'}, "
        "then subcatchments and gages; set remaining fields with set.\n"
        "4. save('new', 'model.inp') and run('new') to test it; fix what report(...) shows."
    )


@prompts_mcp.prompt()
def explain_results(session_id: str = "default") -> str:
    """Plain-language explanation of results for stakeholders."""
    return (
        f"Explain the results of session '{session_id}' to a non-technical audience.\n"
        "Use report(..., 'summary'), report(..., 'flooding'), report(..., 'capacity') and "
        "report(..., 'mass_balance'). Say what was simulated, where the system struggles, how "
        "confident the numbers are (continuity errors) and what could be done."
    )
