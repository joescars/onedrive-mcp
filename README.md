# OneDrive Read-Only MCP Server

A **read-only** Model Context Protocol (MCP) server that lets an AI agent
search and download files from a **personal Microsoft OneDrive** account via
the Microsoft Graph API.

## Safety guarantee: read-only

This server only ever issues **HTTP GET** requests to Microsoft Graph. That
is enforced in code, not just by convention:

- `graph_client.py` has exactly one function that talks to the network
  (`_get`), it hardcodes `requests.get`, and it asserts on that fact.
- No other function in the codebase calls `requests.post/put/patch/delete`.
- There is no "write", "delete", "upload", "move", or "rename" tool exposed
  by the MCP server — only `search_onedrive`, `list_folder`,
  `get_item_metadata`, `download_file` (downloads FROM OneDrive to local
  disk — it never uploads or modifies anything in OneDrive), and
  `get_drive_info`.

## Requirements

- Linux server with Python 3.12 (or any Python 3.10+)
- A **personal Microsoft account** (outlook.com/hotmail.com/live.com or a
  Microsoft account added to consumer OneDrive) — this is NOT for OneDrive
  for Business / SharePoint (those use a different auth audience).
- An Azure App Registration (free, takes ~5 minutes — see below).

## 1. Azure Portal app registration

1. Go to https://portal.azure.com → search **"App registrations"** → **New
   registration**.
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
cd /path/to/onedrive-mcp
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env
# edit .env: set AZURE_CLIENT_ID to the Application (client) ID from step 1.6
```

`.env` fields:

| Variable | Default | Meaning |
|---|---|---|
| `AZURE_CLIENT_ID` | *(required)* | Application (client) ID from Azure |
| `AZURE_TENANT_ID` | `consumers` | Leave as `consumers` for personal MSA |
| `TOKEN_CACHE_PATH` | `./token_cache.bin` | Where MSAL persists tokens (chmod 600) |
| `DOWNLOAD_DIR` | `./downloads` | Local dir for downloaded files |

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

## 4. Run the server standalone (manual test)

```bash
./venv/bin/python server.py
```

It will sit waiting on stdio (this is normal — it's meant to be driven by
an MCP client, not typed into directly). Press Ctrl+C to stop. To actually
exercise it, use `scripts/smoke_test.py` (below) or wire it into Hermes /
Open WebUI.

## 5. Register with Hermes (stdio MCP client)

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

## 6. Expose to Open WebUI via `mcpo`

Open WebUI's "Tool Server" feature speaks OpenAPI/HTTP, not raw MCP-stdio.
Bridge with [`mcpo`](https://github.com/open-webui/mcpo):

```bash
# install mcpo into this project's venv (it must be importable by the
# same interpreter that runs the bridge launcher below)
./venv/bin/pip install mcpo
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

Then in Open WebUI: **Settings → Tools → Add Tool Server** → OpenAPI URL
`http://<server-host>:8765` (use `http://127.0.0.1:8765` if Open WebUI runs
on the same host, or your private network address otherwise), and paste the
same API key (from `.mcpo.env`) into Open WebUI's "API Key" field for this
tool server.

**Security note**: `mcpo` defaults to binding `0.0.0.0` (all network
interfaces) with **no authentication at all** unless you set an API key.
The launcher above always binds `127.0.0.1` and requires a key — treat
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

```bash
sudo cp deploy/onedrive-mcpo.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now onedrive-mcpo.service
```

## Tools exposed

| Tool | Description |
|---|---|
| `search_onedrive(query, top=20)` | Full-text search across the drive |
| `list_folder(path='/', top=50)` | List a folder's children |
| `get_item_metadata(path_or_id)` | Full metadata for one file/folder |
| `download_file(path_or_id, dest_filename=None)` | Download a file to `DOWNLOAD_DIR`, returns local path |
| `get_drive_info()` | Quota/owner info — good smoke test |

All tools accept either a human path (e.g. `/Documents/report.pdf`) or a
raw Graph item id where a "path_or_id" parameter is documented.

## Testing

### Unit tests (no credentials needed, no network calls)

```bash
./venv/bin/python -m pytest -v
```

These mock all Graph HTTP responses with the `responses` library and test
path resolution + response shaping logic only.

### Manual integration smoke test (REQUIRES real sign-in first)

```bash
./venv/bin/python scripts/setup_auth.py   # one-time, if not already done
./venv/bin/python scripts/smoke_test.py
```

This calls `get_drive_info()` and `list_folder('/')` against your real
OneDrive account and prints the results. It is intentionally excluded from
the pytest suite because it needs live credentials and network access.

## Troubleshooting

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
- **Throttling (HTTP 429)**: `graph_client.py` already retries with
  backoff honoring the `Retry-After` header, up to 4 attempts. If you still
  see failures, you're issuing requests faster than Graph's limits allow —
  reduce concurrency/frequency.
- **`ModuleNotFoundError: mcp.server.fastmcp`**: you likely installed
  `mcp` 2.x. This project pins `mcp<2.0.0` in `requirements.txt` (FastMCP
  was renamed/relocated in mcp 2.x) — reinstall with
  `./venv/bin/pip install -r requirements.txt`.

## Project structure

```
onedrive-mcp/
  server.py            MCP server + the 5 tool definitions (FastMCP, stdio)
  graph_client.py       Read-only Graph API wrapper (GET-only, enforced)
  auth.py                MSAL device-code flow + token cache load/save/refresh
  scripts/
    setup_auth.py         One-time interactive device-code sign-in
    smoke_test.py          Manual integration test (needs real credentials)
  tests/
    test_graph_client.py   Unit tests (mocked Graph HTTP responses)
    test_auth.py            Unit tests for auth config handling
  deploy/
    onedrive-mcpo.service   systemd template for the mcpo HTTP bridge
  requirements.txt
  .env.example
  .gitignore
  README.md
```
