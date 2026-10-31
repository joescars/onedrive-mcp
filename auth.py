"""MSAL device-code authentication for a personal Microsoft account.

Handles:
  - Interactive device-code login (run once via scripts/setup_auth.py)
  - Loading/saving a plaintext token cache protected by filesystem permissions
  - Silent token acquisition (with refresh) for the running MCP server

No Graph HTTP calls live here — this module is purely about acquiring an
access token. graph_client.py is the only place that talks to Graph, and it
only ever issues GET requests (see the read-only guard there).
"""
from __future__ import annotations

import logging
import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import msal
import requests
from dotenv import load_dotenv
from filelock import FileLock, Timeout

# Code location is used only to support legacy checkout-local .env files.
# Persistent configuration and user data must not live beside installed code:
# MCP clients can install the package into an ephemeral cache (for example,
# npx's package cache) or make its source tree read-only.
PROJECT_ROOT = Path(__file__).resolve().parent


def _expand_user_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    # Never let a relative override depend on the MCP host's arbitrary cwd.
    return (path if path.is_absolute() else Path.home() / path).resolve()


def _user_config_dir() -> Path:
    override = os.environ.get("ONEDRIVE_MCP_CONFIG_DIR")
    if override:
        return _expand_user_path(override)
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return _expand_user_path(str(base / "onedrive-mcp"))


def get_user_data_dir() -> Path:
    """Return the stable per-user data directory, independent of install/cwd."""
    override = os.environ.get("ONEDRIVE_MCP_DATA_DIR")
    if override:
        return _expand_user_path(override)
    if os.name == "nt":
        base = Path(
            os.environ.get("LOCALAPPDATA")
            or os.environ.get("APPDATA")
            or Path.home() / "AppData" / "Local"
        )
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return _expand_user_path(str(base / "onedrive-mcp"))


CONFIG_DIR = _user_config_dir()
_CONFIG_ENV_PATH = CONFIG_DIR / ".env"
_LEGACY_ENV_PATH = PROJECT_ROOT / ".env"
_LEGACY_ENV_IN_USE = not _CONFIG_ENV_PATH.is_file() and _LEGACY_ENV_PATH.is_file()
_ENV_PATH = _CONFIG_ENV_PATH if _CONFIG_ENV_PATH.is_file() else _LEGACY_ENV_PATH

if os.name != "posix":
    logging.getLogger(__name__).warning(
        "Windows/non-POSIX hosts are supported for development only. "
        "Owner-only token/download permissions require a Linux deployment."
    )

# Prefer the stable per-user config file. Keep loading a checkout-local .env
# only as a migration path for existing source installs; new installs should
# create ~/.config/onedrive-mcp/.env (or the platform equivalent).
load_dotenv(_ENV_PATH)

