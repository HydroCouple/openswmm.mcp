"""Guided workflow prompts for common SWMM modelling tasks."""

from __future__ import annotations

from fastmcp import FastMCP

prompts_mcp = FastMCP("prompts")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@prompts_mcp.prompt()
def analyze_model(inp_path: str) -> str:
    """Return a comprehensive model-review prompt.

    The generated instructions tell the LLM to open the model, inspect its
    structure, and produce a written assessment covering network topology,
    hydrology settings, hydraulic parameters, and potential issues.

    Parameters
    ----------
    inp_path:
        File-system path to the SWMM ``.inp`` file to analyse.
    """
    return (
        f"Please perform a comprehensive review of the SWMM model at "
        f"'{inp_path}'. Follow these steps:\n"
        "\n"
        "1. Open the model using the `open_model` tool with the path above.\n"
        "2. Read the `swmm://session/default/summary` resource to get an "
        "overview of element counts, flow units, and time settings.\n"
        "3. Read `swmm://session/default/options` to review routing method, "
        "time-step, and other simulation options.\n"
        "4. Read `swmm://session/default/nodes` and "
        "`swmm://session/default/links` to catalogue the network elements.\n"
        "5. Read `swmm://session/default/subcatchments` to understand the "
        "hydrological inputs.\n"
        "6. For any unusually configured elements (e.g. very small or very "
        "large areas, extreme slopes), drill into the element detail "
        "resource for further inspection.\n"
        "\n"
        "Produce a structured report with the following sections:\n"
        "- **Model Overview**: element counts, flow units, simulation period.\n"
        "- **Network Topology**: how nodes and links are connected, outfall "
        "locations, storage units.\n"
        "- **Hydrology**: subcatchment parameters, rain gages, infiltration "
        "method.\n"
        "- **Hydraulics**: routing method, time-step adequacy, conduit "
        "sizing.\n"
        "- **Potential Issues**: missing data, unreasonable parameter values, "
        "disconnected elements, Courant-number concerns.\n"
        "- **Recommendations**: suggested corrections or next steps."
    )


@prompts_mcp.prompt()
def diagnose_flooding(
    session_id: str = "default",
    node_ids: str | None = None,
) -> str:
    """Return a flooding-investigation prompt.

    If *node_ids* is provided (comma-separated), the investigation is
    scoped to those specific nodes; otherwise the LLM should discover
    flooded nodes from the simulation results.

    Parameters
    ----------
    session_id:
        Session to investigate.
    node_ids:
        Optional comma-separated list of node IDs to focus on.
    """
    node_clause = ""
    if node_ids:
        ids = [n.strip() for n in node_ids.split(",") if n.strip()]
        formatted = ", ".join(f"'{n}'" for n in ids)
        node_clause = f"\nFocus your investigation on these specific nodes: {formatted}.\n"

    return (
        f"Investigate flooding in session '{session_id}'.{node_clause}\n"
        "\n"
        "Follow these steps:\n"
        "1. Use the `get_flooding_summary` tool to identify nodes that "
        "experienced flooding.\n"
        "2. For each flooded node, read "
        f"`swmm://session/{session_id}/nodes/{{node_id}}` to inspect "
        "invert elevation, maximum depth, and current state.\n"
        "3. Examine upstream and downstream links using "
        f"`swmm://session/{session_id}/links/{{link_id}}` to check for "
        "capacity bottlenecks (look at max filling and velocity).\n"
        "4. Check the `get_time_series` tool for the flooding nodes to see "
        "if the flooding is brief surcharging or sustained overflow.\n"
        "5. Review subcatchment runoff feeding into flooded nodes.\n"
        f"6. Read `swmm://session/{session_id}/mass_balance` to check "
        "overall continuity errors that could indicate numerical issues.\n"
        "\n"
        "Produce a diagnosis that includes:\n"
        "- **Flooding Summary**: which nodes flood, peak overflow rate, "
        "total flood volume, duration.\n"
        "- **Root Cause Analysis**: capacity constraints, insufficient "
        "storage, backwater effects, or high inflows.\n"
        "- **Hydraulic Context**: upstream/downstream conditions at the "
        "time of flooding.\n"
        "- **Mitigation Options**: specific parameter changes, upsizing, "
        "storage additions, or LID controls that could reduce flooding."
    )


