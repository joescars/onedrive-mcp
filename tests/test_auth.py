"""Unit tests for auth.TokenSource (no real MSAL network calls)."""
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

import auth
from auth import TokenSource
from settings import ConfigError
from settings import load_settings as _load


def load_settings(environ):
    return _load(environ, project_root=Path("/nonexistent-checkout"))

ROOT = Path(__file__).resolve().parent.parent
GUID = "11111111-1111-1111-1111-111111111111"


def make_env(tmp_path, **extra):
    env = {
        "ONEDRIVE_MCP_CONFIG_DIR": str(tmp_path / "config"),
        "ONEDRIVE_MCP_DATA_DIR": str(tmp_path / "data"),
        "TOKEN_CACHE_PATH": str(tmp_path / "cache.bin"),
        "AZURE_CLIENT_ID": GUID,
    }
    env.update(extra)
    return env


@pytest.fixture
def settings(tmp_path):
    return load_settings(make_env(tmp_path))


def source_with_app(settings, app=None, **kwargs):
    return TokenSource(settings, app_factory=lambda cache: app, **kwargs)


def test_missing_client_id_is_friendly(tmp_path):
    env = make_env(tmp_path)
    del env["AZURE_CLIENT_ID"]
    source = TokenSource(load_settings(env))
    with pytest.raises(ConfigError, match="AZURE_CLIENT_ID"):
        source.access_token()
    with pytest.raises(ConfigError, match="AZURE_CLIENT_ID"):
        source.sign_in_device_code()
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("force_refresh", [False, True])
def test_silent_refresh_passes_force_flag(settings, force_refresh):
    app = Mock()
    app.get_accounts.return_value = [{"username": "test"}]
    app.acquire_token_silent.return_value = {"access_token": "dummy-token"}
    assert source_with_app(settings, app).access_token(force_refresh=force_refresh) == "dummy-token"
    app.acquire_token_silent.assert_called_once_with(
        auth.SCOPES, account={"username": "test"}, force_refresh=force_refresh,
    )


def test_missing_account_is_actionable(settings):
    app = Mock()
    app.get_accounts.return_value = []
    with pytest.raises(ConfigError, match="onedrive-mcp-setup"):
        source_with_app(settings, app).access_token()


def test_failed_silent_refresh_is_actionable(settings):
    app = Mock()
    app.get_accounts.return_value = [{"username": "t"}]
    app.acquire_token_silent.return_value = None
    with pytest.raises(ConfigError, match="sign in again"):
        source_with_app(settings, app).access_token()


def test_device_code_sign_in_returns_result(settings, capsys):
    app = Mock()
    app.initiate_device_flow.return_value = {"user_code": "ABC", "message": "go sign in"}
    app.acquire_token_by_device_flow.return_value = {"access_token": "t", "scope": "Files.Read"}
    result = source_with_app(settings, app).sign_in_device_code()
    assert result["scope"] == "Files.Read"
    assert "go sign in" in capsys.readouterr().out


def test_device_code_failure_is_config_error(settings):
    app = Mock()
    app.initiate_device_flow.return_value = {"user_code": "ABC", "message": "m"}
    app.acquire_token_by_device_flow.return_value = {"error": "denied", "error_description": "no"}
    with pytest.raises(ConfigError, match="Device code login failed"):
        source_with_app(settings, app).sign_in_device_code()


def test_corrupt_cache_is_sanitized(settings):
    settings.token_cache_path.write_text("secret-corrupt-input", encoding="utf-8")
    with pytest.raises(ConfigError) as failure:
        TokenSource(settings).access_token()
    assert "secret-corrupt-input" not in str(failure.value)
    assert "cache" in str(failure.value)


def test_serialization_failure_preserves_cache(settings):
    path = settings.token_cache_path
    path.write_text("old-state", encoding="utf-8")
    cache = Mock(has_state_changed=True)
    cache.serialize.side_effect = ValueError("serialization failure")
    with pytest.raises(ValueError):
        TokenSource(settings)._save_cache(cache)
    assert path.read_text() == "old-state"
    assert not list(path.parent.glob(".token-cache-*"))


def test_replace_failure_preserves_cache_and_cleans_temporary(monkeypatch, settings):
    path = settings.token_cache_path
    path.write_text("old-state", encoding="utf-8")
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = "new-state"
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError):
        TokenSource(settings)._save_cache(cache)
    assert path.read_text() == "old-state"
    assert not list(path.parent.glob(".token-cache-*"))


def test_flush_failure_preserves_cache(monkeypatch, settings):
    path = settings.token_cache_path
    path.write_text("old-state", encoding="utf-8")
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = "new-state"
    monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        TokenSource(settings)._save_cache(cache)
    assert path.read_text() == "old-state"
    assert not list(path.parent.glob(".token-cache-*"))


def test_cache_lock_timeout_is_actionable(settings):
    from filelock import FileLock
    settings.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(settings.token_cache_path) + ".lock"):
        with pytest.raises(ConfigError, match="busy"):
            TokenSource(settings, lock_timeout=0.1).access_token()


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem mode guarantee")
def test_default_data_directory_is_owner_only(tmp_path):
    env = make_env(tmp_path)
    del env["TOKEN_CACHE_PATH"]
    s = load_settings(env)
    with TokenSource(s)._cache_transaction():
        pass
    assert s.data_dir.stat().st_mode & 0o777 == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX filesystem mode guarantee")
def test_atomic_cache_created_owner_only_even_when_old_file_is_public(settings):
    path = settings.token_cache_path
    path.write_text("old-state", encoding="utf-8")
    path.chmod(0o644)
    cache = Mock(has_state_changed=True)
    cache.serialize.return_value = "{}"
    TokenSource(settings)._save_cache(cache)
    assert path.stat().st_mode & 0o777 == 0o600


def test_cache_transactions_serialize_across_processes(tmp_path, settings):
    script = """
import json, os
from auth import TokenSource
from settings import load_settings
source = TokenSource(load_settings())
for _ in range(10):
    with source._cache_transaction():
        cache = source._load_cache()
        state = json.loads(cache.serialize())
        state['test_counter'] = state.get('test_counter', 0) + 1
        cache.deserialize(json.dumps(state))
        cache.has_state_changed = True
        source._save_cache(cache)
"""
    env = {**os.environ, **make_env(tmp_path), "PYTHONPATH": str(ROOT)}
    processes = [
        subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, env=env,
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
    assert json.loads(settings.token_cache_path.read_text())["test_counter"] == 20


def test_user_config_is_loaded_by_entry_point_from_arbitrary_cwd(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(mode=0o700)
    (config_dir / ".env").write_text(f"AZURE_CLIENT_ID={GUID}\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("AZURE_CLIENT_ID", None)
    env["ONEDRIVE_MCP_CONFIG_DIR"] = str(config_dir)
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", "from settings import load_settings; print(load_settings().client_id)"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=10,
    )
    assert result.stdout.strip() == GUID
    if os.name == "posix":
        assert (config_dir / ".env").stat().st_mode & 0o777 == 0o600


def test_importing_modules_does_not_read_env_or_warn():
    script = "import os; os.environ['AZURE_CLIENT_ID']='bad/id'; import auth, graph_client, server, settings"
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_auth_transport_errors_are_sanitized(settings):
    def factory(cache):
        raise requests.ConnectionError("SECRET")
    with pytest.raises(ConfigError) as failure:
        TokenSource(settings, app_factory=factory).access_token()
    assert "SECRET" not in str(failure.value)
    assert "connection failed" in str(failure.value)
