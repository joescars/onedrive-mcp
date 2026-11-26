"""Unit tests for settings.py: all configuration parsing, no network."""
import dataclasses
import os
import sys
from pathlib import Path

import pytest

import settings as settings_module
from settings import AuthConfigError, ConfigError
from settings import load_settings as _load


def load_settings(environ, project_root=None):
    # Never pick up a developer's real checkout .env.
    return _load(environ, project_root=project_root or Path("/nonexistent-checkout"))

GUID = "11111111-1111-1111-1111-111111111111"


def make(tmp_path, **extra):
    env = {
        "ONEDRIVE_MCP_CONFIG_DIR": str(tmp_path / "config"),
        "ONEDRIVE_MCP_DATA_DIR": str(tmp_path / "data"),
    }
    env.update(extra)
    return env


def test_auth_config_error_is_config_error():
    assert AuthConfigError is ConfigError
    import auth
    assert auth.AuthConfigError is ConfigError


def test_settings_are_immutable(tmp_path):
    s = load_settings(make(tmp_path))
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.client_id = "x"


def test_missing_client_id_is_allowed(tmp_path):
    assert load_settings(make(tmp_path)).client_id is None


def test_placeholder_client_id_rejected(tmp_path):
    with pytest.raises(ConfigError, match="AZURE_CLIENT_ID"):
        load_settings(make(tmp_path, AZURE_CLIENT_ID="00000000-0000-0000-0000-000000000000"))


def test_invalid_client_id_rejected(tmp_path):
    with pytest.raises(ConfigError, match="invalid characters"):
        load_settings(make(tmp_path, AZURE_CLIENT_ID="bad/id"))
    with pytest.raises(ConfigError, match="GUID"):
        load_settings(make(tmp_path, AZURE_CLIENT_ID="not-a-guid"))


def test_valid_client_id(tmp_path):
    assert load_settings(make(tmp_path, AZURE_CLIENT_ID=GUID)).client_id == GUID


def test_invalid_tenant_rejected(tmp_path):
    with pytest.raises(ConfigError, match="AZURE_TENANT_ID.*invalid characters"):
        load_settings(make(tmp_path, AZURE_TENANT_ID="bad?tenant"))


def test_tenant_defaults_and_authority(tmp_path):
    s = load_settings(make(tmp_path))
    assert s.tenant_id == "consumers"
    assert s.authority == "https://login.microsoftonline.com/consumers"
    assert load_settings(make(tmp_path, AZURE_TENANT_ID="common")).tenant_id == "common"


def test_same_cache_and_download_paths_rejected(tmp_path):
    target = str(tmp_path / "same")
    with pytest.raises(ConfigError, match="must not resolve to the same location"):
        load_settings(make(tmp_path, TOKEN_CACHE_PATH=target, DOWNLOAD_DIR=target))


@pytest.mark.parametrize("variable", ["MAX_DOWNLOAD_BYTES", "MAX_DOWNLOAD_DIR_BYTES"])
@pytest.mark.parametrize("value", ["0", "-5", "abc", "1.5"])
def test_limits_validated(tmp_path, variable, value):
    with pytest.raises(ConfigError, match=variable):
        load_settings(make(tmp_path, **{variable: value}))


def test_limit_defaults_and_overrides(tmp_path):
    s = load_settings(make(tmp_path))
    assert s.max_download_bytes == 100 * 1024 * 1024
    assert s.max_download_dir_bytes == 1024 * 1024 * 1024
    s = load_settings(make(tmp_path, MAX_DOWNLOAD_BYTES="7", MAX_DOWNLOAD_DIR_BYTES="9"))
    assert (s.max_download_bytes, s.max_download_dir_bytes) == (7, 9)


def test_token_cache_path_expands(tmp_path):
    s = load_settings(make(tmp_path, TOKEN_CACHE_PATH=str(tmp_path / "cache.bin")))
    assert s.token_cache_path == (tmp_path / "cache.bin").resolve()


def test_relative_paths_are_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    s = load_settings(make(tmp_path, TOKEN_CACHE_PATH="state/cache.bin", DOWNLOAD_DIR="downloads"),
                      project_root=tmp_path / "checkout")
    assert s.token_cache_path == (tmp_path / "data" / "state" / "cache.bin").resolve()
    assert s.download_dir == (tmp_path / "data" / "downloads").resolve()


def test_default_paths_use_data_dir(tmp_path):
    s = load_settings(make(tmp_path))
    assert s.token_cache_path == (tmp_path / "data" / "token_cache.bin").resolve()
    assert s.download_dir == (tmp_path / "data" / "downloads").resolve()


def test_loading_creates_nothing(tmp_path):
    load_settings(make(tmp_path))
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "config").exists()


def test_relative_dir_override_rejected(tmp_path):
    with pytest.raises(ConfigError, match="ONEDRIVE_MCP_DATA_DIR must be an absolute path"):
        load_settings({"ONEDRIVE_MCP_CONFIG_DIR": str(tmp_path), "ONEDRIVE_MCP_DATA_DIR": "rel"})


