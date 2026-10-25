"""One-time interactive setup: signs in via OAuth2 device code flow and
persists the MSAL token cache so the MCP server can run headlessly and
silently refresh tokens afterwards.

Usage:
    # Copy .env.example to ~/.config/onedrive-mcp/.env and set AZURE_CLIENT_ID
    ./venv/bin/python scripts/setup_auth.py

Requires AZURE_CLIENT_ID in the per-user config file or process environment
(see docs/setup.md for the full setup instructions).
"""
import sys
from pathlib import Path

# Allow running as `python scripts/setup_auth.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth import AuthConfigError, get_token_cache_path, run_device_code_flow  # noqa: E402


def main() -> int:
    print("OneDrive MCP server — one-time device code sign-in")
    print("-" * 60)
    try:
        result = run_device_code_flow()
    except AuthConfigError as exc:
        print(f"\nSetup failed: {exc}", file=sys.stderr)
        return 1

    scopes = result.get("scope", "")
    print("\nSign-in complete.")
    print(f"Granted scopes: {scopes}")
    print(f"Token cache written to: {get_token_cache_path()}")
    print(
        "\nYou can now run the MCP server (python server.py) or the smoke "
        "test (python scripts/smoke_test.py)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
