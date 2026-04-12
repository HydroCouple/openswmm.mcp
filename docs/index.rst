OpenSWMM MCP Server
====================

The **OpenSWMM MCP Server** exposes the OpenSWMM stormwater engine through
the `Model Context Protocol <https://modelcontextprotocol.io/>`_ (MCP),
allowing large-language models and AI assistants to open, run, query, and
modify EPA-SWMM hydraulic and hydrologic models.

Built on `FastMCP 3.x <https://github.com/jlowin/fastmcp>`_, the server
provides over 35 tools organised into seven domain namespaces, nine
``swmm://`` resources for structured data access, and seven guided-workflow
prompts for common stormwater modelling tasks.

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
   user-guide/resources
   user-guide/prompts
   user-guide/examples

.. toctree::
   :maxdepth: 2
   :caption: Developer Guide

   developer/architecture
   developer/contributing
   developer/testing

.. toctree::
   :maxdepth: 2
   :caption: API Reference

   api/index

Indices and tables
------------------

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
