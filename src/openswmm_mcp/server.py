"""Root MCP server: one FastMCP instance with a small, catalog-driven tool set.

Tool sets (``OPENSWMM_MCP_TOOLSETS``):

* ``core`` (always) -- 14 tools: sessions and runs, catalog access, results.
* ``gym`` -- 5 tools for openswmm.gymnasium environments and optimisation jobs.
* ``run_python`` -- registered only with ``OPENSWMM_MCP_ENABLE_PYTHON=true`` on stdio.
"""

from __future__ import annotations

from fastmcp import FastMCP

from openswmm_mcp.auth import create_auth
from openswmm_mcp.config import ServerSettings
from openswmm_mcp.dependencies import server_lifespan, toolsets
from openswmm_mcp.prompts.workflows import prompts_mcp
from openswmm_mcp.resources import resources_mcp
from openswmm_mcp.skills import skills_mcp
from openswmm_mcp.tools import access, model, results

INSTRUCTIONS = (
    "Tools for the OpenSWMM stormwater engine. Typical flow: open_model -> run -> report / "
    "timeseries / compare. Everything the engine exposes is reachable through five generic "
    "tools driven by its catalog: describe() to discover element kinds, services, fields and "
    "methods; find/get/set to read and write fields; call to run any listed method. Values "
    "are in the model's units (describe with session_id resolves them); enums are passed by "
    "name. Bundled skills (swmm://skills; load with the use_skill prompt): "
    "capacity-assessment, calibrate-model, operational-optimization."
)

CORE_TOOLS = (
    model.open_model,
    model.run,
    model.session,
    model.save,
    access.describe,
    access.find,
    access.get,
    access.set_fields,
    access.call,
    model.edit,
    results.timeseries,
    results.report,
    results.compare,
    results.export,
)


def build_server(settings: ServerSettings | None = None) -> FastMCP:
    settings = settings or ServerSettings()
    server = FastMCP(
        "OpenSWMM",
        instructions=INSTRUCTIONS,
        lifespan=server_lifespan,
        auth=create_auth(settings),  # None on stdio
    )
    for fn in CORE_TOOLS:
        server.tool(fn, name="set" if fn is access.set_fields else fn.__name__)
    if "gym" in toolsets(settings):
        from openswmm_mcp.tools import gym

        for fn in gym.TOOLS:
            server.tool(fn)
    if settings.enable_python and settings.transport == "stdio":
        from openswmm_mcp.tools.code import run_python

        server.tool(run_python)
    server.mount(resources_mcp)
    server.mount(prompts_mcp)
    server.mount(skills_mcp)
    return server


mcp = build_server()
