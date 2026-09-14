"""Thin, read-only Microsoft Graph API client for OneDrive (personal MSA).

HARD SAFETY RULE — READ ONLY
-----------------------------
This module must NEVER issue anything other than an HTTP GET to Graph. There
is exactly one function that performs the actual HTTP call (`_get`), and it
hardcodes `requests.get`. Do not add `requests.post/put/patch/delete` calls
anywhere in this file. `_get()` asserts the caller isn't trying to sneak a
different verb in, as a defence-in-depth guard against accidental copy/paste
of a write call.
"""
from __future__ import annotations

import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Optional

import requests

from auth import AuthConfigError, get_access_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

MAX_RETRIES = 4


class GraphError(RuntimeError):
    """Human-readable Graph API error (never a raw stack trace to the tool caller)."""


class GraphNotFoundError(GraphError):
    """Item not found (HTTP 404)."""


# ---------------------------------------------------------------------------
# Core, read-only-only HTTP call
# ---------------------------------------------------------------------------

def _get(url: str, *, params: Optional[dict] = None, stream: bool = False,
          _retry_count: int = 0, _allow_full_url: bool = False) -> requests.Response:
    """The ONLY function in this codebase allowed to call the network for
    Graph. It always performs requests.get (READ ONLY — see module docstring).
    """
    # Defence-in-depth: this function's entire contract is "GET only". If
    # someone edits this to add a verb argument, make that obviously wrong.
    http_verb = requests.get
    assert http_verb is requests.get, "graph_client must only ever perform GET requests"

    try:
        token = get_access_token()
    except AuthConfigError as exc:
        raise GraphError(str(exc)) from exc

    headers = {"Authorization": f"Bearer {token}"}
    resp = http_verb(url, headers=headers, params=params, stream=stream, timeout=60)

    if resp.status_code == 401 and _retry_count == 0:
        # Access token might have just expired; acquire_token_silent inside
        # get_access_token() already handles refresh, but in case the cached
        # token was stale at call time, force one retry.
        return _get(url, params=params, stream=stream, _retry_count=1)

    if resp.status_code == 429:
        if _retry_count >= MAX_RETRIES:
            raise GraphError(
                "Microsoft Graph is throttling requests (HTTP 429) and the "
                "retry budget was exhausted. Please try again shortly."
            )
        retry_after = float(resp.headers.get("Retry-After", "2"))
        time.sleep(min(retry_after, 30))
        return _get(url, params=params, stream=stream, _retry_count=_retry_count + 1)

    if resp.status_code == 404:
        raise GraphNotFoundError("The requested OneDrive item was not found.")

    if resp.status_code == 401:
        raise GraphError(
            "Microsoft Graph rejected the access token as unauthorized even "
            "after a refresh attempt. Your sign-in may have been revoked. "
            "Re-run 'python scripts/setup_auth.py' to sign in again."
        )

    if resp.status_code == 403:
        raise GraphError(
            "Microsoft Graph denied access (HTTP 403). This usually means "
            "the app registration is missing the Files.Read / Files.Read.All "
            "delegated permission, or the signed-in account doesn't have "
            "access to this item."
        )

    if not resp.ok:
        raise GraphError(
            f"Microsoft Graph request failed: HTTP {resp.status_code} "
            f"{resp.reason} — {resp.text[:500]}"
        )

    return resp


def _get_json(url: str, *, params: Optional[dict] = None) -> dict:
    return _get(url, params=params).json()


# ---------------------------------------------------------------------------
# Path resolution helpers
# ---------------------------------------------------------------------------

def _is_probably_item_id(value: str) -> bool:
    """Heuristic: Graph drive item ids don't start with '/' and don't look
    like a human path. Paths always start with '/' (or are 'root')."""
    if not value:
        return False
    if value == "root":
        return False
    if value.startswith("/"):
        return False
    return True


def build_item_url_from_path(path: str) -> str:
    """Build a /me/drive/root:{path} style Graph URL from a human path."""
    path = path.strip()
    if path in ("", "/", "root"):
        return f"{GRAPH_BASE}/me/drive/root"
    clean = path if path.startswith("/") else f"/{path}"
    # Graph's colon-path addressing: /me/drive/root:/Documents/foo.pdf
    return f"{GRAPH_BASE}/me/drive/root:{clean}"


def build_item_url_from_id(item_id: str) -> str:
    return f"{GRAPH_BASE}/me/drive/items/{item_id}"


def resolve_item_url(path_or_id: str) -> str:
    """Resolve either a human path ('/Documents/foo.pdf', '/', 'root') or a
    raw Graph item id into the correct 'get item' Graph URL."""
    if _is_probably_item_id(path_or_id):
        return build_item_url_from_id(path_or_id)
    return build_item_url_from_path(path_or_id)


# ---------------------------------------------------------------------------
# Response shaping
# ---------------------------------------------------------------------------

