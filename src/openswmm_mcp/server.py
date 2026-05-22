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
    instructions="SWMM stormwater engine tools for LLM-driven modeling workflows",
    lifespan=server_lifespan,
)

# ---------------------------------------------------------------------------
# Tool sub-servers (namespaced)
# ---------------------------------------------------------------------------

from openswmm_mcp.tools.analysis import analysis_mcp  # noqa: E402
from openswmm_mcp.tools.building import building_mcp  # noqa: E402
from openswmm_mcp.tools.controls import controls_mcp  # noqa: E402
from openswmm_mcp.tools.editing import editing_mcp  # noqa: E402
from openswmm_mcp.tools.forcing import forcing_mcp  # noqa: E402
from openswmm_mcp.tools.geopackage import geopackage_mcp  # noqa: E402
from openswmm_mcp.tools.hotstart import hotstart_mcp  # noqa: E402
from openswmm_mcp.tools.inflows import inflows_mcp  # noqa: E402
from openswmm_mcp.tools.infrastructure import infrastructure_mcp  # noqa: E402
from openswmm_mcp.tools.lifecycle import lifecycle_mcp  # noqa: E402
from openswmm_mcp.tools.nodes import nodes_mcp  # noqa: E402
from openswmm_mcp.tools.query import query_mcp  # noqa: E402
from openswmm_mcp.tools.spatial_quality import spatial_quality_mcp  # noqa: E402
from openswmm_mcp.tools.tables import tables_mcp  # noqa: E402

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
mcp.mount(infrastructure_mcp, namespace="infrastructure")
mcp.mount(nodes_mcp, namespace="nodes")

# ---------------------------------------------------------------------------
# Resource and prompt sub-servers (no namespace -- keep URIs short)
# ---------------------------------------------------------------------------

from openswmm_mcp.prompts.workflows import prompts_mcp  # noqa: E402
from openswmm_mcp.resources.model import resources_mcp  # noqa: E402

mcp.mount(resources_mcp)
mcp.mount(prompts_mcp)
