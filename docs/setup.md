# Setup

[Back to README](../README.md)

This guide configures the server for a personal Microsoft account and consumer
OneDrive. OneDrive for Business and SharePoint are outside the project's
default scope.

## Requirements

- Linux with Python 3.11 or newer (required by the server, optional `mcpo`
  bridge, and dependency maintenance tools).
- A personal Microsoft account, such as an outlook.com, hotmail.com, or
  live.com account.
- Access to a Microsoft Entra tenant where you can register an application.

Linux is the supported production platform. Windows and other non-POSIX hosts
are development-only: `chmod` does not establish owner-only Windows ACLs, and
the plaintext token cache is protected by permissions rather than encryption.

## 1. Register an Azure application

Check Microsoft's [app-registration prerequisites](https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app#prerequisites)
first. Its guide lists an Azure account with an active subscription, a tenant,
and application-registration permissions. A personal OneDrive account alone
does not guarantee access to App registrations. The directory where you
register the application is separate from the personal account whose files
you authorize later. This server runs locally and does not deploy an Azure
compute resource.

1. Open the [Azure Portal](https://portal.azure.com), search for **App
   registrations**, and select **New registration**.
2. Enter a name such as `onedrive-mcp`.
3. Select **Personal Microsoft accounts only**. You may select the combined
   organizational and personal account option, but this project defaults to
   the `consumers` tenant.
4. Leave **Redirect URI** blank.
5. Select **Register**.
6. Copy the **Application (client) ID** from the Overview page.
7. Under **API permissions**, add these Microsoft Graph **Delegated
   permissions**:
   - `Files.Read`
   - `Files.Read.All`
8. Under **Authentication > Advanced settings**, set **Allow public client
   flows** to **Yes** and save.

No client secret, redirect URI, or administrator consent is needed for a
personal Microsoft account's delegated file access. You grant that consent
during device-code sign-in; permission to create the app registration in its
directory is a separate prerequisite. Each deployment supplies its own
Application (client) ID; this project does not distribute a shared client ID
or any client secret.

The server currently requests both `Files.Read` and `Files.Read.All`. These
are read-only scopes, but they are not restricted to one folder.

## 2. Install the server

```bash
git clone https://github.com/joescars/onedrive-mcp.git
cd onedrive-mcp
python3 --version  # must report Python 3.11 or newer
python3 -m venv venv
./venv/bin/pip install --require-hashes -r requirements.lock
export ONEDRIVE_MCP_CONFIG_DIR="${ONEDRIVE_MCP_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/onedrive-mcp}"
CONFIG_DIR="$ONEDRIVE_MCP_CONFIG_DIR"
install -d -m 700 "$CONFIG_DIR"
cp .env.example "$CONFIG_DIR/.env"
chmod 600 "$CONFIG_DIR/.env"
```

If the repository is already present, skip `git clone` and start from its
directory. Keep the checkout, virtual environment, token cache, and downloads
on a local Linux filesystem rather than a network share or cloud-synced folder.
Store the per-user data directory on a local Linux filesystem as well. If
`python3` is older than 3.11, select a newer interpreter explicitly when
creating the virtual environment.

Edit `"$CONFIG_DIR/.env"` and replace the placeholder `AZURE_CLIENT_ID`.
The default configuration directory is `~/.config/onedrive-mcp` (or
`$XDG_CONFIG_HOME/onedrive-mcp`); set `ONEDRIVE_MCP_CONFIG_DIR` in the
process environment to use another absolute directory. The server also
accepts a checkout-local `.env` for backwards compatibility when no
user-level config file exists.

### Environment variables

| Variable                    | Default                                                   | Purpose                                      |
| --------------------------- | --------------------------------------------------------- | -------------------------------------------- |
| `AZURE_CLIENT_ID`           | Required                                                  | Azure Application (client) ID                |
| `AZURE_TENANT_ID`           | `consumers`                                               | Authentication tenant for personal accounts |
| `ONEDRIVE_MCP_CONFIG_DIR`   | `~/.config/onedrive-mcp`                                  | Per-user config directory                    |
| `ONEDRIVE_MCP_DATA_DIR`     | `~/.local/share/onedrive-mcp`                             | Stable per-user data directory               |
| `TOKEN_CACHE_PATH`          | `$ONEDRIVE_MCP_DATA_DIR/token_cache.bin`                  | Persistent MSAL token cache                 |
| `DOWNLOAD_DIR`              | `$ONEDRIVE_MCP_DATA_DIR/downloads`                        | Local download destination                  |
| `MAX_DOWNLOAD_BYTES`        | `104857600`                                               | Per-file limit in bytes (100 MiB)            |
| `MAX_DOWNLOAD_DIR_BYTES`    | `1073741824`                                               | Total download-directory limit (1 GiB)       |

The default config directory honors `$XDG_CONFIG_HOME`; the data directory
honors `$XDG_DATA_HOME`. Windows development uses `%APPDATA%` for config and
`%LOCALAPPDATA%` for data; macOS uses `~/Library/Application Support`. Set
`ONEDRIVE_MCP_DATA_DIR` in the process environment or `.env` to override the
data directory. Both config/data directory overrides must be absolute paths.
`ONEDRIVE_MCP_CONFIG_DIR` must be set in the process
environment before launch because it determines where `.env` is loaded from.
Relative `TOKEN_CACHE_PATH` and `DOWNLOAD_DIR` overrides resolve under the
stable data directory, never under the process working directory or installed
package.

Existing checkout installs can continue using their project-local `.env` as
a compatibility fallback. Before switching to a packaged installation,
move that file to the per-user config directory and move the token cache and
downloads to the per-user data directory (or set explicit absolute paths).
Check any custom paths in `.env` before moving data. The old template's
`./token_cache.bin` and `./downloads` values were relative to the checkout;
when copied to the new config location, relative values resolve under the new
data directory. Move those existing files/directories there after stopping
the server, or update their paths explicitly. When the user-level `.env`
exists, it takes precedence over the legacy checkout file.

## 3. Sign in

```bash
./venv/bin/python scripts/setup_auth.py
```

The script displays a URL and device code. Open the URL on any device, enter
the code, sign in with your personal Microsoft account, and approve
`Files.Read` and `Files.Read.All`.

The server stores the resulting cache in the stable user data directory (or
at `TOKEN_CACHE_PATH`) with owner-only permissions. Refresh tokens normally
allow silent renewal; repeat sign-in only
after credentials expire or are revoked.

Cache writes use owner-only temporary files and atomic replacement. A
cross-process lock protects simultaneous sign-in and refresh operations. If
the cache remains busy for 10 seconds, wait for the other operation to finish
and retry.

## 4. Verify access

```bash
./venv/bin/python scripts/smoke_test.py
```

This live test calls `get_drive_info()` and `list_folder('/')`. It reads
metadata from the real account but does not download or modify files.

Next, configure [VS Code or Hermes](clients.md), or deploy the authenticated
[Open WebUI bridge](deployment.md).
