# Setup

[Back to README](../README.md)

This guide configures the server for a personal Microsoft account and consumer
OneDrive. OneDrive for Business and SharePoint are outside the project's
default scope.

## Requirements

- Linux with Python 3.10 or newer.
- Python 3.11 or newer for the optional `mcpo` bridge and dependency
  maintenance tools.
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
directory is a separate prerequisite.

The server currently requests both `Files.Read` and `Files.Read.All`. These
are read-only scopes, but they are not restricted to one folder.

## 2. Install the server

```bash
git clone https://github.com/joescars/onedrive-mcp.git
cd onedrive-mcp
python3 -m venv venv
./venv/bin/pip install --require-hashes -r requirements.lock
cp .env.example .env
```

If the repository is already present, skip `git clone` and start from its
directory. Keep the checkout, virtual environment, token cache, and downloads
on a local Linux filesystem rather than a network share or cloud-synced folder.

Edit `.env`, replace the placeholder `AZURE_CLIENT_ID`, and restrict access:

```bash
chmod 600 .env
```

### Environment variables

| Variable                 | Default             | Purpose                                     |
| ------------------------ | ------------------- | ------------------------------------------- |
| `AZURE_CLIENT_ID`        | Required            | Azure Application (client) ID               |
| `AZURE_TENANT_ID`        | `consumers`         | Authentication tenant for personal accounts |
| `TOKEN_CACHE_PATH`       | `./token_cache.bin` | Persistent MSAL token cache                 |
| `DOWNLOAD_DIR`           | `./downloads`       | Local download destination                  |
| `MAX_DOWNLOAD_BYTES`     | `104857600`         | Per-file limit in bytes (100 MiB)           |
| `MAX_DOWNLOAD_DIR_BYTES` | `1073741824`        | Total download-directory limit (1 GiB)      |

Relative cache and download paths resolve from the project directory, not the
launching process's working directory.

## 3. Sign in

```bash
./venv/bin/python scripts/setup_auth.py
```

The script displays a URL and device code. Open the URL on any device, enter
the code, sign in with your personal Microsoft account, and approve
`Files.Read` and `Files.Read.All`.

The server stores the resulting cache at `TOKEN_CACHE_PATH` with owner-only
permissions. Refresh tokens normally allow silent renewal; repeat sign-in only
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
