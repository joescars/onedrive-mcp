"""Regression coverage for bounded GETs, pagination and atomic downloads."""
import os
import time
from unittest.mock import MagicMock, Mock, patch

import pytest
import requests
import responses

import graph_client as graph
from tests.fakes import FakeTokenSource, make_client


@pytest.fixture
def tokens():
    return FakeTokenSource()


@pytest.fixture
def client(tmp_path, tokens):
    return make_client(tmp_path, tokens)


@pytest.mark.parametrize("statuses", [[401, 200], [429, 401, 200], [401, 429, 200]])
@responses.activate
def test_401_refresh_is_independent_of_throttling(statuses, tmp_path):
    tokens = FakeTokenSource()
    client = make_client(tmp_path, tokens, sleep=lambda delay: None)
    for status in statuses:
        responses.get(
            f"{graph.GRAPH_BASE}/me/drive", status=status,
            json={"id": "drive"}, headers={"Retry-After": "0"},
        )
    assert client.get_drive_info()["drive_id"] == "drive"
    flags = tokens.calls
    assert flags.count(True) == 1
    assert flags[statuses.index(401) + 1]


@responses.activate
def test_401_only_refreshes_once(client):
    responses.get(f"{graph.GRAPH_BASE}/me/drive", status=401)
    with pytest.raises(graph.GraphError, match="after a refresh"):
        client.get_drive_info()
    assert len(responses.calls) == 2


@pytest.mark.parametrize("delay", ["90", "3600"])
@responses.activate
def test_long_retry_after_is_not_shortened(delay, tmp_path):
    sleep = Mock()
    client = make_client(tmp_path, sleep=sleep)
    responses.get(f"{graph.GRAPH_BASE}/me/drive", status=429, headers={"Retry-After": delay})
    with pytest.raises(graph.GraphError, match=f"at least {delay} seconds"):
        client.get_drive_info()
    sleep.assert_not_called()
    assert len(responses.calls) == 1


@responses.activate
def test_retry_budget_and_response_cleanup(tmp_path):
    sleep = Mock()
    client = make_client(tmp_path, sleep=sleep)
    replies = [MagicMock(status_code=429, ok=False, headers={"Retry-After": "20"}) for _ in range(4)]
    with patch.object(requests, "get", side_effect=replies):
        with pytest.raises(graph.GraphError, match="Retry later"):
            client.get_drive_info()
    assert sleep.call_count == 3
    for reply in replies:
        reply.close.assert_called_once()


@pytest.mark.parametrize("header", ["nan", "inf", "-inf", "invalid"])
def test_invalid_retry_after(header):
    assert graph._parse_retry_after(header) == 2


@responses.activate
def test_graph_error_body_is_not_exposed(client):
    responses.get(f"{graph.GRAPH_BASE}/me/drive", status=500, body="SECRET-signed-url")
    with pytest.raises(graph.GraphError) as failure:
        client.get_drive_info()
    assert "HTTP 500" in str(failure.value)
    assert "SECRET" not in str(failure.value)


@responses.activate
def test_graph_request_uses_bearer_access_token(client):
    responses.get(f"{graph.GRAPH_BASE}/me/drive", json={"id": "drive"})
    assert client.get_drive_info()["drive_id"] == "drive"
    assert responses.calls[0].request.headers["Authorization"] == "Bearer test-token"


@responses.activate
def test_invalid_json_is_sanitized(client):
    responses.get(f"{graph.GRAPH_BASE}/me/drive", status=200, body="SECRET-not-json")
    with pytest.raises(graph.GraphError, match="unreadable") as failure:
        client.get_drive_info()
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
    client_class = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "OneDriveClient")
    get = next(node for node in client_class.body if isinstance(node, ast.FunctionDef) and node.name == "_get")
    assert any(isinstance(node, ast.Assert) for node in get.body)


