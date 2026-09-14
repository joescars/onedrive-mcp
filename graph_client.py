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

import email.utils
import mimetypes
import os
import stat
import time
from datetime import datetime, timezone
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


def _parse_retry_after(value: Optional[str]) -> float:
    """Parse a Retry-After header, which RFC 9110 allows as either delta
    seconds or an HTTP-date (Graph sends the date form). Clamped to [0, 30].
    Any unparseable value falls back to 2s — never crashes the 429 handler.
    """
    if not value:
        return 2.0
    value = value.strip()
    try:
        seconds = float(value)
    except ValueError:
        # HTTP-date form, e.g. "Wed, 21 Oct 2015 07:28:00 GMT"
        try:
            then = email.utils.parsedate_to_datetime(value)
            seconds = (then.replace(tzinfo=timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError):
            return 2.0
    return max(0.0, min(seconds, 30.0))


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
        time.sleep(_parse_retry_after(resp.headers.get("Retry-After")))
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


def _quote_graph_path(path: str) -> str:
    """Percent-encode a Graph drive path for use in colon-path addressing,
    keeping '/' (and ':') as they are valid path separators, while encoding
    reserved URL characters (?, #, %, ...) and rejecting dot-segments.

    SECURITY (N-L4): without this, raw path text is interpolated into the
    URL. A '?' or '#' shifts the URL to query/fragment, and a '..' segment
    is collapsed by the client (requests) into a different Graph endpoint
    (e.g. path_or_id='../../users' normalizes from /me/drive/items/... to
    /v1.0/me/users). Encoding blocks both: '?'/'#' become %-escaped, and a
    ..-segment anywhere in the path raises instead of escaping the drive
    addressing.
    """
    if path in ("", "/", "root"):
        return path
    segments = path.split("/")
    out: list[str] = []
    for seg in segments:
        if seg in (".", ".."):
            raise GraphError(
                "Invalid OneDrive path: dot-segment traversal is not allowed "
                f"(got {seg!r})."
            )
        out.append(requests.utils.quote(seg, safe=":"))
    return "/".join(out)


def _quote_graph_id(item_id: str) -> str:
    """Percent-encode a raw Graph item id, so URL-reserved characters in a
    caller-supplied id ('/', '?', '#', '..') cannot change which endpoint
    is hit (N-L4). Real ids (e.g. '977892149CC23DE6!s8a...') pass through
    unchanged; encoded dots/slashes have no dot-segment meaning on the wire.
    """
    return requests.utils.quote(item_id, safe="")


def build_item_url_from_path(path: str) -> str:
    """Build a /me/drive/root:{path} style Graph URL from a human path."""
    path = path.strip()
    if path in ("", "/", "root"):
        return f"{GRAPH_BASE}/me/drive/root"
    clean = path if path.startswith("/") else f"/{path}"
    # Graph's colon-path addressing: /me/drive/root:/Documents/foo.pdf
    return f"{GRAPH_BASE}/me/drive/root:{_quote_graph_path(clean)}"


def build_item_url_from_id(item_id: str) -> str:
    return f"{GRAPH_BASE}/me/drive/items/{_quote_graph_id(item_id)}"


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
    fields plus a few extras that are useful for a single-item lookup).

    SECURITY: deliberately does NOT include @microsoft.graph.downloadUrl.
    That URL is a short-lived, no-authentication-required link — possession
    of it is equivalent to holding a file-scoped bearer token — and tool
    results flow into agent transcripts/logs. download_file() reads it from
    the raw Graph response internally and never exposes it. (SECURITY_REVIEW
    finding N-M2.)
    """
    base = shape_drive_item(item)
    base.update({
        "created": item.get("createdDateTime"),
        "mime_type": (item.get("file") or {}).get("mimeType"),
        "child_count": (item.get("folder") or {}).get("childCount"),
    })
    return base


# ---------------------------------------------------------------------------
# High-level, read-only operations used by the MCP tools
# ---------------------------------------------------------------------------

def search_items(query: str, top: int = 20) -> list[dict]:
    # safe="" percent-encodes '/' and '?' inside the OData query so a
    # crafted search term cannot shift the search(q='...') endpoint or
    # become a query string (N-L4).
    url = f"{GRAPH_BASE}/me/drive/root/search(q='{requests.utils.quote(query, safe='')}')"
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
        children_url = f"{GRAPH_BASE}/me/drive/root:{_quote_graph_path(clean)}:/children"

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


def _valid_dest_name(name: str) -> bool:
    """True only for a bare, non-empty file name that stays inside the
    download directory (N-L2): no path separators, no dot-segments."""
    name = (name or "").strip()
    return bool(name) and "/" not in name and "\\" not in name and name not in (".", "..")


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

    # N-L2: an empty name would resolve to the download directory itself
    # (passing the old guard's exception clause and then failing with
    # IsADirectoryError), and sub-paths like "sub/dir.pdf" would need
    # directories the code never creates. Reject both up front — and
    # reject an explicitly-provided empty/odd dest_filename even though it
    # would otherwise fall through to the item's real name.
    if dest_filename is not None and not _valid_dest_name(dest_filename):
        raise GraphError(
            f"Invalid destination filename {dest_filename!r}: must be a bare "
            "file name with no path separators."
        )
    name = (dest_filename or item.get("name") or "downloaded_file").strip()
    if not _valid_dest_name(name):
        raise GraphError(
            f"Invalid destination filename {name!r}: must be a bare file name "
            "with no path separators."
        )

    download_dir.mkdir(parents=True, exist_ok=True)
    # Restrict the downloads directory to the owner only — downloaded
    # content can include personal documents and should not inherit the
    # process umask (which may leave it group/world-readable on a shared
    # host). Best-effort: don't fail the download if chmod isn't permitted.
    try:
        os.chmod(download_dir, stat.S_IRWXU)
    except OSError:
        pass
    dest_path = (download_dir / name).resolve()
    # Guard against path traversal via a crafted dest_filename (N-L2/L2
    # hardening): the resolved destination must live strictly inside the
    # download directory — not an escape, not the directory itself.
    if download_dir.resolve() not in dest_path.parents:
        raise GraphError("Invalid destination filename.")
    # Refuse to silently clobber an existing file (N-L2) — otherwise a
    # re-download (or a crafted name colliding with a prior document) would
    # overwrite real personal data without warning.
    if dest_path.exists():
        raise GraphError(
            f"A file named '{name}' already exists in {download_dir}. "
            "Choose a different dest_filename to avoid overwriting it."
        )

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
    # N-L1: create with owner-only mode from the start, so the file is never
    # group/world-readable during the stream (previously open() created it at
    # 0664-umask until the chmod after the transfer finished).
    fd = os.open(dest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "wb") as f:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                f.write(chunk)
                total += len(chunk)

    # Best-effort: guarantee owner-only even if the FS ignored the mode.
    try:
        os.chmod(dest_path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    expected_size = item.get("size")
    if expected_size is not None and total != expected_size:
        raise GraphError(
            f"Download size mismatch for '{name}': expected {expected_size} "
            f"bytes from OneDrive metadata but wrote {total} bytes. The file "
            "may be truncated or corrupted — it was still saved to "
            f"{dest_path}, but re-download before trusting its contents."
        )

    mime_type = (item.get("file") or {}).get("mimeType") or mimetypes.guess_type(name)[0] or "application/octet-stream"

    return {
        "local_path": str(dest_path),
        "size_bytes": total,
        "mime_type": mime_type,
        "source_name": item.get("name"),
    }
