"""Sphinx configuration for the OpenSWMM MCP Server documentation."""

from contextlib import ExitStack
from importlib import import_module

from sphinx.ext.autodoc.mock import mock

project = "OpenSWMM MCP Server"
copyright = "2026, Caleb Buahin"
author = "Caleb Buahin"
version = "0.2.0"
release = "0.2.0.dev0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx_epytext",  # Gym support API docstrings use epytext.
]

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    # Plans, handoffs, test instructions and verification write-ups are working
    # records: not committed, and not published if present in a local checkout.
    "*_PLAN*.md",
    "**/*_PLAN*.md",
    "**/*HANDOFF*.md",
    "**/*VERIFICATION*.md",
    "**/*_INSTRUCTIONS.md",
]

html_theme = "pydata_sphinx_theme"
html_theme_options = {
    "github_url": "https://github.com/HydroCouple/openswmm.mcp",
    "show_toc_level": 2,
    "logo": {
        "image_light": "../images/hydrocouplecomposer.png",
        "image_dark": "../images/hydrocouplecomposer.png",
        "text": "OpenSWMM MCP",
    },
}
html_logo = "../images/hydrocouplecomposer.png"
html_favicon = "../images/hydrocouplecomposer.png"

# Napoleon
napoleon_google_docstring = True
napoleon_numpy_docstring = True
# Render docstring "Attributes:" sections as :ivar: fields rather than
# standalone .. attribute:: directives, so they don't collide with the
# same members documented by autodoc ``:members:`` (duplicate-object warnings).
napoleon_use_ivar = True

# Intersphinx
intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
}

# MyST
myst_enable_extensions = ["colon_fence", "deflist"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}

# Autodoc
autodoc_default_options = {"members": True, "undoc-members": True}
autosummary_generate = True

# Suppress duplicate-object-description warnings from inherited members
# in the Backend subclasses (OpenSWMMBackend / LegacyBackend each
# redeclare the abstract members of Backend).
suppress_warnings = ["ref.python", "duplicate"]

# Documentation imports the Python API without the compiled engine or optional integrations.
autodoc_mock_imports = ["openswmm", "fastmcp", "openswmm_gymnasium"]

# The server checks catalog compatibility at import time. Keep the engine mock
# alive across autodoc imports and give it the minimum revision expected by
# openswmm_mcp.catalog.MIN_CATALOG_REVISION; no engine methods run in this build.
_docs_mocks = ExitStack()
_docs_mocks.enter_context(mock(["openswmm"]))
_engine = import_module("openswmm.engine")
_catalog = _engine.catalog
_catalog.revision = lambda: 2
_engine.catalog = _catalog


def setup(app):
    app.connect("build-finished", lambda app, exception: _docs_mocks.close())
