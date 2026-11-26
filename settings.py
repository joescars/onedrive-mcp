"""Runtime configuration for the OneDrive MCP server.

``load_settings()`` is the ONE place that reads environment variables and the
per-user ``.env`` file. It is called once per process by an entry point and
returns an immutable :class:`Settings`. Nothing here runs at import time.

Persistent state (``.env``, token cache, downloads) lives in stable per-user
directories, never next to installed code and never relative to the cwd. The
checkout-local ``.env`` is only a backwards-compatibility fallback.

Error messages name the offending variable but never include path values.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import sys
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Optional

from dotenv import dotenv_values

# Code location is used only to support legacy checkout-local .env files.
CODE_DIR = Path(__file__).resolve().parent

AUTHORITY_BASE = "https://login.microsoftonline.com"
DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_DOWNLOAD_DIR_BYTES = 1024 * 1024 * 1024

_TENANT_RE = re.compile(r"^[A-Za-z0-9.-]+$")
_BAD_CHARS = ("/", "\\", "?", "#", "\0")
_WELL_KNOWN_TENANTS = {"consumers", "common", "organizations"}


class ConfigError(RuntimeError):
    """Raised when configuration (or required credentials) is missing or invalid."""


# Backwards-compatible name.
AuthConfigError = ConfigError


@dataclass(frozen=True)
class Settings:
    """Complete, validated runtime configuration for one process."""

    client_id: Optional[str]  # None => unconfigured: tools discoverable, nothing reaches OneDrive
    tenant_id: str
    config_dir: Path
    data_dir: Path
    env_path: Optional[Path]  # the .env that was loaded, if any
    legacy_env_in_use: bool  # True when env_path is the checkout-local fallback
    token_cache_path: Path
    download_dir: Path
    max_download_bytes: int
    max_download_dir_bytes: int

    @property
    def authority(self) -> str:
        return f"{AUTHORITY_BASE}/{self.tenant_id}"


@contextmanager
def _path_errors(variable: str) -> Iterator[None]:
    """Normalize filesystem failures without exposing configured path values."""
    try:
        yield
    except ConfigError:
        raise
    except (OSError, RuntimeError, ValueError):
        # Symlink loops raise RuntimeError on older Python and OSError on newer
        # versions. Never let the underlying path-bearing exception escape.
        raise ConfigError(
            f"{variable} could not be resolved or inspected. Check path types, "
            "permissions, and symlink loops."
        ) from None


def _path_stat(path: Path):
    try:
        return path.stat()
    except FileNotFoundError:
        return None


def _absolute_resolved(path: Path) -> Path:
    # Detect loops even when resolve(strict=False) tolerates them.
    _path_stat(path)
    return path.resolve()


def _override_dir(environ: Mapping[str, str], variable: str) -> Optional[Path]:
    raw = environ.get(variable, "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ConfigError(f"{variable} must be an absolute path, not a relative path.")
    return path


def _default_config_base(environ: Mapping[str, str]) -> Path:
    if os.name == "nt":
        return Path(environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _default_data_base(environ: Mapping[str, str]) -> Path:
    if os.name == "nt":
        return Path(
            environ.get("LOCALAPPDATA")
            or environ.get("APPDATA")
            or Path.home() / "AppData" / "Local"
        )
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _checked_dir(environ, variable: str, default_base) -> Path:
    with _path_errors(variable):
        path = _override_dir(environ, variable) or (default_base(environ) / "onedrive-mcp")
        resolved = _absolute_resolved(path)
        metadata = _path_stat(resolved)
        if metadata is not None and not stat.S_ISDIR(metadata.st_mode):
            raise ConfigError(f"{variable} must point to a directory.")
        return resolved


def _locate_env_file(config_dir: Path, project_root: Path) -> tuple[Optional[Path], bool]:
    """Return (.env path or None, whether it is the legacy checkout one)."""
    with _path_errors("ONEDRIVE_MCP_CONFIG_DIR / .env"):
        config_env = config_dir / ".env"
        metadata = _path_stat(config_env)
        if metadata is not None:
            if not stat.S_ISREG(metadata.st_mode):
                raise ConfigError("The OneDrive MCP .env must be a regular file.")
            return config_env, False
        legacy = project_root / ".env"
        metadata = _path_stat(legacy)
        if metadata is None:
            return None, False
        if not stat.S_ISREG(metadata.st_mode):
            raise ConfigError("The OneDrive MCP .env must be a regular file.")
        return legacy, True


def _read_env_file(env_path: Path) -> dict[str, str]:
    with _path_errors("ONEDRIVE_MCP_CONFIG_DIR / .env"):
        values = {k: v for k, v in dotenv_values(env_path).items() if v is not None}
        try:
            if stat.S_IMODE(env_path.stat().st_mode) != 0o600:
                os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            logging.getLogger(__name__).warning(
                "Could not restrict the OneDrive MCP .env file; check its permissions."
            )
        return values


def _parse_client_id(raw: str) -> Optional[str]:
    client_id = raw.strip()
    if not client_id:
        return None
    if any(ch in client_id for ch in _BAD_CHARS):
        raise ConfigError("AZURE_CLIENT_ID contains invalid characters.")
    try:
        uuid.UUID(client_id)
    except ValueError:
        raise ConfigError(
            "AZURE_CLIENT_ID must be a valid Azure application (client) ID in GUID format."
        ) from None
    if client_id.startswith("00000000"):
        raise ConfigError(
            "AZURE_CLIENT_ID still uses the placeholder value. Replace it with "
            "your app registration's real Application (client) ID."
        )
    return client_id


def _parse_tenant(raw: str) -> str:
    tenant = raw.strip() or "consumers"
    if any(ch in tenant for ch in _BAD_CHARS):
        raise ConfigError("AZURE_TENANT_ID contains invalid characters.")
    if tenant not in _WELL_KNOWN_TENANTS and not _TENANT_RE.fullmatch(tenant):
        raise ConfigError(
            "AZURE_TENANT_ID must be 'consumers', 'common', 'organizations', "
            "a tenant GUID, or a tenant domain name."
        )
    return tenant


def _data_path(
    values: Mapping[str, str], variable: str, default: Path, base: Path, *, is_directory: bool
) -> Path:
    with _path_errors(variable):
        raw = values.get(variable, "").strip()
        if not raw:
            path = default
        else:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                # Never depend on the host's arbitrary cwd.
                path = base / path
        resolved = _absolute_resolved(path)
        metadata = _path_stat(resolved)
        if metadata is not None:
            expected = stat.S_ISDIR if is_directory else stat.S_ISREG
            if not expected(metadata.st_mode):
                kind = "directory" if is_directory else "regular file"
                raise ConfigError(f"{variable} must point to a {kind}.")
        return resolved


def _parse_limit(values: Mapping[str, str], variable: str, default: int) -> int:
    raw = values.get(variable, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{variable} must be a positive integer number of bytes.") from None
    if value <= 0:
        raise ConfigError(f"{variable} must be a positive integer number of bytes.")
    return value


def load_settings(
    environ: Optional[Mapping[str, str]] = None, *, project_root: Optional[Path] = None
) -> Settings:
    """Read, merge and validate all configuration. Raises ConfigError.

    ``environ`` defaults to ``os.environ``; values there win over the ``.env``
    file. ``.env`` is read with ``dotenv_values`` — ``os.environ`` is never
    mutated. ``project_root`` is only used to find the legacy checkout ``.env``.
    A missing AZURE_CLIENT_ID is allowed (``client_id`` is None).
    """
    environ = os.environ if environ is None else environ
    project_root = CODE_DIR if project_root is None else Path(project_root)

    if os.name != "posix":
        logging.getLogger(__name__).warning(
            "Windows/non-POSIX hosts are supported for development only. "
            "Owner-only token/download permissions require a Linux deployment."
        )

    config_dir = _checked_dir(environ, "ONEDRIVE_MCP_CONFIG_DIR", _default_config_base)
    env_path, legacy = _locate_env_file(config_dir, project_root)
    values: dict[str, str] = _read_env_file(env_path) if env_path is not None else {}
    values.update(environ)  # process environment wins over .env

    client_id = _parse_client_id(values.get("AZURE_CLIENT_ID", ""))
    tenant_id = _parse_tenant(values.get("AZURE_TENANT_ID", ""))

    data_dir = _checked_dir(values, "ONEDRIVE_MCP_DATA_DIR", _default_data_base)
    relative_base = project_root if legacy else data_dir
    token_cache = _data_path(
        values, "TOKEN_CACHE_PATH", data_dir / "token_cache.bin", relative_base, is_directory=False
    )
    download_dir = _data_path(
        values, "DOWNLOAD_DIR", data_dir / "downloads", relative_base, is_directory=True
    )
    if token_cache == download_dir:
        raise ConfigError("TOKEN_CACHE_PATH and DOWNLOAD_DIR must not resolve to the same location.")

    return Settings(
        client_id=client_id,
        tenant_id=tenant_id,
        config_dir=config_dir,
        data_dir=data_dir,
        env_path=env_path,
        legacy_env_in_use=legacy,
        token_cache_path=token_cache,
        download_dir=download_dir,
        max_download_bytes=_parse_limit(values, "MAX_DOWNLOAD_BYTES", DEFAULT_MAX_DOWNLOAD_BYTES),
        max_download_dir_bytes=_parse_limit(
            values, "MAX_DOWNLOAD_DIR_BYTES", DEFAULT_MAX_DOWNLOAD_DIR_BYTES
        ),
    )
