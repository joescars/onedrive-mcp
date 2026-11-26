"""Shared test doubles: a fake token source and a settings/client factory."""
from graph_client import OneDriveClient
from settings import load_settings


class FakeTokenSource:
    def __init__(self, token: str = "test-token"):
        self.token = token
        self.calls = []

    def access_token(self, *, force_refresh: bool = False) -> str:
        self.calls.append(force_refresh)
        return self.token


def make_settings(tmp_path, **env):
    environ = {
        "ONEDRIVE_MCP_CONFIG_DIR": str(tmp_path / "_config"),
        "ONEDRIVE_MCP_DATA_DIR": str(tmp_path / "_data"),
        "TOKEN_CACHE_PATH": str(tmp_path / "_cache.bin"),
        "DOWNLOAD_DIR": str(tmp_path),
        "AZURE_CLIENT_ID": "11111111-1111-1111-1111-111111111111",
    }
    environ.update(env)
    return load_settings(environ, project_root=tmp_path / "_no_checkout")


def make_client(tmp_path, token_source=None, sleep=None, **env):
    kwargs = {} if sleep is None else {"sleep": sleep}
    return OneDriveClient(
        make_settings(tmp_path, **env), token_source or FakeTokenSource(), **kwargs
    )
