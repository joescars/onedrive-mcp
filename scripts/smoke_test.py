"""Manual integration/smoke test against the REAL Microsoft Graph API.

*** REQUIRES real credentials ***
You must have already run `python scripts/setup_auth.py` successfully (a
valid token_cache.bin must exist) before this script will work. This is not
part of the automated pytest suite — it makes real network calls to your
actual OneDrive account.

Usage:
    cd onedrive-mcp
    source venv/bin/activate
    python scripts/smoke_test.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auth import AuthConfigError  # noqa: E402
import graph_client  # noqa: E402


def main() -> int:
    print("=== OneDrive MCP smoke test (real Graph API calls) ===\n")

    print("1) get_drive_info() ...")
    try:
        info = graph_client.get_drive_info()
    except AuthConfigError as exc:
        print(f"\nFAILED — auth not set up: {exc}", file=sys.stderr)
        print("Run: python scripts/setup_auth.py", file=sys.stderr)
        return 1
    except graph_client.GraphError as exc:
        print(f"\nFAILED — Graph error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(info, indent=2))

    print("\n2) list_folder('/') ...")
    try:
        listing = graph_client.list_folder("/", top=10)
    except graph_client.GraphError as exc:
        print(f"\nFAILED — Graph error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(listing, indent=2))

    print("\nSmoke test passed — authentication and read access are working.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
