"""Exercise the real stdio protocol with synthetic data and no Graph calls."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import Mock

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import graph_client
import server


def test_stdio_handshake_and_schema_from_arbitrary_cwd(tmp_path):
    async def check():
        parameters = StdioServerParameters(
            command=sys.executable, args=[str(Path(server.__file__).resolve())],
            cwd=str(tmp_path),
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
                        assert "next_link" in properties
                        assert properties["top"]["minimum"] == 1
                        assert properties["top"]["maximum"] == 200
                    invalid = await session.call_tool("list_folder", {"top": 201})
                    assert invalid.isError
    asyncio.run(check())


@pytest.mark.parametrize("fallback,stream_failure", [(False, False), (True, False), (False, True), (True, True)])
def test_signed_url_never_appears_in_stdio_response(tmp_path, fallback, stream_failure):
    script = f"""
from unittest.mock import MagicMock
import requests
import graph_client
import server
from pathlib import Path
graph_client.get_access_token = lambda **kwargs: 'dummy'
graph_client._get_json = lambda *args, **kwargs: {{
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
graph_client.requests.get = failing_get
server._download_dir = lambda: Path({str(tmp_path)!r})
server.mcp.run(transport='stdio')
"""
    async def check():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-c", script],
            cwd=str(Path(server.__file__).resolve().parent),
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


def test_search_preserves_fields_and_exposes_pagination(monkeypatch):
    monkeypatch.setattr(graph_client, "search_items", Mock(return_value={
        "items": [{"name": "a"}], "next_link": "next", "has_more": True,
    }))
    result = server.search_onedrive("a")
    assert result == {
        "query": "a", "count": 1, "items": [{"name": "a"}],
        "next_link": "next", "has_more": True,
    }


def test_download_dir_is_independent_of_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DOWNLOAD_DIR", "downloads")
    assert server._download_dir() == (Path(server.__file__).resolve().parent / "downloads")
