# Configuration

The OpenSWMM MCP Server is configured through environment variables, all
prefixed with `OPENSWMM_MCP_`. These can be set in your shell, in a `.env`
file, or in the Claude Code MCP server configuration.

## Environment Variables

| Variable | Type | Default | Description |
|---|---|---|---|
| `OPENSWMM_MCP_WORKING_DIR` | `str` | `"./"` | Base directory for model files and session working directories. |
| `OPENSWMM_MCP_MAX_SESSIONS` | `int` | `5` | Maximum number of concurrent simulation sessions. |
| `OPENSWMM_MCP_TRANSPORT` | `str` | `"stdio"` | Transport mode: `"stdio"`, `"http"`, or `"sse"`. |
| `OPENSWMM_MCP_HTTP_PORT` | `int` | `8080` | Port for HTTP/SSE transport (ignored for stdio). |
| `OPENSWMM_MCP_LOG_LEVEL` | `str` | `"INFO"` | Logging level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `OPENSWMM_MCP_TOOLSETS` | `str` | `"core"` | Tool sets to register: `core` (14 tools), or `core,gym` to add the 5 gym tools. |
| `OPENSWMM_MCP_ENABLE_PYTHON` | `bool` | `false` | Register `run_python` (stdio transport only). |
| `OPENSWMM_MCP_OAUTH_ISSUER` | `str` | `None` | OAuth token issuer URL (HTTP transport only). |
| `OPENSWMM_MCP_OAUTH_AUDIENCE` | `str` | `None` | OAuth audience identifier (HTTP transport only). |
| `OPENSWMM_MCP_JWT_JWKS_URL` | `str` | `None` | JWKS endpoint for JWT verification (HTTP transport only). |

## Tool sets

The default `core` set is 14 tools, about 12k characters of definitions.
`core,gym` adds the five `gym_*` tools (about 17k characters in all) and needs
the `gym` extra (`pip install 'openswmm.mcp[gym]'`); if the extra is missing,
the server refuses to start and names it.

`OPENSWMM_MCP_ENABLE_PYTHON=true` adds `run_python`, which runs Python with a
session's `Solver` in scope. It runs code with the server's own permissions,
so it is off by default and never registered on the HTTP or SSE transports.

A refused tool call (a missing argument, a lifecycle phase, an unknown field)
is logged as one line rather than a traceback; the client receives the reason
as the tool error.

## Claude Desktop

Add the server to `claude_desktop_config.json` (on macOS in
`~/Library/Application Support/Claude/`, on Windows in `%APPDATA%\Claude\`).
Claude Desktop does not read your shell's `PATH`, so give the full path of the
Python environment where `openswmm.mcp` is installed:

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "openswmm_mcp"],
      "env": {
        "OPENSWMM_MCP_WORKING_DIR": "/Users/me/swmm-models",
        "OPENSWMM_MCP_TOOLSETS": "core,gym"
      }
    }
  }
}
```

Restart Claude Desktop after editing the file.

## Claude Code JSON Configuration

When using the server with Claude Code, configure it in
`.claude/settings.json`:

```json
{
  "mcpServers": {
    "openswmm": {
      "command": "openswmm.mcp",
      "args": [],
      "env": {
        "OPENSWMM_MCP_WORKING_DIR": "/Users/me/swmm-models",
        "OPENSWMM_MCP_MAX_SESSIONS": "10",
        "OPENSWMM_MCP_LOG_LEVEL": "DEBUG"
      }
    }
  }
}
```

## Transport Modes

### stdio (default)

The default transport mode. The server communicates over standard input/output
and is intended for use with MCP clients like Claude Code that manage the
server process directly. No authentication is required.

```bash
OPENSWMM_MCP_TRANSPORT=stdio openswmm.mcp
```

### HTTP

Runs the server as an HTTP endpoint with optional OAuth/JWT authentication.
Suitable for multi-user or remote deployments.

```bash
OPENSWMM_MCP_TRANSPORT=http \
OPENSWMM_MCP_HTTP_PORT=8080 \
OPENSWMM_MCP_OAUTH_ISSUER=https://accounts.example.com \
OPENSWMM_MCP_OAUTH_AUDIENCE=openswmm-mcp \
OPENSWMM_MCP_JWT_JWKS_URL=https://accounts.example.com/.well-known/jwks.json \
openswmm.mcp
```

### SSE (Server-Sent Events)

Similar to HTTP but uses Server-Sent Events for streaming progress updates.
Useful for browser-based clients.

```bash
OPENSWMM_MCP_TRANSPORT=sse \
OPENSWMM_MCP_HTTP_PORT=8080 \
openswmm.mcp
```

## Authentication (HTTP Transport)

When the transport is set to `http` or `sse`, the server supports two
authentication methods:

1. **OAuth**: For interactive users authenticating through a browser-based
   flow. Configure `OPENSWMM_MCP_OAUTH_ISSUER` and
   `OPENSWMM_MCP_OAUTH_AUDIENCE`.

2. **JWT**: For service-to-service authentication (API keys, CI/CD pipelines).
   Configure `OPENSWMM_MCP_JWT_JWKS_URL` and `OPENSWMM_MCP_OAUTH_AUDIENCE`.

Both methods can be active simultaneously. When using stdio transport,
authentication is bypassed entirely.
