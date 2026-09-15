"""Regression coverage for bounded GETs, pagination and atomic downloads."""
import os
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
import responses

import graph_client as graph


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setattr(graph, "get_access_token", Mock(return_value="test-token"))
    monkeypatch.delenv("MAX_DOWNLOAD_BYTES", raising=False)
    monkeypatch.delenv("MAX_DOWNLOAD_DIR_BYTES", raising=False)


@pytest.mark.parametrize("statuses", [[401, 200], [429, 401, 200], [401, 429, 200]])
@responses.activate
def test_401_refresh_is_independent_of_throttling(statuses):
    for status in statuses:
        responses.get(
            f"{graph.GRAPH_BASE}/me/drive", status=status,
            json={"id": "drive"}, headers={"Retry-After": "0"},
        )
    assert graph.get_drive_info()["drive_id"] == "drive"
    flags = [call.kwargs["force_refresh"] for call in graph.get_access_token.call_args_list]
    assert flags.count(True) == 1
    assert flags[statuses.index(401) + 1]


@responses.activate
def test_401_only_refreshes_once():
    responses.get(f"{graph.GRAPH_BASE}/me/drive", status=401)
    with pytest.raises(graph.GraphError, match="after a refresh"):
        graph.get_drive_info()
    assert len(responses.calls) == 2


@pytest.mark.parametrize("delay", ["90", "3600"])
@responses.activate
def test_long_retry_after_is_not_shortened(delay):
    responses.get(f"{graph.GRAPH_BASE}/me/drive", status=429, headers={"Retry-After": delay})
    with patch.object(graph.time, "sleep") as sleep:
        with pytest.raises(graph.GraphError, match=f"at least {delay} seconds"):
            graph.get_drive_info()
        sleep.assert_not_called()
    assert len(responses.calls) == 1


@responses.activate
def test_retry_budget_and_response_cleanup():
    replies = [MagicMock(status_code=429, ok=False, headers={"Retry-After": "20"}) for _ in range(4)]
    with patch.object(graph.requests, "get", side_effect=replies), patch.object(graph.time, "sleep") as sleep:
        with pytest.raises(graph.GraphError, match="Retry later"):
            graph.get_drive_info()
    assert sleep.call_count == 3
    for reply in replies:
        reply.close.assert_called_once()


@pytest.mark.parametrize("header", ["nan", "inf", "-inf", "invalid"])
def test_invalid_retry_after(header):
    assert graph._parse_retry_after(header) == 2


@responses.activate
def test_graph_error_body_is_not_exposed():
    responses.get(f"{graph.GRAPH_BASE}/me/drive", status=500, body="SECRET-signed-url")
    with pytest.raises(graph.GraphError) as failure:
        graph.get_drive_info()
    assert "HTTP 500" in str(failure.value)
    assert "SECRET" not in str(failure.value)


@responses.activate
def test_invalid_json_is_sanitized():
    responses.get(f"{graph.GRAPH_BASE}/me/drive", status=200, body="SECRET-not-json")
    with pytest.raises(graph.GraphError, match="unreadable") as failure:
        graph.get_drive_info()
    assert "SECRET" not in str(failure.value)


def test_graph_module_preserves_get_only_invariant():
    import ast
    from pathlib import Path
    module = ast.parse(Path(graph.__file__).read_text(encoding="utf-8"))
    forbidden = {"post", "put", "patch", "delete", "request"}
    assert not [
        node for node in ast.walk(module)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in forbidden
    ]
    get = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "_get")
    assert any(isinstance(node, ast.Assert) for node in get.body)


def test_http_date_preserves_timezone():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    future = datetime.now(timezone(timedelta(hours=3))) + timedelta(seconds=120)
    assert 118 <= graph._parse_retry_after(format_datetime(future)) <= 120


@pytest.mark.parametrize("operation,argument,endpoint", [
    (graph.search_items, "report", "/me/drive/root/search(q='report')"),
    (graph.list_folder, "/", "/me/drive/root/children"),
    (graph.list_folder, "/Documents", "/me/drive/root:/Documents:/children"),
    (graph.list_folder, "123", "/me/drive/items/123/children"),
])
@responses.activate
def test_multiple_pages(operation, argument, endpoint):
    url = graph.GRAPH_BASE + endpoint
    next_link = url + "?$top=1&$skiptoken=opaque%2Btoken"
    responses.get(url + "?%24top=1", json={
        "value": [{"id": "1", "name": "first"}], "@odata.nextLink": next_link,
    }, match=[responses.matchers.query_param_matcher({"$top": "1"})])
    responses.get(next_link, json={"value": [{"id": "2", "name": "second"}]},
                  match=[responses.matchers.query_param_matcher({"$top": "1", "$skiptoken": "opaque+token"})])
    first = operation(argument, top=1)
    second = operation(argument, top=1, next_link=first["next_link"])
    assert first["has_more"] is True
    assert second["has_more"] is False
    assert [first["items"][0]["id"], second["items"][0]["id"]] == ["1", "2"]


