# OneDrive Read-Only MCP Server

[![Tests and dependency audit](https://github.com/joescars/onedrive-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/joescars/onedrive-mcp/actions/workflows/ci.yml)

A **read-only** Model Context Protocol (MCP) server that lets an AI agent
search and download files from a **personal Microsoft OneDrive** account via
the Microsoft Graph API.

**Choose your client:** complete setup and sign-in (steps 1-3), then follow
[VS Code / GitHub Copilot](#vs-code-github-copilot),
[Hermes](#hermes), or [Open WebUI](#6-expose-to-open-webui-via-mcpo).
VS Code and Hermes connect directly over stdio; only Open WebUI needs `mcpo`.

## Contents

- [Read-only design](#read-only-design)
- [Requirements](#requirements)
- [Azure Portal app registration](#1-azure-portal-app-registration)
- [Local setup](#2-local-setup)
- [One-time sign-in](#3-one-time-sign-in-device-code-flow)
- [Connect VS Code, Hermes, or another stdio client](#5-connect-a-stdio-mcp-client)
- [Expose to Open WebUI via `mcpo`](#6-expose-to-open-webui-via-mcpo)
- [Tools exposed](#tools-exposed)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

## Read-only design

This server is designed for read-only access to Microsoft Graph. The current
implementation uses **HTTP GET** for Graph requests:

- Every network call in `graph_client.py` is a `requests.get`. The main
  fetch helper `_get()` hardcodes `requests.get` and asserts on that fact;
  the only other network call is the raw `requests.get` used to stream a
  file's content from its pre-authenticated CDN URL during `download_file`
  (still GET-only — it never uploads or modifies anything).
- No function in the codebase calls `requests.post/put/patch/delete`.
- There is no "write", "delete", "upload", "move", or "rename" tool exposed
  by the MCP server — only `search_onedrive`, `list_folder`,
  `get_item_metadata`, `download_file` (downloads FROM OneDrive to local
  disk — it never uploads or modifies anything in OneDrive), and
  `get_drive_info`.

## Requirements

- Linux server with Python 3.12 (or any Python 3.10+)
- The optional `mcpo` bridge and dependency-maintenance tools require Python 3.11+.
- A **personal Microsoft account** (outlook.com/hotmail.com/live.com or a
  Microsoft account added to consumer OneDrive) — this is NOT for OneDrive
  for Business / SharePoint (those use a different auth audience).
- An Azure App Registration (free, takes ~5 minutes — see below).

**Platform security boundary:** Linux is the supported production platform.
Windows/non-POSIX execution is development-only and emits a warning: `chmod`
does not establish owner-only Windows ACLs. Do not use real tokens or personal
downloads there unless you independently secure the directories with appropriate
ACLs. The token cache is plaintext protected by permissions, not encrypted.

## 1. Azure Portal app registration

1. Go to the [Azure Portal](https://portal.azure.com) → search **"App
   registrations"** → **New registration**.
2. **Name**: anything, e.g. `onedrive-mcp`.
3. **Supported account types**: choose **"Personal Microsoft accounts
   only"** (or "Accounts in any organizational directory and personal
   Microsoft accounts" if you want it to also work with a work/school
   account later — but this project's auth defaults to the `consumers`
   tenant, which only works with personal accounts).
4. **Redirect URI**: leave blank / not needed — the device code flow used
   here has no redirect URI.
5. Click **Register**.
6. On the app's **Overview** page, copy the **Application (client) ID** —
   this goes into `.env` as `AZURE_CLIENT_ID`.
7. Go to **API permissions** → **Add a permission** → **Microsoft Graph** →
   **Delegated permissions** → search and add:
   - `Files.Read`
   - `Files.Read.All`
   Admin consent is **not required** for these delegated scopes on a
   personal Microsoft account app — you'll consent yourself during the
   device-code sign-in.
8. Go to **Authentication** → under **Advanced settings**, set **"Allow
   public client flows"** to **Yes**, then **Save**. This is required for
   the device code flow to work (it's a public/native client, no client
   secret is used or needed).

That's it — no client secret, no redirect URI, no admin consent needed.

## 2. Local setup

```bash
git clone https://github.com/joescars/onedrive-mcp.git
cd onedrive-mcp
python3 -m venv venv
./venv/bin/pip install --require-hashes -r requirements.lock

cp .env.example .env
# edit .env: set AZURE_CLIENT_ID to the Application (client) ID from step 1.6
chmod 600 .env
```

If you already have the repository, skip `git clone` and use its existing
path. Keep the checkout, virtual environment, token cache, and download
directory on a local Linux filesystem rather than a network share or
cloud-synced folder so owner-only permissions and atomic file operations work
as intended.

`.env` fields:

| Variable | Default | Meaning |
|---|---|---|
| `AZURE_CLIENT_ID` | *(required)* | Application (client) ID from Azure |
| `AZURE_TENANT_ID` | `consumers` | Leave as `consumers` for personal MSA |
| `TOKEN_CACHE_PATH` | `./token_cache.bin` | Where MSAL persists tokens (chmod 600) |
| `DOWNLOAD_DIR` | `./downloads` | Local dir for downloaded files |
| `MAX_DOWNLOAD_BYTES` | `104857600` | Positive per-file limit in bytes (100 MiB) |
| `MAX_DOWNLOAD_DIR_BYTES` | `1073741824` | Positive total download-directory limit in bytes (1 GiB) |

Relative cache/download paths are anchored to this project's directory, not the
launching process's working directory. Limits are checked against metadata and
during streaming, including when size metadata is missing or incorrect.
Transfers sharing a download directory are serialized to enforce its budget;
unrelated processes writing there are outside this quota mechanism. Existing
files, including abandoned temporary files, count toward the budget. Files are
never automatically deleted to make room: remove unneeded local downloads
yourself or increase the limits.

## 3. One-time sign-in (device code flow)

```bash
./venv/bin/python scripts/setup_auth.py
```

This prints something like:

```
To sign in, use a web browser to open the page https://microsoft.com/devicelogin
and enter the code ABCD-EFGH to authenticate.
```

Open that URL on **any device** (phone, another computer, doesn't need to
be the headless server), enter the code, sign in with your personal
Microsoft account, and approve the `Files.Read` / `Files.Read.All`
permissions. The script on the server will then complete automatically and
save `token_cache.bin` (permissions restricted to your user only). Refresh
tokens let the server silently renew access tokens after this — you should
not need to repeat this step unless the token is revoked or unused for an
extended period.

Cache updates use owner-only temporary files and atomic replacement. A
cross-process lock covers loading, refresh/sign-in, and persistence so simultaneous
MCP hosts cannot overwrite each other's cache updates. A busy cache returns an
actionable error after 10 seconds; wait for another sign-in/request to finish.

Verify authentication and read access with the live smoke test:

```bash
./venv/bin/python scripts/smoke_test.py
```

This reads drive information and lists the root folder; it does not download
or modify files.

## 4. Run the server standalone (manual test)

```bash
./venv/bin/python server.py
```

It will sit waiting on stdio (this is normal — it's meant to be driven by
an MCP client, not typed into directly). Press Ctrl+C to stop. To actually
exercise it, use `scripts/smoke_test.py` (below) or connect it to VS Code,
Hermes, or Open WebUI. A stdio client starts the process for you; do not
leave a separate standalone instance running for it to connect to.

## 5. Connect a stdio MCP client

### Hermes

Add to Hermes's MCP server config (JSON config, same shape as Claude
Desktop's `mcpServers`):

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

No secrets need to go in this config — `server.py` loads `.env` itself
(via `python-dotenv`) from its own directory, so `AZURE_CLIENT_ID` etc.
never appear in the Hermes config file.

### VS Code (GitHub Copilot)

Use a current VS Code release with GitHub Copilot Chat available and signed in.
Complete steps 1-3 first, including device-code sign-in: the MCP server only
refreshes cached credentials and cannot perform interactive sign-in from chat.
You do **not** need `mcpo`, an HTTP port, or a bridge API key.

**Choose where the server will run.** Linux is recommended. On Windows, a
VS Code WSL or Remote-SSH window connected to Linux lets the server run there.
Install this project, create its virtual environment, and sign in on that
Linux host. In WSL, keep the checkout, token cache, and downloads in the Linux
home filesystem rather than a Windows-mounted/OneDrive folder so POSIX
permissions work. Do not reuse a Windows virtual environment in Linux.

1. Open the Command Palette (`Ctrl+Shift+P` on Windows/Linux).
2. Choose the configuration scope:
   - **MCP: Open User Configuration** makes the server available across
     workspaces in the current profile and runs it on your local machine.
   - In a WSL/Remote-SSH window, use **MCP: Open Remote User Configuration**
     to run the server on that Linux host.
   - For one workspace, create `.vscode/mcp.json` in that workspace instead.
     A remote workspace configuration runs on the remote host.
3. Add the `onedrive` entry under `servers`, preserving any existing servers.
   VS Code uses **`servers`**, not Hermes/Claude Desktop's `mcpServers`.
   For a Linux installation, replace both paths with your actual absolute paths:

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

If the workspace is this repository, a workspace-scoped Linux configuration
can use `${workspaceFolder}/venv/bin/python` and
`${workspaceFolder}/server.py` instead. Use absolute paths in user/remote-user
configuration so opening a different project does not change the server path.
The Python extension's selected interpreter is not a substitute for the
`command` path; point directly to this project's virtual environment.

For **native Windows development only**, the equivalent configuration is below.
The [platform security warning](#requirements) still applies; this example
does not configure Windows ACLs. JSON backslashes must be doubled, and paths
containing spaces do not need extra embedded quotation marks.

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

4. Save the configuration. Run **MCP: List Servers**, select `onedrive`,
   and start it. Review the server configuration and approve the trust prompt
   if shown. VS Code should discover the five tools listed below.
5. Open Chat, choose **Agent**, and use **Configure Tools** (or the **Tools**
   tab, depending on the chat interface) to enable the `onedrive` tools.
6. Try: `Use the OneDrive get_drive_info tool to show my quota.` Review and
   approve the tool call if prompted. Then try the
   [example requests](#example-requests) below.

No tokens or `.env` values belong in `mcp.json`; the server loads its own
project-local `.env`. Avoid committing machine-specific absolute paths in a
shared workspace configuration; use a user configuration for personal setup.
For startup errors, use **MCP: List Servers** > `onedrive` > **Show Output**.
After changing `.env`, restart the server from the same server-management menu.

See the official [VS Code MCP setup guide](https://code.visualstudio.com/docs/agent-customization/mcp-servers)
and [configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)
for current commands and UI details.

## 6. Expose to Open WebUI via `mcpo`

Open WebUI's "Tool Server" feature speaks OpenAPI/HTTP, not raw MCP-stdio.
Bridge with [`mcpo`](https://github.com/open-webui/mcpo):

```bash
# install mcpo into this project's venv (it must be importable by the
# same interpreter that runs the bridge launcher below)
./venv/bin/pip install --require-hashes -r requirements-bridge.lock
```

**Generate the API key into a 0600 env file — do not type it into a
command line.** Anything passed as a shell argument is visible to every
local user via `ps` / `/proc/PID/cmdline` and stays in your shell history:

```bash
umask 077
cat > .mcpo.env <<EOF
MCPO_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
# MCPO_HOST=127.0.0.1
# MCPO_PORT=8765
EOF
chmod 600 .mcpo.env
```

Then start the bridge with the included launcher:

```bash
./venv/bin/python scripts/run_mcp_bridge.py .mcpo.env
```

`run_mcp_bridge.py` calls mcpo's `run()` in-process with the key taken
from the env file, so **the key never appears in any argv** (mcpo's own
CLI only accepts `--api-key` as a command-line argument, which is why this
launcher exists). It also refuses to start if the env file is
group/world-readable or the key is unset.
Relative env-file arguments are resolved against the project directory.

Then in Open WebUI: **Settings → Tools → Add Tool Server** → OpenAPI URL
`http://<server-host>:8765` (use `http://127.0.0.1:8765` if Open WebUI runs
on the same host, or your private network address otherwise), and paste the
same API key (from `.mcpo.env`) into Open WebUI's "API Key" field for this
tool server.

**Security note**: `mcpo` can expose an HTTP endpoint with **no authentication
at all** unless you set an API key; upstream binding defaults vary by version.
The launcher above defaults to `127.0.0.1` and requires a key — treat
both as required, not optional:
- **Always set `MCPO_API_KEY`.** Without one, anyone who can reach the
  port — including any other local account on a shared/multi-user host,
  not just remote network attackers — can search and download your entire
  OneDrive through the HTTP endpoint with zero authentication.
- **Binding to `127.0.0.1` does NOT isolate the endpoint from other local
  users on the same machine.** Loopback-bound ports are reachable by any
  process running as any account on that host. The API key, not the bind
  address, is what actually restricts access.
- **Never put the key in a command line** (`--api-key "..."` in a shell,
  or inline in a systemd `ExecStart`): argv is world-readable. Use the
  env-file pattern above; the launcher passes the key in-process.
- If you need remote access (Open WebUI on a different host), set
  `MCPO_HOST` to a private/internal interface only, or put the bridge
  behind your existing reverse proxy (nginx/Caddy/Traefik) with its own
  authentication, and never expose the port directly to the internet —
  but keep the API key set regardless.

A ready-to-edit systemd unit template for running the bridge
persistently is provided at `deploy/onedrive-mcpo.service` (disabled by
default — it uses a root-owned `0600` `/etc/onedrive-mcpo.env` via
`EnvironmentFile=` plus the same key-free launcher; copy, edit paths/user,
then enable it yourself).

Before enabling it, follow the unit's setup comments: create the private
`/var/lib/onedrive-mcp` state/download directories, point `TOKEN_CACHE_PATH`
and `DOWNLOAD_DIR` there, and sign in as the service account. Only this state
directory is writable; the source and interpreter stay read-only. Systemd reads
the root-owned API-key file and supplies the environment; the unprivileged
launcher must **not** receive that file as a command-line argument.

```bash
sudo cp deploy/onedrive-mcpo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now onedrive-mcpo.service
```

## Tools exposed

| Tool | Description |
|---|---|
| `search_onedrive(query, top=20, next_link=None)` | Full-text search across the drive, one page at a time |
| `list_folder(path='/', top=50, next_link=None)` | List a folder's children, one page at a time |
| `get_item_metadata(path_or_id)` | Full metadata for one file/folder |
| `download_file(path_or_id, dest_filename=None)` | Download a file to `DOWNLOAD_DIR`, returns local path |
| `get_drive_info()` | Quota/owner info — good smoke test |

All tools accept either a human path (e.g. `/Documents/report.pdf`) or a
raw Graph item id where a "path_or_id" parameter is documented.

Search preserves its `{query, count, items}` fields and adds `next_link` and
`has_more`. Folder listing returns `{items, next_link, has_more}`. `top` must
be an integer between 1 and 200. When `has_more` is true, pass `next_link`
back to the **same tool with the same query/path and top**. It is an opaque,
signed continuation, not a URL to fetch yourself, and expires after one hour
or a server restart. Restart paging after expiration. Counts are per-page,
not totals. This replaces the old folder-listing raw-URL `next_link`.

Downloads are published only after transfer and size validation succeed.
Interrupted/oversized transfers remove their temporary files and can be retried
with the same destination name. Publication never overwrites existing files,
even under a filename race. Use a filesystem supporting atomic hard links
(for example ext4); permission, storage and unsupported-filesystem errors are
reported explicitly. A crashed process can leave an owner-only
`.onedrive-part-*` file: remove it only after confirming no transfer is active.
Transport failures produce sanitized errors rather than leaking signed
download URLs into MCP responses or transcripts.

## Example requests

After connecting your client and enabling the tools, try:

- `Use OneDrive to list the files in /Documents without downloading anything.`
- `Search OneDrive for "invoice"; show names and paths, and tell me if more pages are available.`
- `Get the metadata for /Documents/report.pdf without downloading its contents.`
- `Download /Documents/report.pdf as report-copy.pdf and report the saved path.`

**Where downloads go:** `local_path` is on the machine running this server,
not necessarily the machine displaying chat. With WSL, Remote-SSH, or an
Open WebUI bridge on another host, the file remains on that host. The tool
returns a path and metadata, not the file's contents; a client needs separate
filesystem access to read or summarize the downloaded file.

**Privacy:** read-only does not mean private to the server. File names, paths,
quota information, and metadata returned by tools enter your client's chat
context and may be processed or retained according to that client's/model
provider's policies. Review tool approvals and use only accounts and files you
intend to make available. Never paste token-cache contents into chat or logs.

## Testing

### Unit tests (no credentials needed, no network calls)

```bash
./venv/bin/python -m pytest -v
```

These mock Graph/MSAL traffic and cover retry/refresh behavior, signed-URL
error redaction, pagination, atomic downloads, quota limits, cache concurrency,
and bridge-launch configuration. They also perform a real stdio handshake
from an unrelated working directory and test MCP error responses using synthetic
data. POSIX permission assertions run on Linux and are skipped on Windows;
Windows passing is not evidence of secure ACLs.

GitHub Actions runs Linux Python 3.10/3.12/3.14 and Windows development checks,
plus an installed-dependency audit on pushes, pull requests and weekly.

### Updating locked dependencies (Python 3.11+)

The `.txt` files declare inputs; generated `.lock` files pin transitive versions
and artifact hashes across supported Python versions/platforms. Do not manually
edit a lockfile. Install the maintenance tools and regenerate in this order:

```bash
./venv/bin/pip install --require-hashes -r requirements-dev.lock
./venv/bin/python -m uv pip compile requirements.txt --universal --python-version 3.10 --generate-hashes --output-file requirements.lock
./venv/bin/python -m uv pip compile requirements-bridge.txt --universal --python-version 3.11 --generate-hashes --constraint requirements.lock --output-file requirements-bridge.lock
./venv/bin/python -m uv pip compile requirements-dev.txt --universal --python-version 3.11 --generate-hashes --constraint requirements-bridge.lock --output-file requirements-dev.lock
./venv/bin/pip install --require-hashes -r requirements-dev.lock
./venv/bin/python -m pytest -v
./venv/bin/python -m pip_audit --local
```

Add `--upgrade` to the compile commands to refresh already-locked versions.
Review lockfile changes together and retain the `mcp<2.0.0` constraint.

### Manual integration smoke test (REQUIRES real sign-in first)

```bash
./venv/bin/python scripts/setup_auth.py   # one-time, if not already done
./venv/bin/python scripts/smoke_test.py
```

This calls `get_drive_info()` and `list_folder('/')` against your real
OneDrive account and prints the results. It is intentionally excluded from
the pytest suite because it needs live credentials and network access.

## Troubleshooting

- **`No matching distribution found` for a locked version**: the package
  index visible to pip may not expose the same releases as the index used to
  generate the locks. Check availability with
  `python -m pip index versions <package>` using the intended interpreter.
  Use the updated project lockfiles; do not edit only a version number, remove
  hashes, or bypass your organization's package-index/TLS policy. Maintainers
  should regenerate all affected locks and verify a clean install against the
  target index, not just an environment where dependencies are already installed.
- **VS Code cannot find/start `onedrive`**: confirm the file uses a top-level
  `servers` object, the interpreter/script paths exist on the selected
  local or remote host, and dependencies were installed into that exact
  virtual environment. Use **MCP: List Servers** > `onedrive` > **Show Output**.
- **VS Code starts the server but tools are missing**: confirm you trusted
  and enabled the server, selected **Agent**, and enabled its tools in
  **Configure Tools** / **Tools**. Your organization's policies can restrict
  MCP access; do not bypass them.
- **VS Code reports no signed-in account**: run `scripts/setup_auth.py`
  with the configured interpreter on the same host and as the same OS user
  that runs the MCP server, then restart it. Signing into GitHub Copilot
  does not sign this server into your personal Microsoft OneDrive account.
- **Connecting VS Code to the `mcpo` URL fails**: that bridge exposes
  OpenAPI for Open WebUI, not a native MCP HTTP endpoint. Use the stdio
  configuration above, including a remote Linux configuration when needed.
- **`AADSTS700016` / `AADSTS7000218`**: usually means "Allow public client
  flows" is not enabled on the app registration (step 1.8), or the client
  ID is wrong.
- **`AADSTS65001` (consent required/denied)**: you declined the permission
  prompt during device-code sign-in, or the app is missing the
  `Files.Read`/`Files.Read.All` delegated permissions (step 1.7). Re-run
  `scripts/setup_auth.py`.
- **`AADSTS50020` (user account from wrong tenant)**: you tried to sign in
  with a work/school account while `AZURE_TENANT_ID=consumers`. Use a
  personal Microsoft account, or change the app's supported account type
  and `AZURE_TENANT_ID` accordingly (outside this project's default
  scope).
- **"accessNotConfigured" / permission errors on Graph calls (HTTP 403)**:
  double-check `Files.Read` and `Files.Read.All` are added under **API
  permissions** as **Delegated** (not Application) permissions, and that
  you approved them during sign-in.
- **`invalid_grant` / "Could not silently refresh..."**: the refresh token
  expired or was revoked (e.g. password change, long inactivity, or you
  deleted `token_cache.bin`). Re-run `./venv/bin/python scripts/setup_auth.py`.
- **Throttling (HTTP 429)**: up to 4 throttling retries honor `Retry-After`,
  independently of one forced token refresh after a 401. Total throttling sleep
  is bounded to 60 seconds per Graph request. If the requested delay exceeds the
  remaining budget, the server returns a "retry later" error with the requested
  delay instead of retrying too early. Missing headers use exponential backoff.
- **`ModuleNotFoundError: mcp.server.fastmcp`**: you likely installed
  `mcp` 2.x. This project pins `mcp<2.0.0` in `requirements.txt` (FastMCP
  was renamed/relocated in mcp 2.x) — reinstall with
  `./venv/bin/pip install --require-hashes -r requirements.lock`.

## Project structure

```
onedrive-mcp/
  server.py            MCP server + the 5 tool definitions (FastMCP, stdio)
  graph_client.py       Read-only Graph API wrapper (GET-only, enforced)
  auth.py                MSAL device-code flow + token cache load/save/refresh
  scripts/
    setup_auth.py         One-time interactive device-code sign-in
    smoke_test.py          Manual integration test (needs real credentials)
    run_mcp_bridge.py      API-key-protected HTTP bridge launcher
  tests/
    test_graph_client.py   Unit tests (mocked Graph HTTP responses)
    test_auth.py            Unit tests for auth config handling
    test_hardening.py       Retry, paging, transfer limits and integrity
    test_server.py          Real stdio handshake/error-boundary regression tests
    test_bridge.py          Bridge configuration/startup regression tests
  deploy/
    onedrive-mcpo.service   systemd template for the mcpo HTTP bridge
  requirements.txt
  requirements.lock        Hash-locked core and test dependencies
  requirements-bridge.*    Optional mcpo inputs and lockfile
  requirements-dev.*       Maintenance inputs and lockfile
  .github/workflows/ci.yml Tests and scheduled dependency audit
  .env.example
  .gitignore
  README.md
```
