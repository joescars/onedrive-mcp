"""Exercise the real stdio protocol with synthetic data and no Graph calls."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import Mock

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import server
from tests.fakes import make_client


def test_stdio_handshake_and_schema_from_arbitrary_cwd(tmp_path):
    config = tmp_path / "config"
    config.mkdir()
    (config / ".env").write_text("")
    async def check():
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(Path(server.__file__).resolve())],
            cwd=str(tmp_path),
            env={
                "ONEDRIVE_MCP_CONFIG_DIR": str(config),
                "ONEDRIVE_MCP_DATA_DIR": str(tmp_path / "data"),
                "TOKEN_CACHE_PATH": str(tmp_path / "data" / "cache.bin"),
                "DOWNLOAD_DIR": str(tmp_path / "data" / "downloads"),
                "AZURE_CLIENT_ID": "",
                "AZURE_TENANT_ID": "consumers",
                "MAX_DOWNLOAD_BYTES": "100",
                "MAX_DOWNLOAD_DIR_BYTES": "1000",
            },
        )
        with anyio.fail_after(30):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                    assert set(tools) == {
                        "search_onedrive", "list_folder", "get_item_metadata",
                        "download_file", "get_drive_info",
                    }
                    for name in ("search_onedrive", "list_folder"):
                        properties = tools[name].inputSchema["properties"]
                        assert "continuation" in properties
                        assert "next_link" not in properties
                        assert properties["top"]["minimum"] == 1
                        assert properties["top"]["maximum"] == 200
                    invalid = await session.call_tool("list_folder", {"top": 201})
                    assert invalid.isError
    asyncio.run(check())
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("fallback,stream_failure", [(False, False), (True, False), (False, True), (True, True)])
def test_signed_url_never_appears_in_stdio_response(tmp_path, fallback, stream_failure):
    script = f"""
from unittest.mock import MagicMock, Mock
import requests
import server
from pathlib import Path
from tests.fakes import make_client
client = make_client(Path({str(tmp_path)!r}))
client._get_json = lambda *args, **kwargs: {{
    'name': 'test.txt', 'size': 2,
    '@microsoft.graph.downloadUrl': {None if fallback else 'https://cdn.example/file?sig=SECRET'!r},
}}
def failing_get(*args, **kwargs):
    failure = requests.ConnectionError('https://cdn.example/file?sig=SECRET')
    if not {stream_failure!r}:
        raise failure
    response = MagicMock(ok=True)
    def chunks(**kwargs):
        yield b'x'
        raise failure
    response.iter_content.side_effect = chunks
    return response
requests.get = failing_get
server.build_server(client).run(transport='stdio')
"""
    async def check():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-c", script],
            cwd=str(Path(server.__file__).resolve().parent),
            env={"PYTHONPATH": str(Path(server.__file__).resolve().parent)},
        )
        with anyio.fail_after(30):
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.call_tool("download_file", {"path_or_id": "/test.txt"})
                    content = result.model_dump_json()
                    assert "SECRET" not in content
                    assert "cdn.example" not in content
                    assert "failed" in content.lower()
    asyncio.run(check())
    assert not (tmp_path / "test.txt").exists()
    assert not list(tmp_path.glob(".onedrive-part-*"))


def tools_for(client):
    return {tool.name: tool.fn for tool in server.build_server(client)._tool_manager.list_tools()}


def test_search_preserves_fields_and_exposes_pagination(tmp_path):
    client = make_client(tmp_path)
    client.search_items = Mock(return_value={
        "items": [{"name": "a"}], "continuation": "next", "has_more": True,
    })
    result = tools_for(client)["search_onedrive"]("a")
    assert result == {
        "query": "a", "count": 1, "items": [{"name": "a"}],
        "continuation": "next", "has_more": True,
    }
    client.search_items.assert_called_once_with("a", top=20, continuation=None)


def test_graph_and_config_errors_become_error_dicts(tmp_path):
    from graph_client import GraphError
    from settings import ConfigError
    client = make_client(tmp_path)
    client.get_drive_info = Mock(side_effect=GraphError("boom"))
    assert tools_for(client)["get_drive_info"]() == {"error": "boom"}
    client.get_drive_info = Mock(side_effect=ConfigError("AZURE_CLIENT_ID missing"))
    assert tools_for(client)["get_drive_info"]() == {"error": "AZURE_CLIENT_ID missing"}


def test_tools_without_client_return_friendly_error():
    tools = tools_for(None)
    assert set(tools) == {"search_onedrive", "list_folder", "get_item_metadata",
                          "download_file", "get_drive_info"}
    assert "not configured" in tools["get_drive_info"]()["error"]
    assert "error" in tools["list_folder"]()


def test_download_uses_settings_download_dir(tmp_path):
    client = make_client(tmp_path)
    client.download_file = Mock(return_value={"local_path": "x"})
    assert tools_for(client)["download_file"]("/a.txt", "b.txt") == {"local_path": "x"}
    client.download_file.assert_called_once_with("/a.txt", "b.txt")