@pytest.mark.parametrize("suffix", [
    "https://evil.example/v1.0/me/drive/root/children?$skiptoken=x",
    "http://graph.microsoft.com/v1.0/me/drive/root/children?$skiptoken=x",
    "https://graph.microsoft.com@evil.example/v1.0/me/drive/root/children?$skiptoken=x",
    "https://graph.microsoft.com/v1.0/users?$skiptoken=x",
    "https://graph.microsoft.com/v1.0/me/drive/root/children?$top=9999&$skiptoken=x",
    "https://graph.microsoft.com/v1.0/me/drive/root/children?$top=2&$top=2",
    "https://graph.microsoft.com/v1.0/me/drive/root/children?$expand=children",
    "",
])
def test_untrusted_continuation_rejected_without_network(suffix):
    with patch.object(graph, "_get_json") as fetch:
        with pytest.raises(graph.GraphError, match="Invalid next_link"):
            graph.list_folder("/", top=2, next_link=suffix)
        fetch.assert_not_called()


@pytest.mark.parametrize("top", [0, -1, 201, True, 1.5, "20"])
def test_top_is_bounded_for_both_operations(top):
    with patch.object(graph, "_get_json") as fetch:
        for operation in (graph.list_folder, graph.search_items):
            with pytest.raises(graph.GraphError, match="top must"):
                operation("test", top=top)
        fetch.assert_not_called()


def test_oversize_page_not_silently_truncated():
    with patch.object(graph, "_get_json", return_value={"value": [{}, {}]}):
        with pytest.raises(graph.GraphError, match="no items were silently discarded"):
            graph.list_folder(top=1)


@responses.activate
def test_canonical_graph_continuation_is_preserved():
    url = graph.GRAPH_BASE + "/me/drive/root/children"
    canonical = graph.GRAPH_BASE + "/drives/drive-id/items/root-id/children?$skiptoken=opaque"
    responses.get(url, json={"value": [], "@odata.nextLink": canonical})
    responses.get(canonical, json={"value": [{"id": "next"}]})
    first = graph.list_folder("/")
    assert not first["next_link"].startswith("https://")
    assert graph.list_folder("/", next_link=first["next_link"])["items"][0]["id"] == "next"


def test_continuation_is_bound_to_request_and_expires(monkeypatch):
    url = graph.GRAPH_BASE + "/me/drive/root/children"
    token = graph._encode_next_link(url + "?$skiptoken=x", url, 50)
    with patch.object(graph, "_get_json") as fetch:
        for path, top, continuation in [("/Documents", 50, token), ("/", 1, token), ("/", 50, token + "x")]:
            with pytest.raises(graph.GraphError, match="Invalid next_link"):
                graph.list_folder(path, top=top, next_link=continuation)
        now = graph.time.time()
        monkeypatch.setattr(graph.time, "time", lambda: now + 3601)
        with pytest.raises(graph.GraphError, match="expired"):
            graph.list_folder("/", next_link=token)
        fetch.assert_not_called()


def test_server_cannot_issue_foreign_continuation():
    with patch.object(graph, "_get_json", return_value={
        "value": [], "@odata.nextLink": "https://evil.example/page",
    }):
        with pytest.raises(graph.GraphError, match="invalid continuation"):
            graph.list_folder("/")


def test_search_escapes_odata_apostrophes():
    with patch.object(graph, "_get_json", return_value={"value": []}) as fetch:
        graph.search_items("O'Brien")
    assert "O%27%27Brien" in fetch.call_args.args[0]


@pytest.mark.parametrize("item_id", [".", ".."])
def test_bare_dot_item_ids_are_rejected(item_id):
    with pytest.raises(graph.GraphError, match="dot-segment"):
        graph.resolve_item_url(item_id)


@pytest.fixture
def download_response(monkeypatch):
    response = MagicMock(ok=True)
    response.iter_content.return_value = iter([b"hello"])
    item = {"name": "file.txt", "size": 5, "@microsoft.graph.downloadUrl": "https://cdn.example/file?sig=SECRET"}
    monkeypatch.setattr(graph, "_get_json", Mock(return_value=item))
    monkeypatch.setattr(graph.requests, "get", Mock(return_value=response))
    return response


