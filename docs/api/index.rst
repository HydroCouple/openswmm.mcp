API Reference
=============

This section provides auto-generated API documentation for every public
module in the ``openswmm_mcp`` package.

.. contents:: Modules
   :local:
   :depth: 1

Core Modules
------------

openswmm_mcp
~~~~~~~~~~~~

.. automodule:: openswmm_mcp
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.server
~~~~~~~~~~~~~~~~~~~

The FastMCP composition root.  Mounts every tool / resource / prompt
sub-server.

.. automodule:: openswmm_mcp.server
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.config
~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.config
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.session
~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.session
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.dependencies
~~~~~~~~~~~~~~~~~~~~~~~~~

Cross-tool dependency helpers (session lookups, state guards, engine
acquisition).

.. automodule:: openswmm_mcp.dependencies
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.models
~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.models
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.errors
~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.errors
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.auth
~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.auth
   :members:
   :undoc-members:
   :show-inheritance:

Backend Modules
---------------

The backend layer abstracts the underlying SWMM engine so that the same
tool surface can drive either the refactored ``openswmm.engine`` (v6.0)
or the legacy SWMM 5 solver.

openswmm_mcp.backends.base
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.backends.base
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.backends.openswmm
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.backends.openswmm
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.backends.legacy
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.backends.legacy
   :members:
   :undoc-members:
   :show-inheritance:

Utility Modules
---------------

openswmm_mcp._util.formatting
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp._util.formatting
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp._util.validation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp._util.validation
   :members:
   :undoc-members:
   :show-inheritance:

Tool Modules
------------

Each tool module exposes a FastMCP sub-server whose tools are mounted
under the matching namespace prefix (e.g. ``tools.nodes`` →
``nodes_*`` tools).

openswmm_mcp.tools.lifecycle
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.lifecycle
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.query
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.query
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.forcing
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.forcing
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.controls
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.controls
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.analysis
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.analysis
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.building
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.building
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.editing
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.editing
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.model
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.model
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.nodes
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.nodes
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.links
~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.links
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.subcatchments
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.subcatchments
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.inflows
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.inflows
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.pollutants
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.pollutants
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.quality
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.quality
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.tables
~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.tables
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.infrastructure
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.infrastructure
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.hotstart
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.hotstart
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.spatial_quality
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.spatial_quality
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.geopackage
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.geopackage
   :members:
   :undoc-members:
   :show-inheritance:

openswmm_mcp.tools.twod
~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.tools.twod
   :members:
   :undoc-members:
   :show-inheritance:

Resource Modules
----------------

openswmm_mcp.resources.model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.resources.model
   :members:
   :undoc-members:
   :show-inheritance:

Prompt Modules
--------------

openswmm_mcp.prompts.workflows
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. automodule:: openswmm_mcp.prompts.workflows
   :members:
   :undoc-members:
   :show-inheritance:
