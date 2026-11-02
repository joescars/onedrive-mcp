# OneDrive Read-Only MCP Server

[![Tests and dependency audit](https://github.com/joescars/onedrive-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/joescars/onedrive-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/joescars-onedrive-mcp)](https://pypi.org/project/joescars-onedrive-mcp/)
[![npm](https://img.shields.io/npm/v/%40joescars%2Fonedrive-mcp)](https://www.npmjs.com/package/@joescars/onedrive-mcp)

A local [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server
for searching, inspecting, and downloading files from a personal Microsoft
OneDrive account. Install the published package with `npx`; no repository
checkout or separate Python installation is needed. The launcher uses Astral
`uv` to install and run the matching Python package in an isolated environment.

> [!IMPORTANT]
> **This server is read-only.** It exposes five read-style tools and only
> makes HTTP GET requests to Microsoft Graph. It can download files from
> OneDrive to the local machine, but cannot upload, edit, rename, move, or
> delete OneDrive content.

This project targets **personal Microsoft accounts and consumer OneDrive**.
OneDrive for Business and SharePoint are outside its default scope. Linux is
the supported production platform; Windows and other non-POSIX systems are for
development only because POSIX file modes do not establish Windows ACLs.

## Install and connect

### Requirements

- Node.js 18 or newer, which provides `npx`.
- [Astral `uv`](https://docs.astral.sh/uv/getting-started/installation/),
  which provides `uvx`. It creates an isolated Python environment and can
  provision a compatible Python interpreter; you do not need to install the
  server's Python dependencies yourself.
- A personal Microsoft account and a Microsoft Entra app registration that
  you own or are authorized to use.

### 1. Register your Microsoft application

Follow [the app registration guide](docs/setup.md#1-register-an-azure-application).
You will use its public **Application (client) ID** below. Each user supplies
their own ID; this project does not ship a shared client ID or client secret.

### 2. Save your client ID and sign in

The following commands are for Linux/macOS shells. They create a private
per-user configuration directory and `.env` file; replace the placeholder
with your app registration's client ID:

```bash
CONFIG_DIR="${ONEDRIVE_MCP_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/onedrive-mcp}"
install -d -m 700 "$CONFIG_DIR"
if [ ! -e "$CONFIG_DIR/.env" ]; then
  (umask 077; printf 'AZURE_CLIENT_ID=%s\nAZURE_TENANT_ID=consumers\n' 'YOUR-APPLICATION-CLIENT-ID' > "$CONFIG_DIR/.env")
else
  echo "Keep the existing $CONFIG_DIR/.env; verify AZURE_CLIENT_ID is set there."
fi
npx --yes --package @joescars/onedrive-mcp@latest onedrive-mcp-setup
```

The setup command follows npm's `latest` tag. The MCP client example below
pins the running server to `0.1.2` so upgrades are intentional.

The setup command displays Microsoft's device-code sign-in instructions and
stores the token cache in the stable per-user data directory. You normally
sign in once; repeat only if your credentials expire or are revoked. See
[Setup](docs/setup.md) for app registration, path overrides, and migration
from a source install.

### 3. Add the server to your MCP client

Use the following stdio configuration in clients that support an
`mcpServers` object:

```json
{
  "mcpServers": {
    "onedrive": {
      "command": "npx",
      "args": ["--yes", "@joescars/onedrive-mcp@0.1.2"]
    }
  }
}
```

Pinning the package version makes upgrades intentional. The MCP client starts
the server when needed; do not run a separate server process. If the client
cannot find `npx` on its `PATH`, use the absolute path to `npx`. Keep client
IDs, `.env` contents, and tokens out of client configuration. For client-
specific examples, see [Client configuration](docs/clients.md); the npm
launcher has its own [installation guide](docs/npm.md).

## What it can do

The server provides these five MCP tools:

| Tool | Purpose |
| --- | --- |
| `search_onedrive(query, top=20, next_link=None)` | Search by file name or indexed content, one page at a time. |
| `list_folder(path='/', top=50, next_link=None)` | List a folder's children with bounded pagination. |
| `get_item_metadata(path_or_id)` | Inspect metadata for a file or folder. |
| `download_file(path_or_id, dest_filename=None)` | Download a file into the local download directory without overwriting an existing file. |
| `get_drive_info()` | Read drive, owner, and quota information. |

Paths such as `/Documents/report.pdf` and Graph item IDs are accepted where
documented. Pagination continuations are opaque; pass them back to the same
tool with the original arguments. The server returns a local path and metadata
for downloads, not the downloaded file's contents. See the [Tool reference](docs/tools.md)
for exact arguments and results.

## Storage and security

- Microsoft Graph requests use read-only delegated scopes and a GET-only
  network helper. This is not a guarantee against future code changes.
- `.env` configuration, the token cache, and downloads live outside the npm
  cache and source checkout in stable per-user directories.
- The token cache contains sensitive credentials. On Linux, files are
  restricted with owner-only POSIX permissions; the cache is not encrypted.
- Signed download URLs are not returned in tool results or errors. Downloads
  have configurable per-file and total-directory limits, and existing files
  are never overwritten.
- Search results, file names, paths, and metadata become available to the MCP
  client and may enter the model's context. Only connect accounts and files
  you intend to make available.

Read the full [Security guide](docs/security.md), and never share
`token_cache.bin` or the contents of your `.env` file.

## Configuration preflight and troubleshooting

Before starting the MCP server, the launcher validates local config values for
common mistakes such as malformed Azure IDs, unsupported tenant names, path
conflicts, bad relative overrides, and non-positive byte limits. The server
still permits a credential-free startup so MCP clients can discover its tools,
but it refuses to proceed with a broken runtime configuration before any Graph
request or token-cache write occurs.

If startup fails, the error is sent to stderr and the process exits without
leaking config values or tokens to MCP stdout. For more detail, see
[Setup](docs/setup.md) and [Troubleshooting](docs/troubleshooting.md).

## Other installation and deployment options

- **Python/source installation:** follow [Setup](docs/setup.md). Useful for
  development or when you prefer managing the checkout yourself.
- **Open WebUI:** this server uses stdio; expose it through the authenticated
  [`mcpo` bridge](docs/deployment.md) rather than pointing Open WebUI directly
  at the stdio process.

## Documentation

| Guide | What it covers |
| --- | --- |
| [Setup](docs/setup.md) | Microsoft app registration, source installation, configuration, and sign-in. |
| [Client configuration](docs/clients.md) | VS Code, Hermes, pi, stdio, and Open WebUI setup. |
| [npm installation](docs/npm.md) | Published launcher requirements, sign-in, and client configuration. |
| [Tool reference](docs/tools.md) | Tool inputs, response shapes, pagination, and downloads. |
| [Security](docs/security.md) | Read-only design, local storage, and privacy considerations. |
| [Development](docs/development.md) | Tests, dependency maintenance, and publishing releases. |
| [Troubleshooting](docs/troubleshooting.md) | Authentication, Graph, client, and dependency issues. |

## License

MIT licensed; see [LICENSE](LICENSE). This is an independent community
project and is not affiliated with or endorsed by Microsoft.
