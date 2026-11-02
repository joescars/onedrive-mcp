"""Installed command-line entry point for the interactive device-code login."""
import sys

from auth import AuthConfigError, get_token_cache_path, run_device_code_flow, validate_runtime_config


def main() -> int:
    print("OneDrive MCP server — one-time device code sign-in")
    print("-" * 60)
    try:
        validate_runtime_config()
        result = run_device_code_flow()
    except AuthConfigError as exc:
        print(f"\nSetup failed: {exc}", file=sys.stderr)
        return 1

    scopes = result.get("scope", "")
    print("\nSign-in complete.")
    print(f"Granted scopes: {scopes}")
    print(f"Token cache written to: {get_token_cache_path()}")
    print(
        "\nYou can now start the MCP server with `onedrive-mcp` (installed "
        "distribution) or `python server.py` (source checkout). The live "
        "smoke test is only included in source checkouts."
    )
    return 0
