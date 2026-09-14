"""Unit tests for graph_client.py path resolution and response shaping.

No real network calls — Graph HTTP responses are mocked with `responses`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import responses as responses_lib

import graph_client
from graph_client import (
    GRAPH_BASE,
    build_item_url_from_id,
    build_item_url_from_path,
    resolve_item_url,
    shape_drive_item,
    shape_drive_item_full,
)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def test_build_item_url_from_path_root():
    assert build_item_url_from_path("/") == f"{GRAPH_BASE}/me/drive/root"
    assert build_item_url_from_path("root") == f"{GRAPH_BASE}/me/drive/root"
    assert build_item_url_from_path("") == f"{GRAPH_BASE}/me/drive/root"


def test_build_item_url_from_path_nested():
    assert (
        build_item_url_from_path("/Documents/foo.pdf")
        == f"{GRAPH_BASE}/me/drive/root:/Documents/foo.pdf"
    )


def test_build_item_url_from_path_adds_leading_slash():
    assert (
        build_item_url_from_path("Documents/foo.pdf")
        == f"{GRAPH_BASE}/me/drive/root:/Documents/foo.pdf"
    )


def test_build_item_url_from_id():
    assert (
        build_item_url_from_id("01ABCXYZ123")
        == f"{GRAPH_BASE}/me/drive/items/01ABCXYZ123"
    )


def test_resolve_item_url_dispatches_path_vs_id():
    assert resolve_item_url("/Documents") == build_item_url_from_path("/Documents")
    assert resolve_item_url("01ABCXYZ123") == build_item_url_from_id("01ABCXYZ123")
    assert resolve_item_url("root") == build_item_url_from_path("root")


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------

FILE_ITEM = {
    "id": "01FILEID",
    "name": "foo.pdf",
    "size": 12345,
    "lastModifiedDateTime": "2024-01-01T00:00:00Z",
    "createdDateTime": "2023-12-01T00:00:00Z",
    "webUrl": "https://onedrive.live.com/foo.pdf",
    "file": {"mimeType": "application/pdf"},
    "parentReference": {"path": "/drive/root:/Documents"},
    "@microsoft.graph.downloadUrl": "https://download.example/foo.pdf",
}

FOLDER_ITEM = {
    "id": "01FOLDERID",
    "name": "Documents",
    "lastModifiedDateTime": "2024-01-01T00:00:00Z",
    "webUrl": "https://onedrive.live.com/Documents",
    "folder": {"childCount": 3},
    "parentReference": {"path": "/drive/root:"},
}

ROOT_ITEM = {
    "id": "01ROOTID",
    "name": "root",
    "folder": {"childCount": 5},
    "parentReference": {"path": ""},
    "webUrl": "https://onedrive.live.com/",
}


def test_shape_drive_item_file():
    shaped = shape_drive_item(FILE_ITEM)
    assert shaped == {
        "name": "foo.pdf",
        "id": "01FILEID",
        "path": "/Documents/foo.pdf",
        "size": 12345,
        "last_modified": "2024-01-01T00:00:00Z",
        "is_folder": False,
        "web_url": "https://onedrive.live.com/foo.pdf",
    }


def test_shape_drive_item_folder_is_folder_true():
    shaped = shape_drive_item(FOLDER_ITEM)
    assert shaped["is_folder"] is True
    assert shaped["path"] == "/Documents"


def test_shape_drive_item_root_path():
    shaped = shape_drive_item(ROOT_ITEM)
    assert shaped["path"] == "/root"  # root's own "name" is "root"


def test_shape_drive_item_full_includes_extra_fields():
    shaped = shape_drive_item_full(FILE_ITEM)
    assert shaped["mime_type"] == "application/pdf"
    assert shaped["created"] == "2023-12-01T00:00:00Z"
    assert shaped["child_count"] is None
    # N-M2 regression: the pre-authenticated Graph downloadUrl is a
    # no-auth, file-bearing URL — it must NEVER appear in tool output.
    assert "download_url" not in shaped
    assert "@microsoft.graph.downloadUrl" not in shaped


def test_shape_drive_item_full_folder_child_count():
    shaped = shape_drive_item_full(FOLDER_ITEM)
    assert shaped["child_count"] == 3
    assert shaped["is_folder"] is True


# ---------------------------------------------------------------------------
# High-level ops against a mocked Graph API (no real network)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _mock_token(monkeypatch):
    monkeypatch.setattr(graph_client, "get_access_token", lambda: "fake-token")


@responses_lib.activate
def test_search_items_shapes_results():
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive/root/search(q='report')",
        json={"value": [FILE_ITEM, FOLDER_ITEM]},
        status=200,
    )
    results = graph_client.search_items("report", top=20)
    assert len(results) == 2
    assert results[0]["name"] == "foo.pdf"
    assert results[1]["is_folder"] is True


@responses_lib.activate
def test_get_drive_info_shapes_quota():
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive",
        json={
            "id": "drive1",
            "driveType": "personal",
            "owner": {"user": {"displayName": "Jane Doe"}},
            "quota": {"used": 100, "total": 1000, "remaining": 900},
            "webUrl": "https://onedrive.live.com/",
        },
        status=200,
    )
    info = graph_client.get_drive_info()
    assert info["owner_name"] == "Jane Doe"
    assert info["quota_remaining"] == 900


@responses_lib.activate
def test_get_item_metadata_not_found_raises_clean_error():
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive/root:/missing.pdf",
        json={"error": {"message": "not found"}},
        status=404,
    )
    with pytest.raises(graph_client.GraphNotFoundError):
        graph_client.get_item_metadata("/missing.pdf")


@responses_lib.activate
def test_get_item_metadata_never_exposes_download_url():
    """N-M2: even when Graph returns @microsoft.graph.downloadUrl, the
    tool-visible metadata must not contain it (possession == bearer)."""
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive/root:/foo.pdf",
        json=dict(FILE_ITEM, **{"@microsoft.graph.downloadUrl": "https://cdn.example/secret-tokenized-url"}),
        status=200,
    )
    shaped = graph_client.get_item_metadata("/foo.pdf")
    flat = str(shaped)
    assert "download_url" not in shaped
    assert "cdn.example" not in flat
    assert "secret-tokenized-url" not in flat


@responses_lib.activate
def test_throttling_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(graph_client.time, "sleep", lambda _s: None)  # skip real sleeping
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive",
        json={"error": {"message": "throttled"}},
        status=429,
        headers={"Retry-After": "0"},
    )
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive",
        json={"id": "drive1", "quota": {}},
        status=200,
    )
    info = graph_client.get_drive_info()
    assert info["drive_id"] == "drive1"


@responses_lib.activate
def test_forbidden_raises_clean_permission_error():
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive",
        json={"error": {"message": "forbidden"}},
        status=403,
    )
    with pytest.raises(graph_client.GraphError, match="Files.Read"):
        graph_client.get_drive_info()


# ---------------------------------------------------------------------------
# download_file: permissions hardening + size-mismatch detection
# ---------------------------------------------------------------------------

@responses_lib.activate
def test_download_file_chmods_dir_and_file_owner_only(tmp_path):
    import stat as stat_mod

    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive/root:/foo.pdf",
        json=dict(FILE_ITEM, size=5, **{"@microsoft.graph.downloadUrl": None}),
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive/root:/foo.pdf/content",
        body=b"hello",
        status=200,
    )
    download_dir = tmp_path / "downloads"
    result = graph_client.download_file("/foo.pdf", download_dir)

    dir_mode = stat_mod.S_IMODE(download_dir.stat().st_mode)
    file_mode = stat_mod.S_IMODE(Path(result["local_path"]).stat().st_mode)
    assert dir_mode == stat_mod.S_IRWXU  # 0o700, owner only
    assert file_mode == (stat_mod.S_IRUSR | stat_mod.S_IWUSR)  # 0o600, owner only
    assert result["size_bytes"] == 5


@responses_lib.activate
def test_download_file_raises_on_size_mismatch(tmp_path):
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive/root:/foo.pdf",
        json=dict(FILE_ITEM, size=9999, **{"@microsoft.graph.downloadUrl": None}),
        status=200,
    )
    responses_lib.add(
        responses_lib.GET,
        f"{GRAPH_BASE}/me/drive/root:/foo.pdf/content",
        body=b"short",
        status=200,
    )
    download_dir = tmp_path / "downloads"
    with pytest.raises(graph_client.GraphError, match="size mismatch"):
        graph_client.download_file("/foo.pdf", download_dir)
