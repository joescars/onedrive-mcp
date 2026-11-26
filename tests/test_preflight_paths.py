"""Credential-free filesystem preflight regressions, including real entry points."""
import os
import subprocess
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent


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
        "PYTHONPATH": str(ROOT),
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
