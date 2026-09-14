"""MSAL device-code authentication for a personal Microsoft account.

Handles:
  - Interactive device-code login (run once via scripts/setup_auth.py)
  - Loading/saving an encrypted-at-rest-by-permissions token cache
  - Silent token acquisition (with refresh) for the running MCP server

No Graph HTTP calls live here — this module is purely about acquiring an
access token. graph_client.py is the only place that talks to Graph, and it
only ever issues GET requests (see the read-only guard there).
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from typing import Optional

import msal
from dotenv import load_dotenv

# This project's root directory (where .env, token_cache.bin, downloads/
# live). Resolved from this file's own location, NOT from the process's
# current working directory — MCP clients (e.g. Hermes) commonly launch this
# server with an unrelated cwd (their own install dir), so relying on cwd
# silently breaks .env discovery and any relative default paths.
PROJECT_ROOT = Path(__file__).resolve().parent

# Load .env once at import time so both the server and the CLI scripts share
# the same configuration source, regardless of the launching process's cwd.
load_dotenv(PROJECT_ROOT / ".env")

# Harden .env permissions to owner-only on import, best-effort. .env holds
# no bearer secret today (just AZURE_CLIENT_ID/AZURE_TENANT_ID/paths), but
# it's exactly the kind of file that tends to accumulate sensitive values
# over time, and token_cache.bin already gets the same treatment.
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    try:
        os.chmod(_env_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

AUTHORITY_BASE = "https://login.microsoftonline.com"

# Delegated scopes needed for read-only OneDrive access. Files.Read.All
# broadens read access to items shared with the user / across the drive,
# but never grants write.
SCOPES = ["Files.Read", "Files.Read.All"]


class AuthConfigError(RuntimeError):
    """Raised when required auth configuration/tokens are missing or invalid."""


def get_client_id() -> str:
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    if not client_id or client_id.startswith("00000000"):
        raise AuthConfigError(
            "AZURE_CLIENT_ID is not set (or is still the placeholder value). "
            "Copy .env.example to .env and set AZURE_CLIENT_ID to your Azure "
            "App Registration's Application (client) ID."
        )
    return client_id


def get_tenant_id() -> str:
    return os.environ.get("AZURE_TENANT_ID", "consumers").strip() or "consumers"


def get_authority() -> str:
    return f"{AUTHORITY_BASE}/{get_tenant_id()}"


def get_token_cache_path() -> Path:
    raw = os.environ.get("TOKEN_CACHE_PATH", "./token_cache.bin")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        # Resolve relative paths against the project root, not the launching
        # process's cwd (see PROJECT_ROOT comment above).
        path = PROJECT_ROOT / path
    return path.resolve()


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    cache_path = get_token_cache_path()
    if cache_path.exists():
        cache.deserialize(cache_path.read_text())
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    cache_path = get_token_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    # Create with restrictive mode from the start (N-L1): with write_text the
    # file is briefly created at 0664 (umask) before the chmod below, leaving
    # a window where other local accounts (group/world) could read the
    # serialized refresh token. os.open with 0600 closes that window.
    fd = os.open(cache_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        f.write(cache.serialize())
    # Best-effort: guarantee owner-only even if an existing file had looser mode.
    try:
        os.chmod(cache_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def build_app(cache: Optional[msal.SerializableTokenCache] = None) -> msal.PublicClientApplication:
    if cache is None:
        cache = _load_cache()
    return msal.PublicClientApplication(
        client_id=get_client_id(),
        authority=get_authority(),
        token_cache=cache,
    )


def run_device_code_flow() -> dict:
    """Interactive, one-time login. Prints instructions to stdout.

    Returns the MSAL token result dict on success. Raises AuthConfigError on
    failure. This function is intended to be called from
    scripts/setup_auth.py, not from the MCP server itself.
    """
    cache = _load_cache()
    app = build_app(cache)

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise AuthConfigError(
            f"Failed to start device code flow: {flow.get('error_description', flow)}"
        )

    print("\n" + "=" * 70)
    print("MICROSOFT ACCOUNT SIGN-IN REQUIRED")
    print("=" * 70)
    print(flow["message"])
    print("=" * 70)
    print(
        "Open the URL above on ANY device with a browser (phone, laptop, "
        "etc.), enter the code, and sign in with your PERSONAL Microsoft "
        "account. This terminal will wait and continue automatically."
    )
    print("=" * 70 + "\n")
    sys.stdout.flush()

    result = app.acquire_token_by_device_flow(flow)  # blocks until login or timeout

    if "access_token" not in result:
        raise AuthConfigError(
            "Device code login failed: "
            f"{result.get('error')}: {result.get('error_description')}"
        )

    _save_cache(cache)
    print(f"Login successful. Token cache saved to: {get_token_cache_path()}")
    return result


def get_access_token() -> str:
    """Silently acquire (and transparently refresh) an access token.

    Raises AuthConfigError with a human-readable message if there is no
    usable cached account / refresh token, instructing the caller to run
    scripts/setup_auth.py again.
    """
    cache = _load_cache()
    app = build_app(cache)

    accounts = app.get_accounts()
    if not accounts:
        raise AuthConfigError(
            "No signed-in account found in the token cache. Run "
            "'python scripts/setup_auth.py' once to sign in via device code."
        )

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)

    if not result or "access_token" not in result:
        raise AuthConfigError(
            "Could not silently refresh the Microsoft Graph access token "
            "(it may have been revoked or expired beyond refresh). Re-run "
            "'python scripts/setup_auth.py' to sign in again."
        )

    return result["access_token"]