@prompts_mcp.prompt()
def compare_scenarios(session_a: str, session_b: str) -> str:
    """Return a cross-scenario comparison prompt.

    Parameters
    ----------
    session_a:
        Identifier of the first (baseline) session.
    session_b:
        Identifier of the second (alternative) session.
    """
    return (
        f"Compare the simulation results of session '{session_a}' "
        f"(baseline) against session '{session_b}' (alternative).\n"
        "\n"
        "Follow these steps:\n"
        f"1. Read `swmm://session/{session_a}/summary` and "
        f"`swmm://session/{session_b}/summary` side by side.\n"
        f"2. Read `swmm://session/{session_a}/mass_balance` and "
        f"`swmm://session/{session_b}/mass_balance` to compare continuity "
        "errors.\n"
        "3. Use `get_flooding_summary` on both sessions to compare flooding "
        "extents.\n"
        "4. Use `get_capacity_summary` on both sessions to compare conduit "
        "utilization.\n"
        "5. For nodes or links with the largest differences, use "
        "`get_time_series` on both sessions to compare temporal behaviour.\n"
        "\n"
        "Produce a comparison report with:\n"
        "- **Overview**: what changed between the two scenarios (model "
        "inputs, parameters, or boundary conditions).\n"
        "- **Flooding Comparison**: nodes that flood more or less, volume "
        "and peak-rate differences.\n"
        "- **Capacity Comparison**: links that are more or less utilized.\n"
        "- **Mass Balance**: changes in continuity error.\n"
        "- **Conclusion**: whether the alternative scenario is an "
        "improvement, and any trade-offs observed."
    )


@prompts_mcp.prompt()
def design_review(
    session_id: str = "default",
    standard: str | None = None,
) -> str:
    """Return a design-standards-check prompt.

    Parameters
    ----------
    session_id:
        Session to review.
    standard:
        Optional name of the design standard or regulatory framework to
        check against (e.g. ``"10-year"`` or ``"local municipal code"``).
    """
    standard_clause = ""
    if standard:
        standard_clause = (
            f"\nEvaluate the model against the '{standard}' design standard. "
            "Flag any elements that do not meet the criteria.\n"
        )

    return (
        f"Perform a design review of the model in session "
        f"'{session_id}'.{standard_clause}\n"
        "\n"
        "Follow these steps:\n"
        f"1. Read `swmm://session/{session_id}/summary` and "
        f"`swmm://session/{session_id}/options` for an overview.\n"
        "2. Use `get_capacity_summary` to identify conduits operating above "
        "design capacity.\n"
        "3. Use `get_flooding_summary` to identify nodes that flood under "
        "the design storm.\n"
        "4. Examine key hydraulic parameters: pipe slopes, Manning's "
        "roughness, crown elevations relative to ground.\n"
        "5. Check subcatchment parameters for reasonableness: percent "
        "impervious, width, slope.\n"
        "6. Verify outfall boundary conditions.\n"
        "\n"
        "Produce a design review report with:\n"
        "- **Pass/Fail Summary**: count of elements meeting vs. failing "
        "criteria.\n"
        "- **Capacity Issues**: conduits exceeding design flow.\n"
        "- **Flooding Issues**: nodes exceeding allowable surcharge.\n"
        "- **Parameter Concerns**: elements with questionable inputs.\n"
        "- **Recommendations**: prioritised list of design changes."
    )


