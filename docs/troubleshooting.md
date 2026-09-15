# Troubleshooting

[Back to README](../README.md)

## Installation

### No matching distribution for a locked version

The package index visible to pip may not expose the releases used to generate
the lockfiles. Check the intended interpreter and index:

```bash
python -m pip index versions <package>
```

Use updated project lockfiles. Do not edit a single version, remove hashes, or
bypass organizational package-index or TLS policy. Maintainers should
regenerate all affected locks and test a clean installation.

### `ModuleNotFoundError: mcp.server.fastmcp`

MCP 2.x moved or renamed `FastMCP`. This project intentionally pins
`mcp<2.0.0`. Reinstall the locked dependencies:

```bash
./venv/bin/pip install --require-hashes -r requirements.lock
```

## Authentication

### `AADSTS700016` or `AADSTS7000218`

Confirm that **Allow public client flows** is enabled in the Azure App
Registration and that `AZURE_CLIENT_ID` is the Application (client) ID.

### `AADSTS65001`

Consent was declined or the application lacks the `Files.Read` and
`Files.Read.All` Microsoft Graph delegated permissions. Correct the
registration and rerun:

```bash
./venv/bin/python scripts/setup_auth.py
```

### `AADSTS50020`

A work or school account was used while `AZURE_TENANT_ID=consumers`. Use a
personal Microsoft account. Supporting another tenant requires a compatible
app registration and tenant configuration and is outside the default scope.

### `invalid_grant` or silent refresh failure

The refresh token expired or was revoked, possibly after a password change,
extended inactivity, or cache deletion. Run `scripts/setup_auth.py` again as
the same OS user and on the same host that runs the MCP server.

### Token cache is busy

Another sign-in or token refresh holds the cache lock. Wait for it to finish
and retry. The lock attempt times out after 10 seconds.

## Microsoft Graph

### HTTP 403 or `accessNotConfigured`

Verify that `Files.Read` and `Files.Read.All` were added as **Delegated**
permissions and approved during device-code sign-in.

### HTTP 429 throttling

The client performs up to four throttling retries, independently of one forced
token refresh after HTTP 401. It honors `Retry-After`, uses exponential
backoff when the header is absent, and limits total throttling sleep to 60
seconds per Graph request.

If the requested delay exceeds the remaining budget, the server returns a
retry-later error rather than retrying too early.

## VS Code

### Server cannot be found or started

- Confirm the configuration has a top-level `servers` object.
- Verify the interpreter and script paths on the selected local or remote host.
- Confirm dependencies were installed into that exact virtual environment.
- Open **MCP: List Servers > onedrive > Show Output**.

### Server starts but tools are missing

Trust and enable the server, select **Agent**, and enable its tools in
**Configure Tools** or the **Tools** tab. Organizational policy may restrict
MCP access and should not be bypassed.

### No signed-in OneDrive account

GitHub Copilot sign-in does not authenticate this server with Microsoft. Run
`scripts/setup_auth.py` with the configured interpreter on the server host,
then restart the MCP server.

### Connecting VS Code to the `mcpo` URL fails

The bridge exposes OpenAPI for Open WebUI, not a native MCP HTTP endpoint.
Configure VS Code to start `server.py` over stdio as described in
[Client configuration](clients.md).

## Open WebUI bridge

### Bridge refuses to read the environment file

Confirm the file exists and is owner-only:

```bash
chmod 600 .mcpo.env
```

### `MCPO_API_KEY is not set`

Create `.mcpo.env` using the instructions in
[Open WebUI deployment](deployment.md). Do not pass the key as a command-line
argument.

### Open WebUI cannot reach the bridge

Verify the configured host and port, the Open WebUI Tool Server URL, and any
firewall or reverse-proxy settings. Keep the endpoint on loopback or a private
interface and require the API key.
