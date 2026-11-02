"""Unit tests for auth.py configuration handling (no real MSAL network calls)."""
import sys
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import auth


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_CACHE_PATH", str(tmp_path / "cache.bin"))


def test_get_client_id_raises_when_placeholder(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "00000000-0000-0000-0000-000000000000")
    with pytest.raises(auth.AuthConfigError):
        auth.get_client_id()


def test_get_client_id_raises_when_empty(monkeypatch):
    monkeypatch.delenv("AZURE_CLIENT_ID", raising=False)
    with pytest.raises(auth.AuthConfigError):
        auth.get_client_id()


def test_get_client_id_returns_real_value(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    assert auth.get_client_id() == "11111111-1111-1111-1111-111111111111"


def test_server_main_validates_runtime_config_before_start(monkeypatch):
    import server

    validator = Mock()
    monkeypatch.setattr(server, "validate_runtime_config", validator)
    run = Mock()
    monkeypatch.setattr(server.mcp, "run", run)

    server.main()
    validator.assert_called_once_with()
    run.assert_called_once_with(transport="stdio")


def test_setup_main_validates_runtime_config_before_start(monkeypatch):
    import onedrive_mcp_setup

    validator = Mock()
    monkeypatch.setattr(onedrive_mcp_setup, "validate_runtime_config", validator)
    run = Mock(return_value={"scope": "Files.Read"})
    monkeypatch.setattr(onedrive_mcp_setup, "run_device_code_flow", run)

    assert onedrive_mcp_setup.main() == 0
    validator.assert_called_once_with()
    run.assert_called_once_with()


def test_validate_runtime_config_rejects_invalid_client_id(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "bad/id")
    with pytest.raises(auth.AuthConfigError, match="invalid characters"):
        auth.validate_runtime_config()


def test_validate_runtime_config_rejects_invalid_tenant(monkeypatch):
    monkeypatch.setenv("AZURE_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    monkeypatch.setenv("AZURE_TENANT_ID", "bad?tenant")
    with pytest.raises(auth.AuthConfigError, match="invalid characters"):
        auth.validate_runtime_config()


def test_validate_runtime_config_rejects_same_cache_and_download_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("AZURE_CLIENT_ID", "11111111-1111-1111-1111-111111111111")
    target = tmp_path / "same" 
    monkeypatch.setenv("TOKEN_CACHE_PATH", str(target))
    monkeypatch.setenv("DOWNLOAD_DIR", str(target))
    with pytest.raises(auth.AuthConfigError, match="must not resolve to the same location"):
        auth.validate_runtime_config()


def test_get_tenant_id_defaults_to_consumers(monkeypatch):
    monkeypatch.delenv("AZURE_TENANT_ID", raising=False)
    assert auth.get_tenant_id() == "consumers"


def test_get_tenant_id_respects_env(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "common")
    assert auth.get_tenant_id() == "common"


def test_get_authority_uses_tenant(monkeypatch):
    monkeypatch.setenv("AZURE_TENANT_ID", "consumers")
    assert auth.get_authority() == "https://login.microsoftonline.com/consumers"


def test_get_token_cache_path_expands(monkeypatch, tmp_path):
    monkeypatch.setenv("TOKEN_CACHE_PATH", str(tmp_path / "cache.bin"))
    assert auth.get_token_cache_path() == (tmp_path / "cache.bin").resolve()


def test_relative_cache_path_is_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(auth, "_LEGACY_ENV_IN_USE", False)
    monkeypatch.setenv("ONEDRIVE_MCP_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("TOKEN_CACHE_PATH", "state/cache.bin")
    assert auth.get_token_cache_path() == (tmp_path / "app-data" / "state" / "cache.bin").resolve()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux XDG data-directory behavior")
def test_default_user_data_directory_honors_xdg(monkeypatch, tmp_path):
    monkeypatch.delenv("ONEDRIVE_MCP_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    assert auth.get_user_data_dir() == (tmp_path / "xdg-data" / "onedrive-mcp").resolve()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux XDG data-directory behavior")
def test_empty_xdg_value_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("ONEDRIVE_MCP_DATA_DIR", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", "")
    assert auth.get_user_data_dir() == (Path.home() / ".local" / "share" / "onedrive-mcp").resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows user-data directory behavior")
def test_default_user_data_directory_uses_local_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("ONEDRIVE_MCP_DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming-app-data"))
    assert auth.get_user_data_dir() == (tmp_path / "local-app-data" / "onedrive-mcp").resolve()


def test_default_cache_and_download_paths_use_user_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_LEGACY_ENV_IN_USE", False)
    monkeypatch.setenv("ONEDRIVE_MCP_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.delenv("TOKEN_CACHE_PATH", raising=False)
    monkeypatch.delenv("DOWNLOAD_DIR", raising=False)
    assert auth.get_token_cache_path() == (tmp_path / "app-data" / "token_cache.bin").resolve()
    assert auth.get_download_dir() == (tmp_path / "app-data" / "downloads").resolve()


def test_legacy_checkout_env_keeps_relative_cache_location(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "_LEGACY_ENV_IN_USE", True)
    monkeypatch.setenv("TOKEN_CACHE_PATH", "token_cache.bin")
    assert auth.get_token_cache_path() == (auth.PROJECT_ROOT / "token_cache.bin").resolve()


@pytest.mark.parametrize("force_refresh", [False, True])
def test_silent_refresh_passes_force_flag(monkeypatch, force_refresh):
    app = Mock()
    app.get_accounts.return_value = [{"username": "test"}]
    app.acquire_token_silent.return_value = {"access_token": "dummy-token"}
    monkeypatch.setattr(auth, "build_app", Mock(return_value=app))
    assert auth.get_access_token(force_refresh=force_refresh) == "dummy-token"
    app.acquire_token_silent.assert_called_once_with(
        auth.SCOPES, account={"username": "test"}, force_refresh=force_refresh,
    )


def test_missing_account_is_actionable(monkeypatch):
    app = Mock()
    app.get_accounts.return_value = []
    monkeypatch.setattr(auth, "build_app", Mock(return_value=app))
    with pytest.raises(auth.AuthConfigError, match="onedrive-mcp-setup"):
        auth.get_access_token()


def test_corrupt_cache_is_sanitized():
    auth.get_token_cache_path().write_text("secret-corrupt-input", encoding="utf-8")
    with pytest.raises(auth.AuthConfigError) as failure:
        auth.get_access_token()
    assert "secret-corrupt-input" not in str(failure.value)
    assert "cache" in str(failure.value)


def test_serialization_failure_preserves_cache():
    path = auth.get_token_cache_path()
    path.write_text("old-state", encoding="utf-8")
    cache = Mock(has_state_changed=True)
    cache.serialize.side_effect = ValueError("serialization failure")
    with pytest.raises(ValueError):
        auth._save_cache(cache)
    assert path.read_text() == "old-state"
    assert not list(path.parent.glob(".token-cache-*"))


def test_replace_failure_preserves_cache_and_cleans_temporary(monkeypatch):
    path = auth.get_token_cache_path()
    path.write_text("old-state", encoding="utf-8")
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = "new-state"
    monkeypatch.setattr(auth.os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError):
        auth._save_cache(cache)
    assert path.read_text() == "old-state"
    assert not list(path.parent.glob(".token-cache-*"))


def test_flush_failure_preserves_cache(monkeypatch):
    path = auth.get_token_cache_path()
    path.write_text("old-state", encoding="utf-8")
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = "new-state"
    monkeypatch.setattr(auth.os, "fsync", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        auth._save_cache(cache)
    assert path.read_text() == "old-state"
    assert not list(path.parent.glob(".token-cache-*"))


def test_cache_lock_timeout_is_actionable(monkeypatch):
    from filelock import Timeout
    monkeypatch.setattr(auth, "FileLock", Mock(side_effect=Timeout("dummy-lock")))
    with pytest.raises(auth.AuthConfigError, match="busy"):
        auth.get_access_token()


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem mode guarantee")
def test_default_data_directory_is_owner_only(monkeypatch, tmp_path):
    data_dir = tmp_path / "private-data"
    monkeypatch.setenv("ONEDRIVE_MCP_DATA_DIR", str(data_dir))
    monkeypatch.delenv("TOKEN_CACHE_PATH", raising=False)
    with auth._cache_transaction():
        pass
    assert data_dir.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem mode guarantee")
def test_atomic_cache_created_owner_only_even_when_old_file_is_public():
    path = auth.get_token_cache_path()
    path.write_text("old-state", encoding="utf-8")
    path.chmod(0o644)
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = "{}"
    auth._save_cache(cache)
    assert path.stat().st_mode & 0o777 == 0o600


def test_cache_transactions_serialize_across_processes():
    script = """
import json
import auth
for _ in range(10):
    with auth._cache_transaction():
        cache = auth._load_cache()
        state = json.loads(cache.serialize())
        state['test_counter'] = state.get('test_counter', 0) + 1
        cache.deserialize(json.dumps(state))
        cache.has_state_changed = True
        auth._save_cache(cache)
"""
    processes = [
        subprocess.Popen([sys.executable, "-c", script], cwd=auth.PROJECT_ROOT,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(2)
    ]
    try:
        for process in processes:
            _, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr.decode()
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert json.loads(auth.get_token_cache_path().read_text())["test_counter"] == 20


def test_user_config_is_loaded_from_arbitrary_cwd(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(mode=0o700)
    (config_dir / ".env").write_text("AZURE_CLIENT_ID=11111111-1111-1111-1111-111111111111\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("AZURE_CLIENT_ID", None)
    env["ONEDRIVE_MCP_CONFIG_DIR"] = str(config_dir)
    env["PYTHONPATH"] = str(auth.PROJECT_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", "import auth; print(auth.get_client_id())"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    assert result.stdout.strip() == "11111111-1111-1111-1111-111111111111"
    if os.name == "posix":
        assert (config_dir / ".env").stat().st_mode & 0o777 == 0o600


def test_auth_transport_errors_are_sanitized(monkeypatch):
    import requests
    monkeypatch.setattr(auth, "build_app", Mock(side_effect=requests.ConnectionError("SECRET")))
    with pytest.raises(auth.AuthConfigError) as failure:
        auth.get_access_token()
    assert "SECRET" not in str(failure.value)
    assert "connection failed" in str(failure.value)