@prompts_mcp.prompt()
def what_if(session_id: str = "default", description: str = "") -> str:
    """Return a what-if scenario setup prompt.

    The generated instructions guide the LLM through cloning a session,
    applying modifications, re-running, and comparing results.

    Parameters
    ----------
    session_id:
        The baseline session to branch from.
    description:
        Plain-language description of the scenario the user wants to test.
    """
    desc_clause = ""
    if description:
        desc_clause = (
            f'\nThe user wants to explore: "{description}"\n'
            "Translate this description into concrete model changes.\n"
        )

    return (
        f"Set up and evaluate a what-if scenario based on session "
        f"'{session_id}'.{desc_clause}\n"
        "\n"
        "Follow these steps:\n"
        f"1. Use `clone_session` to create a copy of '{session_id}' with a "
        "descriptive target_id (e.g. 'whatif_1').\n"
        "2. Apply the desired modifications using the appropriate tools:\n"
        "   - `set_forcing` for rainfall or inflow changes.\n"
        "   - `set_param` for conduit, node, or subcatchment property "
        "changes.\n"
        "   - `add_lid` for green infrastructure additions.\n"
        "   - `set_treatment` for water-quality modifications.\n"
        "3. Run the modified scenario using `run_simulation` or "
        "`step_simulation`.\n"
        f"4. Use the `compare_scenarios` prompt with '{session_id}' as "
        "baseline and the new session as alternative.\n"
        "\n"
        "Present the results as:\n"
        "- **Scenario Description**: what was changed and why.\n"
        "- **Key Differences**: flooding, capacity, and water-quality "
        "changes.\n"
        "- **Assessment**: whether the scenario achieves the desired "
        "outcome.\n"
        "- **Next Steps**: further refinements or additional scenarios "
        "to test."
    )


@prompts_mcp.prompt()
def build_simple_model(description: str) -> str:
    """Return a guided model-construction prompt.

    Parameters
    ----------
    description:
        Plain-language description of the drainage network to build
        (e.g. ``"three subcatchments draining to a single outfall"``).
    """
    return (
        "Build a SWMM model from scratch based on the following "
        f'description:\n\n"{description}"\n\n'
        "Follow these steps:\n"
        "1. Use `create_model` to initialise an empty model.\n"
        "2. Add nodes using `add_node` for each junction, outfall, and "
        "storage unit described.\n"
        "3. Connect them with `add_link`, choosing appropriate conduit "
        "types and cross-sections.\n"
        "4. Add subcatchments with `add_subcatchment`, assigning "
        "reasonable hydrological parameters (area, width, slope, percent "
        "impervious) based on the description.\n"
        "5. Set coordinates using `set_coordinates` to give the network "
        "a logical spatial layout.\n"
        "6. Configure simulation options with `set_option` (flow units, "
        "routing method, time-step, simulation period).\n"
        "7. Add a rain gage and assign it to the subcatchments.\n"
        "8. Save the model with `save_model`.\n"
        "9. Open and run a quick test simulation to check for errors.\n"
        "\n"
        "After building the model, report:\n"
        "- **Model Structure**: a summary table of all elements created.\n"
        "- **Parameter Choices**: rationale for the values selected.\n"
        "- **Test Run Results**: whether the model ran successfully and "
        "the continuity errors.\n"
        "- **Suggestions**: any improvements or additional detail the "
        "user may want to add."
    )


@prompts_mcp.prompt()
def explain_results(session_id: str = "default") -> str:
    """Return a plain-language result-interpretation prompt.

    Parameters
    ----------
    session_id:
        The session whose results should be explained.
    """
    return (
        f"Explain the simulation results for session '{session_id}' in "
        "plain, non-technical language suitable for a project stakeholder.\n"
        "\n"
        "Follow these steps:\n"
        f"1. Read `swmm://session/{session_id}/summary` for overall model "
        "information.\n"
        f"2. Read `swmm://session/{session_id}/mass_balance` to check "
        "whether the simulation is numerically reliable.\n"
        "3. Use `get_flooding_summary` to identify any locations where "
        "water overflows.\n"
        "4. Use `get_capacity_summary` to identify pipes that are near or "
        "at full capacity.\n"
        "5. If pollutants are modelled, check water-quality results with "
        "`get_quality`.\n"
        "\n"
        "Present the explanation as:\n"
        "- **What Was Simulated**: brief description of the model, storm "
        "event, and duration.\n"
        "- **How the System Performed**: did the drainage system handle "
        "the rainfall? Where did problems occur?\n"
        "- **Flooding**: describe any flooding locations in everyday terms "
        "(streets, intersections) if coordinate data is available.\n"
        "- **Pipe Capacity**: highlight any pipes that are struggling to "
        "carry the flow.\n"
        "- **Water Quality** (if applicable): summarise pollutant levels "
        "and whether treatment goals are met.\n"
        "- **Bottom Line**: one-paragraph summary of the key take-away "
        "and recommended actions."
    )
