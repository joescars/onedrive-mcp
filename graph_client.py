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

import base64
import email.utils
import hashlib
import hmac
import json
import math
import mimetypes
import os
import secrets
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import requests
from filelock import FileLock, Timeout

from auth import AuthConfigError, get_access_token

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

MAX_RETRIES = 4
MAX_RETRY_WAIT = 60.0
MAX_PAGE_SIZE = 200
_PAGE_KEY = secrets.token_bytes(32)


class GraphError(RuntimeError):
    """Human-readable Graph API error (never a raw stack trace to the tool caller)."""


class GraphNotFoundError(GraphError):
    """Item not found (HTTP 404)."""


def _parse_retry_after(value: Optional[str]) -> float:
    """Parse a Retry-After header, which RFC 9110 allows as either delta
    seconds or an HTTP-date. Never shorten a valid server-requested delay.
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
            if then.tzinfo is None:
                then = then.replace(tzinfo=timezone.utc)
            seconds = (then - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 2.0
    return max(0.0, seconds) if math.isfinite(seconds) else 2.0


# ---------------------------------------------------------------------------
# Core, read-only-only HTTP call
# ---------------------------------------------------------------------------

def _get(url: str, *, params: Optional[dict] = None, stream: bool = False) -> requests.Response:
    """The ONLY function in this codebase allowed to call the network for
    Graph. It always performs requests.get (READ ONLY — see module docstring).
    """
    # Defence-in-depth: this function's entire contract is "GET only". If
    # someone edits this to add a verb argument, make that obviously wrong.
    http_verb = requests.get
    assert http_verb is requests.get, "graph_client must only ever perform GET requests"

    refreshed = False
    force_refresh = False
    retries = 0
    waited = 0.0
    while True:
        try:
            token = get_access_token(force_refresh=force_refresh)
            force_refresh = False
            headers = {"Authorization": f"Bearer {token}"}
            resp = http_verb(url, headers=headers, params=params, stream=stream, timeout=60)
        except AuthConfigError as exc:
            raise GraphError(str(exc)) from None
        except requests.exceptions.RequestException:
            raise GraphError("Microsoft Graph connection failed. Please retry later.") from None

        if resp.ok:
            return resp
        status = resp.status_code
        retry_after = resp.headers.get("Retry-After")
        resp.close()
        if status == 401 and not refreshed:
            refreshed = True
            force_refresh = True
            continue
        if status == 429:
            delay = _parse_retry_after(retry_after) if retry_after else 2.0 ** (retries + 1)
            if retries >= MAX_RETRIES or waited + delay > MAX_RETRY_WAIT:
                raise GraphError(
                    f"Microsoft Graph is throttling requests (HTTP 429). "
                    f"Retry later, after at least {delay:g} seconds."
                )
            time.sleep(delay)
            waited += delay
            retries += 1
            continue
        if status == 404:
            raise GraphNotFoundError("The requested OneDrive item was not found.")
        if status == 401:
            raise GraphError(
                "Microsoft Graph rejected the access token after a refresh attempt. "
                "Re-run 'python scripts/setup_auth.py' to sign in again."
            )
        if status == 403:
            raise GraphError(
                "Microsoft Graph denied access (HTTP 403). Check Files.Read / "
                "Files.Read.All delegated permissions and access to this item."
            )
        raise GraphError(f"Microsoft Graph request failed: HTTP {status}. Please retry later.")


def _get_json(url: str, *, params: Optional[dict] = None) -> dict:
    try:
        with _get(url, params=params) as response:
            result = response.json()
    except (requests.exceptions.RequestException, ValueError):
        raise GraphError("Microsoft Graph returned an unreadable response. Please retry later.") from None
    if not isinstance(result, dict):
        raise GraphError("Microsoft Graph returned an unexpected response.")
    return result


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
    if item_id in (".", ".."):
        raise GraphError("Invalid OneDrive item id: dot-segment traversal is not allowed.")
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

def _encode_next_link(url: str, endpoint: str, top: int) -> str:
    # Graph may canonicalize /me/drive/root to /drives/{id}/items/{id}.
    # Sign the returned URL instead of trusting a caller-supplied raw URL.
    try:
        candidate = urlsplit(url)
        if (
            candidate.scheme != "https"
            or candidate.netloc != "graph.microsoft.com"
            or not candidate.path.startswith(("/v1.0/me/drive/", "/v1.0/drives/"))
            or candidate.fragment
            or any(ord(c) < 33 for c in url)
            or len(url) > 16000
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise GraphError("Microsoft Graph returned an invalid continuation link.") from None
    payload = base64.urlsafe_b64encode(json.dumps({
        "url": url, "endpoint": endpoint, "top": top, "expires": time.time() + 3600,
    }).encode()).decode()
    signature = hmac.new(_PAGE_KEY, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def _decode_next_link(next_link: str, endpoint: str, top: int) -> str:
    try:
        if not isinstance(next_link, str) or len(next_link) > 32768:
            raise ValueError
        payload, signature = next_link.split(".")
        expected = hmac.new(_PAGE_KEY, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(payload))
        if (
            data["endpoint"] != endpoint or data["top"] != top
            or data["expires"] < time.time()
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise GraphError(
            "Invalid next_link. Use the continuation returned by the same tool "
            "with the same query/path and top. Restart paging if it expired "
            "or the server restarted."
        ) from None
    return data["url"]


def _get_page(endpoint: str, top: int, next_link: Optional[str]) -> dict:
    if type(top) is not int or not 1 <= top <= MAX_PAGE_SIZE:
        raise GraphError(f"top must be an integer between 1 and {MAX_PAGE_SIZE}.")
    url = _decode_next_link(next_link, endpoint, top) if next_link is not None else endpoint
    data = _get_json(url, params=None if next_link is not None else {"$top": top})
    items = data.get("value")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise GraphError("Microsoft Graph returned an invalid item page.")
    if len(items) > top:
        raise GraphError("Microsoft Graph exceeded the requested page size; no items were silently discarded.")
    continuation = data.get("@odata.nextLink")
    if continuation is not None:
        if not isinstance(continuation, str):
            raise GraphError("Microsoft Graph returned an invalid continuation link.")
        continuation = _encode_next_link(continuation, endpoint, top)
    return {
        "items": [shape_drive_item(item) for item in items],
        "next_link": continuation,
        "has_more": continuation is not None,
    }


def search_items(query: str, top: int = 20, next_link: Optional[str] = None) -> dict:
    literal = requests.utils.quote(query.replace("'", "''"), safe="")
    url = f"{GRAPH_BASE}/me/drive/root/search(q='{literal}')"
    return _get_page(url, top, next_link)


def list_folder(path: str = "/", top: int = 50, next_link: Optional[str] = None) -> dict:
    item_url = resolve_item_url(path)
    if path in ("/", "", "root"):
        children_url = f"{GRAPH_BASE}/me/drive/root/children"
    elif _is_probably_item_id(path):
        children_url = f"{item_url}/children"
    else:
        children_url = f"{item_url}:/children"
    return _get_page(children_url, top, next_link)


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
    return (
        bool(name) and "/" not in name and "\\" not in name and ":" not in name
        and name not in (".", "..", ".download.lock")
        and not name.startswith(".onedrive-part-")
        and all(ord(c) >= 32 for c in name)
    )


def _positive_limit(variable: str, default: int) -> int:
    try:
        value = int(os.environ.get(variable, str(default)))
        if value > 0:
            return value
    except ValueError:
        raise GraphError(f"{variable} must be a positive integer number of bytes.") from None
    raise GraphError(f"{variable} must be a positive integer number of bytes.")


def _directory_size(directory: Path) -> int:
    def fail_scan(error: OSError) -> None:
        raise error

    total = 0
    for root, _, filenames in os.walk(directory, onerror=fail_scan, followlinks=False):
        for filename in filenames:
            path = Path(root) / filename
            if not path.is_symlink():
                total += path.stat().st_size
    return total


def download_file(path_or_id: str, download_dir: Path, dest_filename: Optional[str] = None) -> dict:
    """Download under a directory-wide lock so concurrent requests share the storage budget."""
    try:
        download_dir = download_dir.resolve()
        download_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(download_dir, stat.S_IRWXU)
        with FileLock(download_dir / ".download.lock", timeout=10, mode=0o600):
            return _download_file(path_or_id, download_dir, dest_filename)
    except requests.exceptions.RequestException:
        # Requests exception text can contain signed URLs, including during iter_content.
        raise GraphError("File transfer failed. No completed download was saved; please retry.") from None
    except Timeout:
        raise GraphError("Download directory is busy. Wait for the current transfer and retry.") from None
    except OSError:
        raise GraphError(
            "Could not save the download. Check directory permissions, filesystem "
            "hard-link support, and available disk space."
        ) from None


def _download_file(path_or_id: str, download_dir: Path, dest_filename: Optional[str]) -> dict:
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

    dest_path = download_dir / name
    # Guard against path traversal via a crafted dest_filename (N-L2/L2
    # hardening): the resolved destination must live strictly inside the
    # download directory — not an escape, not the directory itself.
    if dest_path.resolve().parent != download_dir:
        raise GraphError("Invalid destination filename.")
    # Refuse to silently clobber an existing file (N-L2) — otherwise a
    # re-download (or a crafted name colliding with a prior document) would
    # overwrite real personal data without warning.
    if dest_path.exists() or dest_path.is_symlink():
        raise GraphError(
            f"A file named '{name}' already exists in {download_dir}. "
            "Choose a different dest_filename to avoid overwriting it."
        )

    file_limit = _positive_limit("MAX_DOWNLOAD_BYTES", 100 * 1024 * 1024)
    directory_limit = _positive_limit("MAX_DOWNLOAD_DIR_BYTES", 1024 * 1024 * 1024)
    used = _directory_size(download_dir)
    budget = min(file_limit, directory_limit - used)
    expected_size = item.get("size")
    if expected_size is not None and (type(expected_size) is not int or expected_size < 0):
        raise GraphError("Microsoft Graph returned an invalid file size.")
    if budget < 0 or (expected_size is not None and expected_size > budget):
        raise GraphError("Download exceeds MAX_DOWNLOAD_BYTES or MAX_DOWNLOAD_DIR_BYTES. Free space or adjust the limits.")

    direct_url = item.get("@microsoft.graph.downloadUrl")
    if direct_url:
        if not isinstance(direct_url, str) or urlsplit(direct_url).scheme != "https":
            raise GraphError("Microsoft Graph returned an invalid download URL.")
        resp = requests.get(direct_url, stream=True, timeout=120)
    else:
        content_suffix = ":/content" if url.startswith(f"{GRAPH_BASE}/me/drive/root:") else "/content"
        resp = _get(f"{url}{content_suffix}", stream=True)

    total = 0
    with resp:
        if not resp.ok:
            raise GraphError(f"Failed to download file content: HTTP {resp.status_code}")
        fd, temporary = tempfile.mkstemp(prefix=".onedrive-part-", dir=download_dir)
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        total += len(chunk)
                        if total > budget:
                            raise GraphError("Download exceeded the configured size/storage limit; partial data was removed.")
                        f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            if expected_size is not None and total != expected_size:
                raise GraphError(f"Download size mismatch for '{name}'; partial data was removed. Please retry.")
            # Linking is atomic and refuses existing targets, unlike replace/rename.
            try:
                os.link(temporary, dest_path)
            except FileExistsError:
                raise GraphError("Destination already exists. Choose a different dest_filename.") from None
        finally:
            Path(temporary).unlink(missing_ok=True)

    mime_type = (item.get("file") or {}).get("mimeType") or mimetypes.guess_type(name)[0] or "application/octet-stream"

    return {
        "local_path": str(dest_path),
        "size_bytes": total,
        "mime_type": mime_type,
        "source_name": item.get("name"),
    }
