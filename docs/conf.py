"""Sphinx configuration for the OpenSWMM MCP Server documentation."""

project = "OpenSWMM MCP Server"
copyright = "2026, Caleb Buahin"
author = "Caleb Buahin"
version = "0.1.0"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "myst_parser",
]

templates_path = ["_templates"]
exclude_patterns = ["_build"]

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

# Don't fail if openswmm C extensions aren't installed
autodoc_mock_imports = ["openswmm", "openswmm.engine", "fastmcp"]