def test_http_date_preserves_timezone():
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    future = datetime.now(timezone(timedelta(hours=3))) + timedelta(seconds=120)
    assert 118 <= graph._parse_retry_after(format_datetime(future)) <= 120


@pytest.mark.parametrize("method,argument,endpoint", [
    ("search_items", "report", "/me/drive/root/search(q='report')"),
    ("list_folder", "/", "/me/drive/root/children"),
    ("list_folder", "/Documents", "/me/drive/root:/Documents:/children"),
    ("list_folder", "123", "/me/drive/items/123/children"),
])
@responses.activate
def test_multiple_pages(client, method, argument, endpoint):
    operation = getattr(client, method)
    url = graph.GRAPH_BASE + endpoint
    continuation = url + "?$top=1&$skiptoken=opaque%2Btoken"
    responses.get(url + "?%24top=1", json={
        "value": [{"id": "1", "name": "first"}], "@odata.nextLink": continuation,
    }, match=[responses.matchers.query_param_matcher({"$top": "1"})])
    responses.get(continuation, json={"value": [{"id": "2", "name": "second"}]},
                  match=[responses.matchers.query_param_matcher({"$top": "1", "$skiptoken": "opaque+token"})])
    first = operation(argument, top=1)
    second = operation(argument, top=1, continuation=first["continuation"])
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
def test_untrusted_continuation_rejected_without_network(client, suffix):
    with patch.object(client, "_get_json") as fetch:
        with pytest.raises(graph.GraphError, match="Invalid continuation"):
            client.list_folder("/", top=2, continuation=suffix)
        fetch.assert_not_called()


@pytest.mark.parametrize("top", [0, -1, 201, True, 1.5, "20"])
def test_top_is_bounded_for_both_operations(client, top):
    with patch.object(client, "_get_json") as fetch:
        for operation in (client.list_folder, client.search_items):
            with pytest.raises(graph.GraphError, match="top must"):
                operation("test", top=top)
        fetch.assert_not_called()


def test_oversize_page_not_silently_truncated(client):
    with patch.object(client, "_get_json", return_value={"value": [{}, {}]}):
        with pytest.raises(graph.GraphError, match="no items were silently discarded"):
            client.list_folder(top=1)


@responses.activate
def test_canonical_graph_continuation_is_preserved(client):
    url = graph.GRAPH_BASE + "/me/drive/root/children"
    canonical = graph.GRAPH_BASE + "/drives/drive-id/items/root-id/children?$skiptoken=opaque"
    responses.get(url, json={"value": [], "@odata.nextLink": canonical})
    responses.get(canonical, json={"value": [{"id": "next"}]})
    first = client.list_folder("/")
    assert not first["continuation"].startswith("https://")
    assert client.list_folder("/", continuation=first["continuation"])["items"][0]["id"] == "next"


def test_continuation_is_bound_to_request_and_expires(monkeypatch, client):
    url = graph.GRAPH_BASE + "/me/drive/root/children"
    token = client._encode_continuation(url + "?$skiptoken=x", url, 50)
    with patch.object(client, "_get_json") as fetch:
        for path, top, continuation in [("/Documents", 50, token), ("/", 1, token), ("/", 50, token + "x")]:
            with pytest.raises(graph.GraphError, match="Invalid continuation"):
                client.list_folder(path, top=top, continuation=continuation)
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now + 3601)
        with pytest.raises(graph.GraphError, match="expired"):
            client.list_folder("/", continuation=token)
        fetch.assert_not_called()


def test_continuation_is_not_valid_for_another_client(tmp_path):
    first, second = make_client(tmp_path), make_client(tmp_path)
    url = graph.GRAPH_BASE + "/me/drive/root/children"
    token = first._encode_continuation(url + "?$skiptoken=x", url, 50)
    with patch.object(second, "_get_json") as fetch:
        with pytest.raises(graph.GraphError, match="Invalid continuation"):
            second.list_folder("/", continuation=token)
        fetch.assert_not_called()


