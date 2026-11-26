"""MSAL device-code authentication for a personal Microsoft account.

Handles:
  - Interactive device-code login (run once via onedrive-mcp-setup)
  - Loading/saving a plaintext token cache protected by filesystem permissions
  - Silent token acquisition (with refresh) for the running MCP server

All configuration comes from an already-validated ``Settings``; this module
never reads the environment. No Graph HTTP calls live here — graph_client.py
is the only place that talks to Graph, and it only ever issues GET requests.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Optional

import msal
import requests
from filelock import FileLock, Timeout

from settings import AuthConfigError, ConfigError, Settings

__all__ = ["AuthConfigError", "ConfigError", "SCOPES", "TokenSource"]

# Delegated scopes needed for read-only OneDrive access. Files.Read.All
# broadens read access to items shared with the user / across the drive,
# but never grants write.
SCOPES = ["Files.Read", "Files.Read.All"]

_NOT_CONFIGURED = (
    "AZURE_CLIENT_ID is not set (or is still the placeholder value). "
    "Copy .env.example to the OneDrive MCP config directory as .env "
    "and set AZURE_CLIENT_ID to your Azure App Registration's "
    "Application (client) ID."
)


class TokenSource:
    """Supplies access tokens for the signed-in account."""

    def __init__(
        self,
        settings: Settings,
        *,
        lock_timeout: float = 10,
        app_factory: Optional[Callable[[msal.SerializableTokenCache], "msal.PublicClientApplication"]] = None,
    ) -> None:
        self._settings = settings
        self._lock_timeout = lock_timeout
        self._app_factory = app_factory or self._build_msal_app

    # -- public API ---------------------------------------------------------

    def access_token(self, *, force_refresh: bool = False) -> str:
        """Silently acquire (and transparently refresh) an access token.

        Raises ConfigError with a human-readable message if unconfigured or
        if there is no usable cached account / refresh token.
        """
        self._require_client_id()
        with self._cache_transaction():
            return self._access_token(force_refresh=force_refresh)

    def sign_in_device_code(self) -> dict:
        """Run the human-driven sign-in while holding the shared cache lock."""
        self._require_client_id()
        with self._cache_transaction():
            return self._sign_in_device_code()

    # -- internals ----------------------------------------------------------

    def _require_client_id(self) -> None:
        if self._settings.client_id is None:
            raise ConfigError(_NOT_CONFIGURED)

    def _build_msal_app(self, cache: msal.SerializableTokenCache) -> msal.PublicClientApplication:
        return msal.PublicClientApplication(
            client_id=self._settings.client_id,
            authority=self._settings.authority,
            token_cache=cache,
        )

    def _load_cache(self) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        cache_path = self._settings.token_cache_path
        if cache_path.exists():
            os.chmod(cache_path, stat.S_IRUSR | stat.S_IWUSR)
            cache.deserialize(cache_path.read_text(encoding="utf-8"))
        return cache

    def _save_cache(self, cache: msal.SerializableTokenCache) -> None:
        if not cache.has_state_changed:
            return
        cache_path = self._settings.token_cache_path
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
    def _cache_transaction(self) -> Iterator[None]:
        """Serialize the entire read/refresh/write cycle, including across hosts' processes."""
        path = self._settings.token_cache_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == "posix" and path.parent == self._settings.data_dir:
                os.chmod(path.parent, stat.S_IRWXU)
            with FileLock(str(path) + ".lock", timeout=self._lock_timeout, mode=0o600):
                yield
        except Timeout:
            raise ConfigError(
                "Token cache is busy. Wait for the other sign-in or request to finish and retry."
            ) from None
        except (requests.exceptions.RequestException, msal.exceptions.MsalServiceError):
            raise ConfigError("Microsoft sign-in connection failed. Please try again later.") from None
        except (OSError, ValueError):
            raise ConfigError(
                "Could not read or persist the token cache. Check its permissions and "
                "available disk space; if the cache is corrupt, run `onedrive-mcp-setup` "
                "(or scripts/setup_auth.py from a source checkout) with a new "
                "TOKEN_CACHE_PATH."
            ) from None

    def _sign_in_device_code(self) -> dict:
        """Interactive, one-time login. Prints instructions to stdout."""
        cache = self._load_cache()
        app = self._app_factory(cache)

        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise ConfigError(
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
            raise ConfigError(
                "Device code login failed: "
                f"{result.get('error')}: {result.get('error_description')}"
            )

        self._save_cache(cache)
        return result

    def _access_token(self, *, force_refresh: bool) -> str:
        cache = self._load_cache()
        app = self._app_factory(cache)
        accounts = app.get_accounts()
        if not accounts:
            raise ConfigError(
                "No signed-in account found in the token cache. Run `onedrive-mcp-setup` "
                "(or `python scripts/setup_auth.py` from a source checkout) once "
                "to sign in via device code."
            )

        result = app.acquire_token_silent(SCOPES, account=accounts[0], force_refresh=force_refresh)
        self._save_cache(cache)

        if not result or "access_token" not in result:
            raise ConfigError(
                "Could not silently refresh the Microsoft Graph access token "
                "(it may have been revoked or expired beyond refresh). Re-run "
                "`onedrive-mcp-setup` (or `python scripts/setup_auth.py` from a "
                "source checkout) to sign in again."
            )

        return result["access_token"]
