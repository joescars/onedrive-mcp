"""Start the mcpo HTTP bridge for the OneDrive MCP server WITHOUT putting
the API key in process argv.

Why this exists (SECURITY_REVIEW finding N-M3): mcpo only accepts its
--api-key as a command-line argument, and anything in argv is world-readable
via `ps aux` and `/proc/<pid>/cmdline` — and, for a systemd service, also
readable by unprivileged users via `systemctl cat`. Passing the key inline
therefore leaks it to every local account on the host (and into shell
history), which defeats the point of having it.

This launcher instead:
  1. reads MCPO_API_KEY (required) and optional MCPO_HOST / MCPO_PORT from
     an EnvironmentFile=0600-style env file,
  2. calls mcpo.main.run() in-process with the key as a Python kwarg, so it
     never appears in any process command line.

Usage (see deploy/onedrive-mcpo.service):
    ./venv/bin/python scripts/run_mcp_bridge.py /path/to/bridge.env
    # or simply export the variables yourself:
    MCPO_API_KEY=... ./venv/bin/python scripts/run_mcp_bridge.py

Requires: pip install --require-hashes -r requirements-bridge.lock
(into this project's venv).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """Parse a simple KEY=VALUE env file. Existing os.environ wins, so a
    systemd EnvironmentFile= can be overridden by ad-hoc exports for testing."""
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main() -> int:
    if len(sys.argv) > 2:
        print(f"usage: {sys.argv[0]} [PATH_TO_ENV_FILE]", file=sys.stderr)
        return 2

    if len(sys.argv) == 2:
        env_file = Path(sys.argv[1]).expanduser()
        if not env_file.is_absolute():
            env_file = PROJECT_ROOT / env_file
        if not env_file.exists():
            print(f"env file not found: {env_file}", file=sys.stderr)
            return 2
        # Fail loudly if the operator left the env file group/world-readable.
        mode = env_file.stat().st_mode & 0o777
        if mode & 0o077:
            print(
                f"REFUSING to start: {env_file} is mode {oct(mode)} — "
                "the API key would be readable by other local accounts. "
                f"Run: chmod 600 {env_file}",
                file=sys.stderr,
            )
            return 1
        try:
            _load_env_file(env_file)
        except (OSError, ValueError):
            print("Could not read bridge env file. Check its ownership and permissions.", file=sys.stderr)
            return 1

    api_key = os.environ.get("MCPO_API_KEY", "").strip()
    if not api_key:
        print(
            "MCPO_API_KEY is not set. Generate one and store it in a "
            "0600 env file (never in argv or shell history):\n"
            '  python3 -c "import secrets; print(secrets.token_urlsafe(32))"\n'
            "See README.md §6 for the full runbook.",
            file=sys.stderr,
        )
        return 1

    host = os.environ.get("MCPO_HOST", "127.0.0.1")
    try:
        port = int(os.environ.get("MCPO_PORT", "8765"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        print("MCPO_PORT must be an integer between 1 and 65535.", file=sys.stderr)
        return 1

    server_script = PROJECT_ROOT / "server.py"

    try:
        from mcpo.main import run  # provided by the `mcpo` package
    except ImportError:
        print(
            "mcpo is not installed in this interpreter. Install it with:\n"
            "  ./venv/bin/pip install --require-hashes -r requirements-bridge.lock",
            file=sys.stderr,
        )
        return 1

    import asyncio

    print(f"Starting mcpo bridge on {host}:{port} -> {server_script} "
          "(API key loaded from environment, never argv)")
    asyncio.run(run(
        host=host,
        port=port,
        api_key=api_key,
        server_type="stdio",
        server_command=[sys.executable, str(server_script)],
        strict_auth=True,  # key guards ALL endpoints incl. docs (see N-M3)
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
