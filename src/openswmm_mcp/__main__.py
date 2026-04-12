"""Entry point: python -m openswmm_mcp"""

from openswmm_mcp.server import mcp


def main():
    mcp.run()


if __name__ == "__main__":
    main()
