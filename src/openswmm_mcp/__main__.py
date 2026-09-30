"""Entry point: python -m openswmm_mcp"""

from openswmm_mcp.config import settings
from openswmm_mcp.server import mcp


def main():
    """Run on OPENSWMM_MCP_TRANSPORT (stdio by default; http/sse on OPENSWMM_MCP_HTTP_PORT)."""
    if settings.transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport=settings.transport, port=settings.http_port)


if __name__ == "__main__":
    main()
