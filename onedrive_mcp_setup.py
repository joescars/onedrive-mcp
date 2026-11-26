"""Installed command-line entry point for the interactive device-code login."""
import sys

from auth import TokenSource
from settings import ConfigError, load_settings


def main() -> int:
    print("OneDrive MCP server — one-time device code sign-in")
    print("-" * 60)
    try:
        settings = load_settings()
        result = TokenSource(settings).sign_in_device_code()
    except ConfigError as exc:
        print(f"\nSetup failed: {exc}", file=sys.stderr)
        return 1

    scopes = result.get("scope", "")
    print("\nSign-in complete.")
    print(f"Granted scopes: {scopes}")
    print(f"Token cache written to: {settings.token_cache_path}")
    print(
        "\nYou can now start the MCP server with `onedrive-mcp` (installed "
        "distribution) or `python server.py` (source checkout). The live "
        "smoke test is only included in source checkouts."
    )
    return 0