def test_server_cannot_issue_foreign_continuation(client):
    with patch.object(client, "_get_json", return_value={
        "value": [], "@odata.nextLink": "https://evil.example/page",
    }):
        with pytest.raises(graph.GraphError, match="invalid continuation"):
            client.list_folder("/")


def test_search_escapes_odata_apostrophes(client):
    with patch.object(client, "_get_json", return_value={"value": []}) as fetch:
        client.search_items("O'Brien")
    assert "O%27%27Brien" in fetch.call_args.args[0]


@pytest.mark.parametrize("item_id", [".", ".."])
def test_bare_dot_item_ids_are_rejected(item_id):
    with pytest.raises(graph.GraphError, match="dot-segment"):
        graph.resolve_item_url(item_id)


ITEM = {"name": "file.txt", "size": 5, "@microsoft.graph.downloadUrl": "https://cdn.example/file?sig=SECRET"}


@pytest.fixture
def download_response(monkeypatch):
    response = MagicMock(ok=True)
    response.iter_content.return_value = iter([b"hello"])
    monkeypatch.setattr(requests, "get", Mock(return_value=response))
    return response


@pytest.fixture
def item():
    return dict(ITEM)


@pytest.fixture
def dl(tmp_path, item, download_response):
    """Build a download client (DOWNLOAD_DIR=tmp_path) with overridable limits."""
    def build(**env):
        client = make_client(tmp_path, **env)
        client._get_json = Mock(return_value=item)
        return client
    return build


def test_stream_failure_is_sanitized_and_retry_succeeds(tmp_path, dl, download_response):
    client = dl()
    def interrupted(**kwargs):
        yield b"hi"
        raise requests.ConnectionError("https://cdn.example/file?sig=SECRET")
    download_response.iter_content.side_effect = interrupted
    with pytest.raises(graph.GraphError) as failure:
        client.download_file("/file.txt")
    assert "SECRET" not in str(failure.value)
    assert not (tmp_path / "file.txt").exists()
    assert not list(tmp_path.glob(".onedrive-part-*"))
    download_response.__exit__.assert_called_once()
    download_response.iter_content.side_effect = None
    download_response.iter_content.return_value = iter([b"hello"])
    result = client.download_file("/file.txt")
    assert result["size_bytes"] == 5
    assert (tmp_path / "file.txt").read_bytes() == b"hello"


def test_publication_race_never_overwrites(tmp_path, dl):
    client = dl()
    original_link = os.link
    def race(source, destination):
        destination.write_bytes(b"existing")
        original_link(source, destination)
    with patch.object(os, "link", side_effect=race):
        with pytest.raises(graph.GraphError, match="already exists"):
            client.download_file("/file.txt")
    assert (tmp_path / "file.txt").read_bytes() == b"existing"
    assert not list(tmp_path.glob(".onedrive-part-*"))


def test_metadata_size_limit_rejects_before_transfer(dl):
    with pytest.raises(graph.GraphError, match="exceeds"):
        dl(MAX_DOWNLOAD_BYTES="4").download_file("/file.txt")
    requests.get.assert_not_called()


def test_stream_size_limit_catches_false_metadata(tmp_path, dl, item):
    item["size"] = 3
    with pytest.raises(graph.GraphError, match="exceeded"):
        dl(MAX_DOWNLOAD_BYTES="4").download_file("/file.txt")
    assert not (tmp_path / "file.txt").exists()
    assert not list(tmp_path.glob(".onedrive-part-*"))


def test_directory_budget_accounts_for_existing_files(tmp_path, dl):
    (tmp_path / "existing").write_bytes(b"123")
    with pytest.raises(graph.GraphError, match="exceeds"):
        dl(MAX_DOWNLOAD_DIR_BYTES="7").download_file("/file.txt")
    requests.get.assert_not_called()


