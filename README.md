# OneDrive Read-Only MCP Server

[![Tests and dependency audit](https://github.com/joescars/onedrive-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/joescars/onedrive-mcp/actions/workflows/ci.yml)

A local [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
server that lets AI agents search, inspect, and download files from a personal
Microsoft OneDrive account.

> [!IMPORTANT]
> This project is **read-only**. It exposes five read-style tools and only
> performs HTTP GET requests against Microsoft Graph. It cannot upload, edit,
> rename, move, or delete OneDrive content.

## Features

- Search OneDrive by file name or indexed content.
- Browse folders with secure, bounded pagination.
- Inspect file and folder metadata.
- Download files to a controlled local directory without overwriting existing
  files.
- Refresh cached Microsoft credentials without interactive sign-in.
- Connect directly to VS Code or Hermes over stdio, or to Open WebUI through
  an authenticated `mcpo` bridge.

This server targets **personal Microsoft accounts and consumer OneDrive**.
OneDrive for Business and SharePoint are outside the project's default scope.

## Requirements

- Linux and Python 3.10 or newer.
- A personal Microsoft account.
- A free Azure App Registration configured for device-code authentication.

Linux is the supported production platform. Windows is suitable for
development only because POSIX file modes do not establish Windows ACLs.

## Quick start

For complete Azure and authentication instructions, see
[Setup](docs/setup.md).

```bash
git clone https://github.com/joescars/onedrive-mcp.git
cd onedrive-mcp
python3 -m venv venv
./venv/bin/pip install --require-hashes -r requirements.lock
cp .env.example .env
```

Set `AZURE_CLIENT_ID` in `.env`, restrict the file, and sign in:

```bash
chmod 600 .env
./venv/bin/python scripts/setup_auth.py
./venv/bin/python scripts/smoke_test.py
```

The smoke test reads drive information and lists the root folder. It does not
download or modify files.

## Connect a client

Complete the quick start before configuring a client. The MCP host starts this
server when needed; do not leave a separate standalone process running.

| Client | Transport | Guide |
|---|---|---|
| VS Code with GitHub Copilot | stdio | [VS Code setup](docs/clients.md#vs-code-with-github-copilot) |
| Hermes | stdio | [Hermes setup](docs/clients.md#hermes) |
| Open WebUI | OpenAPI/HTTP via `mcpo` | [Bridge deployment](docs/deployment.md) |

Example VS Code workspace configuration:

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

Use absolute paths for user-level or remote-user configuration. No tokens or
`.env` values belong in client configuration.

## Tools

| Tool | Description |
|---|---|
| `search_onedrive(query, top=20, next_link=None)` | Search the drive one page at a time |
| `list_folder(path='/', top=50, next_link=None)` | List a folder's children |
| `get_item_metadata(path_or_id)` | Return metadata for one file or folder |
| `download_file(path_or_id, dest_filename=None)` | Download a file into `DOWNLOAD_DIR` |
| `get_drive_info()` | Return drive, owner, and quota information |

Paths such as `/Documents/report.pdf` and raw Graph item IDs are accepted where
`path_or_id` is documented. Pagination continuations are opaque and must be
passed back to the same tool with the original arguments.

See the [Tool reference](docs/tools.md) for complete inputs, outputs, and
pagination behavior.

Example prompts:

- `Use OneDrive to list /Documents without downloading anything.`
- `Search OneDrive for "invoice" and tell me if more pages are available.`
- `Get metadata for /Documents/report.pdf without downloading it.`
- `Download /Documents/report.pdf as report-copy.pdf.`

Downloads remain on the machine running the server. The tool returns a local
path and metadata, not the file's contents.

## Security and privacy

- Microsoft Graph access is GET-only and guarded in `graph_client.py`.
- The token cache and downloads are stored with owner-only POSIX permissions.
- Signed Graph download URLs are never returned in tool results or errors.
- Downloads have configurable per-file and directory-wide limits.
- Existing files are never overwritten.
- The optional HTTP bridge requires an API key and defaults to loopback.

Read-only access is still sensitive: names, paths, metadata, and quota details
returned by tools enter the MCP client's context. Use only accounts and files
you intend to make available, and never share `token_cache.bin`.

See [Security](docs/security.md) for the trust model, safeguards, and
operational limitations.

## Documentation

| Guide | Covers |
|---|---|
| [Setup](docs/setup.md) | Azure registration, local installation, environment variables, and sign-in |
| [Client configuration](docs/clients.md) | VS Code, Hermes, and stdio operation |
| [Tool reference](docs/tools.md) | Tool inputs, response shapes, pagination, and downloads |
| [Open WebUI deployment](docs/deployment.md) | Authenticated `mcpo` bridge and systemd |
| [Security](docs/security.md) | Read-only enforcement, local storage, pagination, and privacy |
| [Development](docs/development.md) | Tests, dependency locks, smoke tests, and project structure |
| [Troubleshooting](docs/troubleshooting.md) | Common authentication, Graph, client, and dependency errors |

## Development

Run the credential-free test suite:

```bash
./venv/bin/python -m pytest -v
```

Tests mock Microsoft Graph and MSAL traffic; they do not access a real
OneDrive account. See [Development](docs/development.md) for maintenance and
contribution details.