def test_stream_failure_is_sanitized_and_retry_succeeds(tmp_path, download_response):
    def interrupted(**kwargs):
        yield b"hi"
        raise requests.ConnectionError("https://cdn.example/file?sig=SECRET")
    download_response.iter_content.side_effect = interrupted
    with pytest.raises(graph.GraphError) as failure:
        graph.download_file("/file.txt", tmp_path)
    assert "SECRET" not in str(failure.value)
    assert not (tmp_path / "file.txt").exists()
    assert not list(tmp_path.glob(".onedrive-part-*"))
    download_response.__exit__.assert_called_once()
    download_response.iter_content.side_effect = None
    download_response.iter_content.return_value = iter([b"hello"])
    result = graph.download_file("/file.txt", tmp_path)
    assert result["size_bytes"] == 5
    assert (tmp_path / "file.txt").read_bytes() == b"hello"


def test_publication_race_never_overwrites(tmp_path, download_response):
    original_link = os.link
    def race(source, destination):
        destination.write_bytes(b"existing")
        original_link(source, destination)
    with patch.object(graph.os, "link", side_effect=race):
        with pytest.raises(graph.GraphError, match="already exists"):
            graph.download_file("/file.txt", tmp_path)
    assert (tmp_path / "file.txt").read_bytes() == b"existing"
    assert not list(tmp_path.glob(".onedrive-part-*"))


def test_metadata_size_limit_rejects_before_transfer(tmp_path, monkeypatch, download_response):
    monkeypatch.setenv("MAX_DOWNLOAD_BYTES", "4")
    with pytest.raises(graph.GraphError, match="exceeds"):
        graph.download_file("/file.txt", tmp_path)
    graph.requests.get.assert_not_called()


def test_stream_size_limit_catches_false_metadata(tmp_path, monkeypatch, download_response):
    monkeypatch.setenv("MAX_DOWNLOAD_BYTES", "4")
    graph._get_json.return_value["size"] = 3
    with pytest.raises(graph.GraphError, match="exceeded"):
        graph.download_file("/file.txt", tmp_path)
    assert not (tmp_path / "file.txt").exists()
    assert not list(tmp_path.glob(".onedrive-part-*"))


def test_directory_budget_accounts_for_existing_files(tmp_path, monkeypatch, download_response):
    (tmp_path / "existing").write_bytes(b"123")
    monkeypatch.setenv("MAX_DOWNLOAD_DIR_BYTES", "7")
    with pytest.raises(graph.GraphError, match="exceeds"):
        graph.download_file("/file.txt", tmp_path)
    graph.requests.get.assert_not_called()


def test_directory_scan_failure_does_not_bypass_quota(tmp_path, monkeypatch, download_response):
    def denied(directory, *, onerror, followlinks):
        onerror(PermissionError("unreadable subdirectory"))
        return iter(())
    monkeypatch.setattr(graph.os, "walk", denied)
    with pytest.raises(graph.GraphError, match="permissions"):
        graph.download_file("/file.txt", tmp_path)
    graph.requests.get.assert_not_called()


def test_exact_limits_allow_download(tmp_path, monkeypatch, download_response):
    monkeypatch.setenv("MAX_DOWNLOAD_BYTES", "5")
    monkeypatch.setenv("MAX_DOWNLOAD_DIR_BYTES", "5")
    assert graph.download_file("/file.txt", tmp_path)["size_bytes"] == 5


def test_concurrent_downloads_share_directory_budget(tmp_path, monkeypatch, download_response):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv("MAX_DOWNLOAD_DIR_BYTES", "5")
    def response(*args, **kwargs):
        result = MagicMock(ok=True)
        result.iter_content.return_value = iter([b"hello"])
        return result
    graph.requests.get.side_effect = response
    def download(name):
        try:
            return graph.download_file("/file.txt", tmp_path, name)["size_bytes"]
        except graph.GraphError as exc:
            assert "exceeds" in str(exc)
            return 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(download, ["a.txt", "b.txt"]))
    assert sorted(results) == [0, 5]
    assert sum(path.stat().st_size for path in tmp_path.glob("*.txt")) == 5


def test_missing_metadata_size_still_enforces_stream_limit(tmp_path, monkeypatch, download_response):
    monkeypatch.setenv("MAX_DOWNLOAD_BYTES", "4")
    graph._get_json.return_value.pop("size")
    with pytest.raises(graph.GraphError, match="exceeded"):
        graph.download_file("/file.txt", tmp_path)
    assert not (tmp_path / "file.txt").exists()


@pytest.mark.parametrize("value", ["0", "-1", "invalid"])
def test_invalid_limit_configuration(tmp_path, monkeypatch, download_response, value):
    monkeypatch.setenv("MAX_DOWNLOAD_BYTES", value)
    with pytest.raises(graph.GraphError, match="positive integer"):
        graph.download_file("/file.txt", tmp_path)


@pytest.mark.parametrize("filename", [".download.lock", ".onedrive-part-test", "file:stream", "bad\0name"])
def test_reserved_names_rejected(tmp_path, download_response, filename):
    with pytest.raises(graph.GraphError, match="Invalid destination"):
        graph.download_file("/file.txt", tmp_path, filename)
