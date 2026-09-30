OpenSWMM MCP Server
====================

The **OpenSWMM MCP Server** exposes the OpenSWMM stormwater engine through
the `Model Context Protocol <https://modelcontextprotocol.io/>`_ (MCP),
allowing large-language models and AI assistants to open, run, query, and
modify EPA-SWMM hydraulic and hydrologic models.

Built on `FastMCP 3.x <https://gofastmcp.com/>`_, the server provides 14 core
tools (plus five optional gym tools) that reach every property and method of
the engine through its machine-readable catalog, ``swmm://`` resources for the
catalog and sessions, seven guided-workflow prompts and three bundled skills.
The whole tool set costs under 5k tokens of context, so it loads in any MCP
client.

.. toctree::
   :maxdepth: 2
   :caption: Getting Started

   getting-started/installation
   getting-started/configuration
   getting-started/quickstart

.. toctree::
   :maxdepth: 2
   :caption: User Guide

   user-guide/tools
   user-guide/optimization
   user-guide/resources
   user-guide/prompts
   user-guide/examples

.. toctree::
   :maxdepth: 2
   :caption: Developer Guide

   developer/architecture
   developer/contributing
   developer/testing
   developer/SKILLS
   developer/RTC_AGENT_FEEDBACK_DISPOSITION

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/index

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
