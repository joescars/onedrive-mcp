"""Source-checkout wrapper for the installed device-code setup command.

Usage:
    ./venv/bin/python scripts/setup_auth.py
"""
import sys
from pathlib import Path

# Allow running as `python scripts/setup_auth.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from onedrive_mcp_setup import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