@pytest.mark.skipif(sys.platform != "linux", reason="Linux XDG behavior")
def test_default_directories_honor_xdg(tmp_path):
    env = {"XDG_DATA_HOME": str(tmp_path / "xd"), "XDG_CONFIG_HOME": str(tmp_path / "xc")}
    s = load_settings(env, project_root=tmp_path / "none")
    assert s.data_dir == (tmp_path / "xd" / "onedrive-mcp").resolve()
    assert s.config_dir == (tmp_path / "xc" / "onedrive-mcp").resolve()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux XDG behavior")
def test_empty_xdg_falls_back_to_home():
    s = load_settings({"XDG_DATA_HOME": "", "XDG_CONFIG_HOME": ""}, project_root=Path("/nonexistent-root"))
    assert s.data_dir == (Path.home() / ".local" / "share" / "onedrive-mcp").resolve()


def test_config_env_file_is_loaded_and_secured(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    env_file = config / ".env"
    env_file.write_text(f"AZURE_CLIENT_ID={GUID}\n", encoding="utf-8")
    env_file.chmod(0o644)
    environ = make(tmp_path)
    s = load_settings(environ)
    assert s.client_id == GUID
    assert s.env_path == env_file
    assert not s.legacy_env_in_use
    assert "AZURE_CLIENT_ID" not in os.environ or os.environ["AZURE_CLIENT_ID"] != GUID
    assert "AZURE_CLIENT_ID" not in environ
    if os.name == "posix":
        assert env_file.stat().st_mode & 0o777 == 0o600


def test_process_environment_wins_over_env_file(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / ".env").write_text(f"AZURE_CLIENT_ID={GUID}\nAZURE_TENANT_ID=common\n")
    other = "22222222-2222-2222-2222-222222222222"
    s = load_settings(make(tmp_path, AZURE_CLIENT_ID=other))
    assert s.client_id == other
    assert s.tenant_id == "common"


def test_config_env_beats_legacy_checkout_env(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / ".env").write_text(f"AZURE_CLIENT_ID={GUID}\n")
    root = tmp_path / "checkout"
    root.mkdir()
    (root / ".env").write_text("AZURE_CLIENT_ID=22222222-2222-2222-2222-222222222222\n")
    s = load_settings(make(tmp_path), project_root=root)
    assert s.client_id == GUID and not s.legacy_env_in_use


def test_legacy_checkout_env_keeps_relative_cache_location(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / ".env").write_text(f"AZURE_CLIENT_ID={GUID}\nTOKEN_CACHE_PATH=token_cache.bin\n")
    s = load_settings(make(tmp_path), project_root=root)
    assert s.legacy_env_in_use
    assert s.client_id == GUID
    assert s.token_cache_path == (root / "token_cache.bin").resolve()


def test_env_file_must_be_regular_file(tmp_path):
    config = tmp_path / "config"
    (config / ".env").mkdir(parents=True)
    with pytest.raises(ConfigError, match="regular file"):
        load_settings(make(tmp_path))


def test_wrong_path_type_does_not_echo_value(tmp_path):
    private = tmp_path / "PRIVATE-PATH-MARKER"
    private.write_text("synthetic")
    with pytest.raises(ConfigError, match="DOWNLOAD_DIR must point to a directory") as failure:
        load_settings(make(tmp_path, DOWNLOAD_DIR=str(private)))
    assert "PRIVATE-PATH-MARKER" not in str(failure.value)


@pytest.mark.parametrize("operation,error", [
    ("resolve", RuntimeError("PRIVATE-PATH-MARKER")),
    ("stat", PermissionError("PRIVATE-PATH-MARKER")),
])
def test_path_failures_are_sanitized(monkeypatch, tmp_path, operation, error):
    from unittest.mock import Mock
    environ = make(tmp_path, TOKEN_CACHE_PATH=str(tmp_path / "cache.bin"))
    monkeypatch.setattr(Path, operation, Mock(side_effect=error))
    with pytest.raises(ConfigError, match="ONEDRIVE_MCP_CONFIG_DIR") as failure:
        load_settings(environ)
    assert "PRIVATE-PATH-MARKER" not in str(failure.value)


def test_no_import_time_environment_access():
    source = Path(settings_module.__file__).read_text(encoding="utf-8")
    assert "load_dotenv" not in source


@pytest.mark.skipif(os.name != "nt", reason="Windows user-data directory behavior")
def test_default_user_data_directory_uses_local_appdata(tmp_path):
    s = load_settings(
        {
            "LOCALAPPDATA": str(tmp_path / "local-app-data"),
            "APPDATA": str(tmp_path / "roaming-app-data"),
        },
        project_root=tmp_path / "no-checkout",
    )
    assert s.data_dir == (tmp_path / "local-app-data" / "onedrive-mcp").resolve()
