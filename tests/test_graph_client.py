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
    assert shaped["download_url"] == "https://download.example/foo.pdf"
    assert shaped["child_count"] is None


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