# Restrict the selected config file to its owner, best-effort. The file usually
# contains only a client ID and paths, but may gain sensitive values over time.
if _ENV_PATH.is_file():
    try:
        if stat.S_IMODE(_ENV_PATH.stat().st_mode) != 0o600:
            os.chmod(_ENV_PATH, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        logging.getLogger(__name__).warning("Could not restrict the OneDrive MCP .env file; check its permissions.")

AUTHORITY_BASE = "https://login.microsoftonline.com"

# Delegated scopes needed for read-only OneDrive access. Files.Read.All
# broadens read access to items shared with the user / across the drive,
# but never grants write.
SCOPES = ["Files.Read", "Files.Read.All"]


class AuthConfigError(RuntimeError):
    """Raised when required auth configuration/tokens are missing or invalid."""


def validate_runtime_config() -> None:
    """Fail early for obviously unsafe or malformed config values.

    This keeps the server from wandering into a broken runtime state when a user
    sets placeholder values, path traversal, or malformed tenant/client strings.
    """
    client_id = get_client_id()
    tenant = get_tenant_id()
    if any(ch in client_id for ch in ("/", "\\", "?", "#", "\0")):
        raise AuthConfigError("AZURE_CLIENT_ID contains invalid characters.")
    if not tenant or any(ch in tenant for ch in ("/", "\\", "?", "#", "\0")):
        raise AuthConfigError("AZURE_TENANT_ID is invalid.")

    token_cache = get_token_cache_path()
    download_dir = get_download_dir()
    if token_cache == download_dir:
        raise AuthConfigError("TOKEN_CACHE_PATH and DOWNLOAD_DIR must not resolve to the same location.")


def get_client_id() -> str:
    client_id = os.environ.get("AZURE_CLIENT_ID", "").strip()
    if not client_id or client_id.startswith("00000000"):
        raise AuthConfigError(
            "AZURE_CLIENT_ID is not set (or is still the placeholder value). "
            "Copy .env.example to the OneDrive MCP config directory as .env "
            "and set AZURE_CLIENT_ID to your Azure App Registration's "
            "Application (client) ID."
        )
    if any(ch in client_id for ch in ("/", "\\", "?", "#", "\0")):
        raise AuthConfigError("AZURE_CLIENT_ID contains invalid characters.")
    return client_id


def get_tenant_id() -> str:
    tenant = os.environ.get("AZURE_TENANT_ID", "consumers").strip() or "consumers"
    if any(ch in tenant for ch in ("/", "\\", "?", "#", "\0")):
        raise AuthConfigError("AZURE_TENANT_ID contains invalid characters.")
    return tenant


def get_authority() -> str:
    return f"{AUTHORITY_BASE}/{get_tenant_id()}"


def _resolve_user_data_path(variable: str, default_name: str) -> Path:
    raw = os.environ.get(variable, "").strip()
    if not raw:
        return (get_user_data_dir() / default_name).resolve()
    path = Path(raw).expanduser()
    if not path.is_absolute():
        # Preserve checkout-local relative paths for legacy project .env files.
        # All new configuration resolves relative overrides from the stable
        # per-user data directory, never from the package or process cwd.
        base = PROJECT_ROOT if _LEGACY_ENV_IN_USE else get_user_data_dir()
        path = base / path
    return path.resolve()


def get_token_cache_path() -> Path:
    return _resolve_user_data_path("TOKEN_CACHE_PATH", "token_cache.bin")


def get_download_dir() -> Path:
    return _resolve_user_data_path("DOWNLOAD_DIR", "downloads")


def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    cache_path = get_token_cache_path()
    if cache_path.exists():
        os.chmod(cache_path, stat.S_IRUSR | stat.S_IWUSR)
        cache.deserialize(cache_path.read_text(encoding="utf-8"))
    return cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if not cache.has_state_changed:
        return
    cache_path = get_token_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = cache.serialize()
    fd, temporary = tempfile.mkstemp(prefix=".token-cache-", dir=cache_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(serialized)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, cache_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _cache_transaction() -> Iterator[None]:
    """Serialize the entire read/refresh/write cycle, including across hosts' processes."""
    path = get_token_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix" and path.parent == get_user_data_dir():
            os.chmod(path.parent, stat.S_IRWXU)
        with FileLock(str(path) + ".lock", timeout=10, mode=0o600):
            yield
    except Timeout:
        raise AuthConfigError(
            "Token cache is busy. Wait for the other sign-in or request to finish and retry."
        ) from None
    except (requests.exceptions.RequestException, msal.exceptions.MsalServiceError):
        raise AuthConfigError("Microsoft sign-in connection failed. Please try again later.") from None
    except (OSError, ValueError):
        raise AuthConfigError(
            "Could not read or persist the token cache. Check its permissions and "
            "available disk space; if the cache is corrupt, run `onedrive-mcp-setup` "
            "(or scripts/setup_auth.py from a source checkout) with a new "
            "TOKEN_CACHE_PATH."
        ) from None


def build_app(cache: Optional[msal.SerializableTokenCache] = None) -> msal.PublicClientApplication:
    if cache is None:
        cache = _load_cache()
    return msal.PublicClientApplication(
        client_id=get_client_id(),
        authority=get_authority(),
        token_cache=cache,
    )


def run_device_code_flow() -> dict:
    """Run the human-driven sign-in while holding the shared cache lock."""
    with _cache_transaction():
        return _run_device_code_flow()


def _run_device_code_flow() -> dict:
    """Interactive, one-time login. Prints instructions to stdout.

    Returns the MSAL token result dict on success. Raises AuthConfigError on
    failure. This function is intended to be called from the one-time setup
    command, not from the MCP server itself.
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


def get_access_token(*, force_refresh: bool = False) -> str:
    """Silently acquire (and transparently refresh) an access token.

    Raises AuthConfigError with a human-readable message if there is no
    usable cached account / refresh token, instructing the caller to run the
    one-time setup command again.
    """
    with _cache_transaction():
        return _get_access_token(force_refresh=force_refresh)


def _get_access_token(*, force_refresh: bool) -> str:
    cache = _load_cache()
    app = build_app(cache)
    accounts = app.get_accounts()
    if not accounts:
        raise AuthConfigError(
            "No signed-in account found in the token cache. Run `onedrive-mcp-setup` "
            "(or `python scripts/setup_auth.py` from a source checkout) once "
            "to sign in via device code."
        )

    result = app.acquire_token_silent(SCOPES, account=accounts[0], force_refresh=force_refresh)
    _save_cache(cache)

    if not result or "access_token" not in result:
        raise AuthConfigError(
            "Could not silently refresh the Microsoft Graph access token "
            "(it may have been revoked or expired beyond refresh). Re-run "
            "`onedrive-mcp-setup` (or `python scripts/setup_auth.py` from a "
            "source checkout) to sign in again."
        )

    return result["access_token"]
