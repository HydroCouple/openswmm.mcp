# Prompts

The OpenSWMM MCP Server includes seven guided-workflow prompts. Prompts
are pre-written instruction templates that tell the LLM how to combine tools
and resources to accomplish common stormwater modelling tasks. They are
invoked through the MCP `get_prompt` method.

## Available Prompts

### `analyze_model`

Perform a comprehensive review of a SWMM model.

| Parameter | Type | Description |
|---|---|---|
| `inp_path` | `str` | Path to the `.inp` file to analyse. |

**Workflow:** Opens the model, reads summary/options/elements resources, and
produces a structured report covering model overview, network topology,
hydrology settings, hydraulic parameters, potential issues, and
recommendations.

---

### `diagnose_flooding`

Investigate flooding in a simulation session.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session to investigate. |
| `node_ids` | `str` | `None` | Comma-separated node IDs to focus on. |

**Workflow:** Uses `get_flooding_summary` to identify flooded nodes, inspects
node and link details through resources, checks time series for flooding
duration, reviews subcatchment runoff and mass balance, then produces a
diagnosis with root cause analysis and mitigation options.

---

### `compare_scenarios`

Compare simulation results between two sessions.

| Parameter | Type | Description |
|---|---|---|
| `session_a` | `str` | Baseline session identifier. |
| `session_b` | `str` | Alternative session identifier. |

**Workflow:** Reads summaries and mass balance from both sessions, compares
flooding and capacity summaries, examines time-series differences for key
elements, and produces a comparison report highlighting improvements and
trade-offs.

---

### `design_review`

Evaluate a model against design standards.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session to review. |
| `standard` | `str` | `None` | Design standard name (e.g. `"10-year"`). |

**Workflow:** Reads model summary and options, checks capacity and flooding
summaries, examines hydraulic parameters and subcatchment settings, and
produces a pass/fail report with prioritised recommendations.

---

### `what_if`

Set up and evaluate a what-if scenario.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Baseline session to branch from. |
| `description` | `str` | `""` | Plain-language description of the scenario. |

**Workflow:** Clones the baseline session, translates the description into
concrete model changes (forcing, parameters, LID, treatment), runs the
modified scenario, and compares results against the baseline.

---

### `build_simple_model`

Build a SWMM model from scratch based on a description.

| Parameter | Type | Description |
|---|---|---|
| `description` | `str` | Plain-language description of the drainage network. |

**Workflow:** Creates an empty model, adds nodes/links/subcatchments/gages
based on the description, sets coordinates and simulation options, saves the
model, runs a test simulation, and reports the structure, parameter choices,
test results, and suggestions for improvement.

---

### `explain_results`

Explain simulation results in plain, non-technical language.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `session_id` | `str` | `"default"` | Session whose results to explain. |

**Workflow:** Reads model summary and mass balance, checks flooding and
capacity summaries, reviews water-quality results if applicable, and
presents the explanation in stakeholder-friendly terms covering system
performance, flooding locations, pipe capacity, and a bottom-line summary.
