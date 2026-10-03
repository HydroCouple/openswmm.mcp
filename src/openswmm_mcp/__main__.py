"""Entry point: python -m openswmm_mcp"""

import os

import fastmcp

from openswmm_mcp.config import settings
from openswmm_mcp.server import mcp


def main():
    """Run on OPENSWMM_MCP_TRANSPORT (stdio by default; http/sse on OPENSWMM_MCP_HTTP_PORT)."""
    # FastMCP's startup banner queries pypi.org for a newer FastMCP release.
    # The server makes no network connections of its own (see PRIVACY.md), so
    # that check is off unless the user opts in via FASTMCP_CHECK_FOR_UPDATES.
    if "FASTMCP_CHECK_FOR_UPDATES" not in os.environ:
        fastmcp.settings.check_for_updates = "off"
    if settings.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=settings.transport, port=settings.http_port)


if __name__ == "__main__":
    main()