def test_directory_cache_is_not_used_as_trusted_state_after_growth(tmp_path):
    (tmp_path / "nested").mkdir()
    payload = tmp_path / "nested" / "a.bin"
    payload.write_bytes(b"123")
    assert graph._directory_size(tmp_path) == 3
    payload.write_bytes(b"1234567")
    assert graph._directory_size(tmp_path) == 7

    (tmp_path / "nested" / "b.bin").write_bytes(b"xx")
    assert graph._directory_size(tmp_path) == 9
    (tmp_path / "nested" / "b.bin").unlink()
    assert graph._directory_size(tmp_path) == 7


def test_directory_cache_ignores_corrupt_and_symlink_metadata(tmp_path):
    cache = tmp_path / ".onedrive-dir-size"
    cache.write_text("not-a-number", encoding="utf-8")
    (tmp_path / "prod.bin").write_bytes(b"hello")
    assert graph._directory_size(tmp_path) == 5 + len("not-a-number")
    assert cache.read_text() == "not-a-number"

    cache.write_text("still-bad", encoding="utf-8")
    cache.unlink()
    outside = tmp_path.parent / "outside-dir-size-target"
    outside.write_text("not-used", encoding="utf-8")
    cache.symlink_to(outside)
    assert graph._directory_size(tmp_path) == 5
    assert cache.is_symlink()
    assert outside.read_text() == "not-used"


@pytest.mark.parametrize("relative", [".onedrive-dir-size", "nested/.onedrive-dir-size"])
def test_quota_counts_and_preserves_metadata_named_payloads(tmp_path, relative):
    payload = tmp_path / relative
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"x" * 100)
    assert graph._directory_size(tmp_path) == 100
    assert payload.read_bytes() == b"x" * 100
    assert not list(tmp_path.glob(".onedrive-dir-size-*"))


def test_quota_scan_does_not_create_metadata(tmp_path):
    assert graph._directory_size(tmp_path) == 0
    assert list(tmp_path.iterdir()) == []


def test_directory_scan_failure_does_not_bypass_quota(monkeypatch, dl):
    def denied(directory, *, onerror, followlinks):
        onerror(PermissionError("unreadable subdirectory"))
        return iter(())
    monkeypatch.setattr(os, "walk", denied)
    with pytest.raises(graph.GraphError, match="permissions"):
        dl().download_file("/file.txt")
    requests.get.assert_not_called()


def test_exact_limits_allow_download(dl):
    client = dl(MAX_DOWNLOAD_BYTES="5", MAX_DOWNLOAD_DIR_BYTES="5")
    assert client.download_file("/file.txt")["size_bytes"] == 5


def test_concurrent_downloads_share_directory_budget(tmp_path, dl):
    from concurrent.futures import ThreadPoolExecutor
    client = dl(MAX_DOWNLOAD_DIR_BYTES="5")
    def response(*args, **kwargs):
        result = MagicMock(ok=True)
        result.iter_content.return_value = iter([b"hello"])
        return result
    requests.get.side_effect = response
    def download(name):
        try:
            return client.download_file("/file.txt", name)["size_bytes"]
        except graph.GraphError as exc:
            assert "exceeds" in str(exc)
            return 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(download, ["a.txt", "b.txt"]))
    assert sorted(results) == [0, 5]
    assert sum(path.stat().st_size for path in tmp_path.glob("*.txt")) == 5


def test_missing_metadata_size_still_enforces_stream_limit(tmp_path, dl, item):
    item.pop("size")
    with pytest.raises(graph.GraphError, match="exceeded"):
        dl(MAX_DOWNLOAD_BYTES="4").download_file("/file.txt")
    assert not (tmp_path / "file.txt").exists()


@pytest.mark.parametrize("filename", [".download.lock", ".onedrive-part-test", "file:stream", "bad\0name"])
def test_reserved_names_rejected(dl, filename):
    with pytest.raises(graph.GraphError, match="Invalid destination"):
        dl().download_file("/file.txt", filename)
