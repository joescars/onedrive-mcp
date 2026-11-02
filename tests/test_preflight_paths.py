"""Credential-free filesystem preflight regressions, including real entry points."""
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

import auth


@pytest.mark.parametrize("entry", ["server", "onedrive_mcp_setup"])
@pytest.mark.parametrize("setting", [
    "ONEDRIVE_MCP_CONFIG_DIR", "ONEDRIVE_MCP_DATA_DIR",
    "TOKEN_CACHE_PATH", "DOWNLOAD_DIR", ".env",
])
def test_entry_point_sanitizes_symlink_loops(tmp_path, entry, setting):
    config = tmp_path / "config"
    config.mkdir()
    env_file = config / ".env"
    env_file.write_text("")
    private_path = tmp_path / "PRIVATE-PATH-MARKER"
    try:
        private_path.symlink_to(private_path)
    except OSError:
        pytest.skip("Creating symlinks requires platform privileges")
    data = tmp_path / "data"
    env = os.environ.copy()
    env.update({
        "ONEDRIVE_MCP_CONFIG_DIR": str(config),
        "ONEDRIVE_MCP_DATA_DIR": str(data),
        "TOKEN_CACHE_PATH": str(data / "cache.bin"),
        "DOWNLOAD_DIR": str(data / "downloads"),
        "AZURE_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
        "AZURE_TENANT_ID": "consumers",
        "MAX_DOWNLOAD_BYTES": "100",
        "MAX_DOWNLOAD_DIR_BYTES": "1000",
        "PYTHONPATH": str(auth.PROJECT_ROOT),
    })
    if setting == ".env":
        env_file.unlink()
        env_file.symlink_to(private_path)
    else:
        env[setting] = str(private_path)
    # Fail on any attempted socket access, rather than relying on credentials
    # or network availability. Run the actual entry point from unrelated cwd.
    script = f"""
import importlib
import socket
def no_network(*args, **kwargs):
    raise AssertionError('NETWORK_ATTEMPTED')
socket.socket.connect = no_network
socket.create_connection = no_network
raise SystemExit(importlib.import_module({entry!r}).main())
"""
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 1
    assert setting in result.stderr
    assert "Check" in result.stderr
    assert "Traceback" not in result.stderr
    assert "PRIVATE-PATH-MARKER" not in result.stdout + result.stderr
    assert str(tmp_path) not in result.stdout + result.stderr
    assert "NETWORK_ATTEMPTED" not in result.stderr
    if entry == "server":
        assert result.stdout == ""
    assert not data.exists()  # No cache directory, cache, lock, or download writes.
    assert private_path.is_symlink()


@pytest.mark.parametrize("operation,error", [
    ("resolve", RuntimeError("PRIVATE-PATH-MARKER")),
    ("stat", PermissionError("PRIVATE-PATH-MARKER")),
])
def test_path_failures_are_sanitized(monkeypatch, tmp_path, operation, error):
    monkeypatch.setenv("TOKEN_CACHE_PATH", str(tmp_path / "cache.bin"))
    monkeypatch.setattr(Path, operation, Mock(side_effect=error))
    with pytest.raises(auth.AuthConfigError, match="TOKEN_CACHE_PATH") as failure:
        auth._validate_path_setting("TOKEN_CACHE_PATH", is_directory=False)
    assert "PRIVATE-PATH-MARKER" not in str(failure.value)


@pytest.mark.parametrize("legacy", [False, True])
def test_preflight_preserves_relative_data_paths(monkeypatch, tmp_path, legacy):
    monkeypatch.setattr(auth, "_LEGACY_ENV_IN_USE", legacy)
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path / "checkout")
    monkeypatch.setenv("ONEDRIVE_MCP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOKEN_CACHE_PATH", "state/cache.bin")
    monkeypatch.setenv("DOWNLOAD_DIR", "downloads")
    base = tmp_path / ("checkout" if legacy else "data")
    assert auth._validate_path_setting("TOKEN_CACHE_PATH", is_directory=False) == base / "state/cache.bin"
    assert auth._validate_path_setting("DOWNLOAD_DIR", is_directory=True) == base / "downloads"
    assert not base.exists()


def test_wrong_path_type_does_not_echo_value(monkeypatch, tmp_path):
    private_path = tmp_path / "PRIVATE-PATH-MARKER"
    private_path.write_text("synthetic")
    monkeypatch.setenv("DOWNLOAD_DIR", str(private_path))
    with pytest.raises(auth.AuthConfigError, match="DOWNLOAD_DIR must point to a directory") as failure:
        auth._validate_path_setting("DOWNLOAD_DIR", is_directory=True)
    assert "PRIVATE-PATH-MARKER" not in str(failure.value)
