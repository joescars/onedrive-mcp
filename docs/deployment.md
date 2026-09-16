# Open WebUI deployment

[Back to README](../README.md)

This project exposes MCP over stdio. It uses
[`mcpo`](https://github.com/open-webui/mcpo) as an authenticated bridge to
Open WebUI's OpenAPI tool-server integration, not as a native MCP HTTP endpoint.

> [!WARNING]
> Never expose the bridge without an API key. Anyone who can reach an
> unauthenticated endpoint can search and download content from the connected
> OneDrive account.

## Install the bridge

Python 3.11 or newer is required.

```bash
./venv/bin/pip install --require-hashes -r requirements-bridge.lock
```

## Create the bridge environment

Generate the API key directly into an owner-only file. Do not pass it through
`--api-key`; command-line arguments are visible to other local users through
process listings and may remain in shell history.

```bash
umask 077
cat > .mcpo.env <<EOF
MCPO_API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
# MCPO_HOST=127.0.0.1
# MCPO_PORT=8765
EOF
chmod 600 .mcpo.env
```

Start the included launcher:

```bash
./venv/bin/python scripts/run_mcp_bridge.py .mcpo.env
```

The launcher reads the key from the file and invokes `mcpo` in-process, so the
key never appears in process arguments. It refuses to start when the key is
missing or the file is group/world-readable. Relative environment-file paths
resolve from the project directory.

In Open WebUI, add a Tool Server with:

- **OpenAPI URL:** `http://127.0.0.1:8765` when both services run on the same
  host, or the private bridge address otherwise.
- **API key:** the `MCPO_API_KEY` value from `.mcpo.env`.

If Open WebUI runs in Docker, `127.0.0.1` refers to its container, not the
bridge host. Use a private address reachable from that container and bind the
bridge to the corresponding private interface. Keep authentication enabled;
do not solve reachability by exposing an unauthenticated public port.

Downloads stay on the bridge host. Open WebUI receives a local path, not an
attachment or parsed document text. A separate, explicitly configured file
reader or ingestion workflow is needed to summarize downloaded content.

## Network security

- Keep `MCPO_API_KEY` set even when binding to `127.0.0.1`.
- Loopback prevents remote access but does not isolate the endpoint from other
  accounts on the same host; the API key provides that boundary.
- For remote access, bind only to a private/internal interface or use an
  authenticated reverse proxy such as nginx, Caddy, or Traefik.
- Never expose the bridge port directly to the internet.
- Do not place the API key in a shell command or systemd `ExecStart`.

## Run with systemd

The template at `deploy/onedrive-mcpo.service` keeps source and the interpreter
read-only while allowing writes only under `/var/lib/onedrive-mcp`.

1. Edit the unit's `User`, `WorkingDirectory`, and executable paths.
2. Create a root-owned bridge environment:

   ```bash
   sudo install -m 600 -o root -g root /dev/null /etc/onedrive-mcpo.env
   printf 'MCPO_API_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" \
       | sudo tee /etc/onedrive-mcpo.env >/dev/null
   ```

3. Create runtime directories owned by the service account:

   ```bash
   sudo install -d -m 700 -o your-username -g your-username /var/lib/onedrive-mcp
   sudo install -d -m 700 -o your-username -g your-username /var/lib/onedrive-mcp/downloads
   ```

4. In the project's `.env`, set:

   ```dotenv
   TOKEN_CACHE_PATH=/var/lib/onedrive-mcp/token_cache.bin
   DOWNLOAD_DIR=/var/lib/onedrive-mcp/downloads
   ```

5. Run `scripts/setup_auth.py` as the service account.
6. Install and start the unit:

   ```bash
   sudo cp deploy/onedrive-mcpo.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now onedrive-mcpo.service
   ```

Systemd reads the root-owned API-key file and provides the key through the
process environment. The unprivileged launcher does not need the file path in
its command line.
