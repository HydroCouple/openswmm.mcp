"""Root MCP server module -- creates the FastMCP instance and mounts sub-servers.

The ``mcp`` object defined here is the single entry point used by
``__main__.py`` (``python -m openswmm_mcp``) and by any ASGI / transport
runner that needs a reference to the application.

Sub-servers are mounted with *namespace* prefixes so that tool names are
scoped to their domain (e.g. ``lifecycle_open_model``, ``query_get_node``).
Resources and prompts are mounted without a namespace so their URIs remain
short.
"""

from __future__ import annotations

from fastmcp import FastMCP

from openswmm_mcp.dependencies import server_lifespan

# ---------------------------------------------------------------------------
# Core server instance
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "OpenSWMM MCP Server",
    instructions=(
        "SWMM stormwater engine tools for LLM-driven modeling workflows.\n\n"
        "This server also bundles reusable Agent Skills -- multi-step expert "
        "workflows that are NOT auto-loaded over MCP. Before improvising with raw "
        "tools on a substantive request, check whether a skill applies: read the "
        "`swmm://skills` resource (a JSON index of {name, description}); if one "
        "matches the user's intent, load its full instructions via the "
        "`use_skill(skill_name)` prompt and follow that workflow rather than "
        "reinventing it. In particular: for capacity / constraint / bottleneck / "
        "flooding-or-surcharge assessment and reporting, use `capacity-assessment` "
        "(it drives the standard assessment + plotting/dashboard workflow); for "
        "calibrating against observed data use `calibrate-model`; for real-time / "
        "agent-based control tuning use `operational-optimization`; for a quick "
        "pass/fail check against a specific design storm (return period, "
        "development review) use `design-storm-check`; for overland/2D mesh flood "
        "extent and depth-velocity hazard mapping use `overland-flood-mapping`; "
        "and before trusting an unfamiliar model (or before running any of the "
        "above on one), consider `model-qa-check` to catch authoring errors first. "
        "For a broad start-to-finish tour of every tool namespace instead of one "
        "of these specific workflows -- e.g. the user is new to this server, asks "
        "what it can do, or wants a general walkthrough of an unfamiliar model -- "
        "use `getting-started`."
    ),
    lifespan=server_lifespan,
)

# ---------------------------------------------------------------------------
# Tool sub-servers (namespaced)
# ---------------------------------------------------------------------------

from openswmm_mcp.tools import gym_runs, gym_scoring  # noqa: E402, F401  (register on gym_mcp)
from openswmm_mcp.tools.analysis import analysis_mcp  # noqa: E402
from openswmm_mcp.tools.building import building_mcp  # noqa: E402
from openswmm_mcp.tools.climate import climate_mcp  # noqa: E402
from openswmm_mcp.tools.controls import controls_mcp  # noqa: E402
from openswmm_mcp.tools.datetime_tools import datetime_mcp  # noqa: E402
from openswmm_mcp.tools.editing import editing_mcp  # noqa: E402
from openswmm_mcp.tools.forcing import forcing_mcp  # noqa: E402
from openswmm_mcp.tools.geopackage import geopackage_mcp  # noqa: E402
from openswmm_mcp.tools.gym_envs import gym_mcp  # noqa: E402
from openswmm_mcp.tools.hotstart import hotstart_mcp  # noqa: E402
from openswmm_mcp.tools.inflows import inflows_mcp  # noqa: E402
from openswmm_mcp.tools.infrastructure import infrastructure_mcp  # noqa: E402
from openswmm_mcp.tools.lifecycle import lifecycle_mcp  # noqa: E402
from openswmm_mcp.tools.links import links_mcp  # noqa: E402
from openswmm_mcp.tools.model import model_mcp  # noqa: E402
from openswmm_mcp.tools.nodes import nodes_mcp  # noqa: E402
from openswmm_mcp.tools.pollutants import pollutants_mcp  # noqa: E402
from openswmm_mcp.tools.quality import quality_mcp  # noqa: E402
from openswmm_mcp.tools.query import query_mcp  # noqa: E402
from openswmm_mcp.tools.spatial_quality import spatial_quality_mcp  # noqa: E402
from openswmm_mcp.tools.subcatchments import subcatchments_mcp  # noqa: E402
from openswmm_mcp.tools.tables import tables_mcp  # noqa: E402
from openswmm_mcp.tools.twod import twod_mcp  # noqa: E402
from openswmm_mcp.tools.xsect import xsect_mcp  # noqa: E402

mcp.mount(lifecycle_mcp, namespace="lifecycle")
mcp.mount(query_mcp, namespace="query")
mcp.mount(forcing_mcp, namespace="forcing")
mcp.mount(analysis_mcp, namespace="analysis")
mcp.mount(building_mcp, namespace="building")
mcp.mount(editing_mcp, namespace="editing")
mcp.mount(hotstart_mcp, namespace="hotstart")
mcp.mount(spatial_quality_mcp, namespace="spatial")
mcp.mount(geopackage_mcp, namespace="geopackage")
mcp.mount(tables_mcp, namespace="tables")
mcp.mount(inflows_mcp, namespace="inflows")
mcp.mount(controls_mcp, namespace="controls")
mcp.mount(climate_mcp, namespace="climate")
mcp.mount(infrastructure_mcp, namespace="infrastructure")
mcp.mount(nodes_mcp, namespace="nodes")
mcp.mount(links_mcp, namespace="links")
mcp.mount(subcatchments_mcp, namespace="subcatchments")
mcp.mount(pollutants_mcp, namespace="pollutants")
mcp.mount(model_mcp, namespace="model")
mcp.mount(quality_mcp, namespace="quality")
mcp.mount(twod_mcp, namespace="twod")
mcp.mount(datetime_mcp, namespace="datetime")
mcp.mount(xsect_mcp, namespace="xsect")
mcp.mount(gym_mcp, namespace="gym")

# ---------------------------------------------------------------------------
# Resource and prompt sub-servers (no namespace -- keep URIs short)
# ---------------------------------------------------------------------------

from openswmm_mcp.prompts.workflows import prompts_mcp  # noqa: E402
from openswmm_mcp.resources.model import resources_mcp  # noqa: E402
from openswmm_mcp.skills import skills_mcp  # noqa: E402

mcp.mount(resources_mcp)
mcp.mount(prompts_mcp)
mcp.mount(skills_mcp)