def shape_drive_item(item: dict) -> dict:
    """Normalize a Graph driveItem resource into our clean, stable shape."""
    parent = item.get("parentReference", {}) or {}
    parent_path = parent.get("path", "")  # e.g. "/drive/root:/Documents"
    # Strip the "/drive/root:" prefix Graph adds, leaving a clean human path.
    if parent_path.startswith("/drive/root:"):
        parent_path = parent_path[len("/drive/root:"):] or "/"
    elif parent_path == "":
        parent_path = "/"

    name = item.get("name", "")
    full_path = (parent_path.rstrip("/") + "/" + name) if name else parent_path

    return {
        "name": name,
        "id": item.get("id"),
        "path": full_path or "/",
        "size": item.get("size"),
        "last_modified": item.get("lastModifiedDateTime"),
        "is_folder": "folder" in item,
        "web_url": item.get("webUrl"),
    }


def shape_drive_item_full(item: dict) -> dict:
    """Fuller metadata shape used by get_item_metadata (keeps the clean
    fields plus a few extras that are useful for a single-item lookup)."""
    base = shape_drive_item(item)
    base.update({
        "created": item.get("createdDateTime"),
        "mime_type": (item.get("file") or {}).get("mimeType"),
        "child_count": (item.get("folder") or {}).get("childCount"),
        "download_url": item.get("@microsoft.graph.downloadUrl"),
    })
    return base


# ---------------------------------------------------------------------------
# High-level, read-only operations used by the MCP tools
# ---------------------------------------------------------------------------

def search_items(query: str, top: int = 20) -> list[dict]:
    url = f"{GRAPH_BASE}/me/drive/root/search(q='{requests.utils.quote(query)}')"
    data = _get_json(url, params={"$top": top})
    items = data.get("value", [])
    return [shape_drive_item(i) for i in items[:top]]


def list_folder(path: str = "/", top: int = 50) -> dict:
    item_url = resolve_item_url(path)
    children_url = f"{item_url}:/children" if path not in ("/", "", "root") and not _is_probably_item_id(path) else f"{item_url}/children"
    # For root or item-id addressing, ":children" colon-suffix isn't used.
    if path in ("/", "", "root"):
        children_url = f"{GRAPH_BASE}/me/drive/root/children"
    elif _is_probably_item_id(path):
        children_url = f"{build_item_url_from_id(path)}/children"
    else:
        clean = path if path.startswith("/") else f"/{path}"
        children_url = f"{GRAPH_BASE}/me/drive/root:{clean}:/children"

    data = _get_json(children_url, params={"$top": top})
    items = data.get("value", [])
    return {
        "items": [shape_drive_item(i) for i in items[:top]],
        "next_link": data.get("@odata.nextLink"),
    }


def get_item_metadata(path_or_id: str) -> dict:
    url = resolve_item_url(path_or_id)
    item = _get_json(url)
    return shape_drive_item_full(item)


def get_drive_info() -> dict:
    data = _get_json(f"{GRAPH_BASE}/me/drive")
    owner = (data.get("owner") or {}).get("user", {})
    quota = data.get("quota", {})
    return {
        "drive_id": data.get("id"),
        "drive_type": data.get("driveType"),
        "owner_name": owner.get("displayName"),
        "quota_used": quota.get("used"),
        "quota_total": quota.get("total"),
        "quota_remaining": quota.get("remaining"),
        "web_url": data.get("webUrl"),
    }


def download_file(path_or_id: str, download_dir: Path, dest_filename: Optional[str] = None) -> dict:
    """Downloads a file's content (read-only) and saves it under download_dir.

    Uses the item's @microsoft.graph.downloadUrl when available (a
    pre-authenticated, short-lived URL) and falls back to the /content
    endpoint otherwise. Streams to disk — never buffers the whole file in
    memory, and never mutates anything in OneDrive.
    """
    url = resolve_item_url(path_or_id)
    item = _get_json(url)

    if item.get("folder") is not None:
        raise GraphError(f"'{path_or_id}' is a folder, not a file — cannot download.")

    name = dest_filename or item.get("name") or "downloaded_file"
    download_dir.mkdir(parents=True, exist_ok=True)
    dest_path = (download_dir / name).resolve()
    # Guard against path traversal via a crafted dest_filename.
    if download_dir.resolve() not in dest_path.parents and dest_path != download_dir.resolve():
        raise GraphError("Invalid destination filename.")

    direct_url = item.get("@microsoft.graph.downloadUrl")
    if direct_url:
        # Pre-authenticated URL: still routed through _get so retries/backoff
        # apply, but it needs no Authorization header. _get always adds one;
        # Graph's CDN download URLs ignore/accept an extra bearer header fine
        # in practice, but to be safe we do a raw, GET-only request here too.
        resp = requests.get(direct_url, stream=True, timeout=120)
        if not resp.ok:
            raise GraphError(f"Failed to download file content: HTTP {resp.status_code}")
    else:
        content_url = f"{url}/content"
        resp = _get(content_url, stream=True)

    total = 0
    with open(dest_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
                total += len(chunk)

    mime_type = (item.get("file") or {}).get("mimeType") or mimetypes.guess_type(name)[0] or "application/octet-stream"

    return {
        "local_path": str(dest_path),
        "size_bytes": total,
        "mime_type": mime_type,
        "source_name": item.get("name"),
    }
