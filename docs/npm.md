# npm installation

[Back to README](../README.md)

The npm package is a small launcher for the Python distribution. It keeps the
existing Python implementation and read-only guarantees; it is not a separate
TypeScript implementation.

Package release instructions, including the initial npm publish required
before npm Trusted Publishing can be configured, are in
[Development: Distribution release](development.md#distribution-release).

> [!NOTE]
> The `npx` command becomes installable only after matching `0.1.0` releases
> are published to both npm (`@joescars/onedrive-mcp`) and PyPI
> (`joescars-onedrive-mcp`). The launcher pins the Python distribution to the
> same exact version as the npm package.

## Requirements

- Node.js 18 or newer, for `npx`.
- Astral `uv`, which provides the `uvx` command. Install it using the
  [official uv instructions](https://docs.astral.sh/uv/getting-started/installation/).
  `uvx` creates an isolated Python environment and can provision a compatible
  Python interpreter if needed.
- A personal Microsoft account and an app registration that you own or are
  authorized to use; see [Azure setup](setup.md#1-register-an-azure-application).

The project does not ship a shared Microsoft client ID or client secret.
Each user supplies their own app registration's public client ID. Never
create or distribute a client secret for this device-code flow.

## Configure and sign in

Create the private config directory and `.env` file. Replace the placeholder
with the Application (client) ID from your app registration:

```bash
CONFIG_DIR="${ONEDRIVE_MCP_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/onedrive-mcp}"
install -d -m 700 "$CONFIG_DIR"
(umask 077; printf 'AZURE_CLIENT_ID=%s\nAZURE_TENANT_ID=consumers\n' 'YOUR-APPLICATION-CLIENT-ID' > "$CONFIG_DIR/.env")
```

If you set `ONEDRIVE_MCP_CONFIG_DIR`, use an absolute path and make it
available in the environment of both this setup command and the MCP host. The
default uses `$XDG_CONFIG_HOME/onedrive-mcp` or `~/.config/onedrive-mcp`.

Start the separate interactive device-code flow:

```bash
npx --yes --package @joescars/onedrive-mcp@0.1.0 onedrive-mcp-setup
```

The setup command streams the Microsoft device-code prompt in the terminal
and saves the token cache in the per-user data directory. Run it again only if
Microsoft credentials expire or are revoked.

## Configure an MCP client

Use the exact npm version in the client's stdio configuration so upgrades are
intentional. Example:

```json
{
  "mcpServers": {
    "onedrive": {
      "command": "npx",
      "args": ["--yes", "@joescars/onedrive-mcp@0.1.0"]
    }
  }
}
```

The launcher invokes `uvx` without a shell and pins the Python distribution
version. MCP stdin/stdout are passed through unchanged; startup guidance and
errors go to stderr. The launcher itself does not read or store tokens. The
Python server stores config, cache, and downloads in the same stable per-user
locations used by a source install, never in npm's ephemeral package cache.

If your MCP host does not inherit your shell's `PATH`, provide the absolute
path to `npx` in its `command` field. If you customized the config or data
directory, pass the same override in the MCP host's environment. Do not put
client IDs, tokens, or `.env` contents in MCP arguments or logs.

To upgrade, change the explicit npm version after reviewing the release and
repeat device-code sign-in only if the app registration or granted permissions
changed. To uninstall, remove the client entry; remove the local token cache
and downloads separately if you no longer need them. Revoke the app's consent
in the Microsoft account's security settings if access should end immediately.
