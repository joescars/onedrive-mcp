# Client configuration

[Back to README](../README.md)

The server uses MCP over stdio. Complete [setup and sign-in](setup.md) first;
the running server can refresh cached credentials but cannot perform an
interactive sign-in from chat.

A client starts the server process when needed. Running `server.py` separately
does not create a service for stdio clients to connect to.

## VS Code with GitHub Copilot

Use a current VS Code release with GitHub Copilot Chat installed and signed in.
You do not need `mcpo`, an HTTP port, or a bridge API key.

### Choose where the server runs

Linux is recommended. In a VS Code WSL or Remote-SSH window, install the
project, create the virtual environment, and sign in on that Linux host. Keep
the checkout, token cache, and downloads in its Linux filesystem rather than a
Windows-mounted or OneDrive folder. Do not reuse a Windows virtual environment
in Linux.

### Configure the server

1. Open the Command Palette.
2. Choose the appropriate scope:
   - **MCP: Open User Configuration** for all local workspaces in the current
     profile.
   - **MCP: Open Remote User Configuration** in WSL or Remote-SSH.
   - `.vscode/mcp.json` for one workspace.
3. Add an `onedrive` entry under the top-level `servers` object.

Linux user or remote-user configuration:

```json
{
  "servers": {
    "onedrive": {
      "type": "stdio",
      "command": "/home/you/onedrive-mcp/venv/bin/python",
      "args": ["/home/you/onedrive-mcp/server.py"]
    }
  }
}
```

For this repository's workspace configuration, the paths may be relative to
the workspace:

```json
{
  "servers": {
    "onedrive": {
      "type": "stdio",
      "command": "${workspaceFolder}/venv/bin/python",
      "args": ["${workspaceFolder}/server.py"]
    }
  }
}
```

Use absolute paths in user or remote-user configuration so another workspace
does not change the server path. Point `command` directly to this project's
virtual environment; the Python extension's selected interpreter is not used
for MCP server execution.

Native Windows development configuration:

```json
{
  "servers": {
    "onedrive": {
      "type": "stdio",
      "command": "C:\\Code\\onedrive-mcp\\venv\\Scripts\\python.exe",
      "args": ["C:\\Code\\onedrive-mcp\\server.py"]
    }
  }
}
```

The [Windows security limitation](security.md#platform-security-boundary)
still applies.

### Start and use the server

1. Save the configuration.
2. Run **MCP: List Servers**, select `onedrive`, and start it.
3. Review and approve the trust prompt if shown.
4. Open Chat, choose **Agent**, and enable the OneDrive tools through
   **Configure Tools** or the **Tools** tab.
5. Try: `Use the OneDrive get_drive_info tool to show my quota.`

Do not put tokens or `.env` values in `mcp.json`. For startup errors, open
**MCP: List Servers > onedrive > Show Output**. Restart the server after
changing `.env`.

See the official [VS Code MCP server
guide](https://code.visualstudio.com/docs/agent-customization/mcp-servers) and
[configuration
reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
for current interface details.

## Hermes

Add the following entry to Hermes's MCP server configuration. Its JSON format
uses `mcpServers`, unlike VS Code's `servers`.

```json
{
  "mcpServers": {
    "onedrive": {
      "command": "/path/to/onedrive-mcp/venv/bin/python",
      "args": ["/path/to/onedrive-mcp/server.py"]
    }
  }
}
```

Use absolute paths. `server.py` loads the project-local `.env`, so secrets and
environment values do not belong in the Hermes configuration.

## Standalone process

For a simple startup check:

```bash
./venv/bin/python server.py
```

The process waits silently for MCP messages on stdio. Press `Ctrl+C` to stop.
Use `scripts/smoke_test.py` to exercise the real OneDrive integration.

## Open WebUI

Open WebUI consumes OpenAPI/HTTP tool servers rather than this server's raw
stdio transport. Use the authenticated [`mcpo` deployment guide](deployment.md)
instead of pointing Open WebUI or VS Code directly at the wrong transport.
