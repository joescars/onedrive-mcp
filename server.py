"""Read-only OneDrive MCP server (Microsoft Graph, personal Microsoft account).

Run standalone for manual testing:
    python server.py

Registered with Hermes / Claude-style MCP hosts via stdio transport (see
README.md for the exact config snippet). Can also be bridged to an HTTP
OpenAPI server for Open WebUI via `mcpo` (see README.md).

This server exposes exactly 5 read-only tools. It never calls anything but
HTTP GET against Microsoft Graph (enforced in graph_client.py).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

import graph_client
from graph_client import GraphError, GraphNotFoundError

load_dotenv(Path(__file__).resolve().parent / ".env")

mcp = FastMCP("onedrive-readonly")


def _download_dir() -> Path:
    raw = os.environ.get("DOWNLOAD_DIR", "./downloads")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        # Resolve relative to this file's directory, not the launching
        # process's cwd (Hermes/other MCP hosts often launch with an
        # unrelated cwd — see auth.py's PROJECT_ROOT for the same fix).
        path = Path(__file__).resolve().parent / path
    return path.resolve()


def _friendly_error(exc: Exception) -> dict:
    """Never leak a raw stack trace to the calling agent — always a clean,
    actionable message."""
    return {"error": str(exc)}


@mcp.tool()
def search_onedrive(query: str, top: int = 20) -> dict:
    """Search OneDrive for files/folders by name or content match.

    Args:
        query: Search text (Graph full-text search over file/folder names and content).
        top: Max number of results to return (default 20).

    Returns a list of items: {name, id, path, size, last_modified, is_folder, web_url}.
    """
    try:
        results = graph_client.search_items(query, top=top)
        return {"query": query, "count": len(results), "items": results}
    except (GraphError, GraphNotFoundError) as exc:
        return _friendly_error(exc)


@mcp.tool()
def list_folder(path: str = "/", top: int = 50) -> dict:
    """List the contents of a OneDrive folder.

    Args:
        path: Folder path (e.g. '/Documents') or 'root'/'/' for the drive root.
              A Graph item id is also accepted.
        top: Max children to return per page (default 50).

    Returns {items: [...], next_link}. next_link is set when there are more
    results than 'top' (basic paging support — pass the returned items and
    note next_link is a raw Graph URL for reference, not directly callable
    by this tool today).
    """
    try:
        return graph_client.list_folder(path, top=top)
    except (GraphError, GraphNotFoundError) as exc:
        return _friendly_error(exc)


@mcp.tool()
def get_item_metadata(path_or_id: str) -> dict:
    """Get full metadata for a single OneDrive file or folder.

    Args:
        path_or_id: Either a human path (e.g. '/Documents/foo.pdf') or a raw
                    Graph drive item id.

    Returns {name, id, path, size, last_modified, is_folder, web_url,
    created, mime_type, child_count}. The pre-authenticated Graph
    downloadUrl is intentionally NOT returned (see SECURITY_REVIEW N-M2) —
    use download_file() to fetch content.
    """
    try:
        return graph_client.get_item_metadata(path_or_id)
    except (GraphError, GraphNotFoundError) as exc:
        return _friendly_error(exc)


@mcp.tool()
def download_file(path_or_id: str, dest_filename: Optional[str] = None) -> dict:
    """Download a OneDrive file's content to local disk (read-only — the
    source file in OneDrive is never modified).

    Args:
        path_or_id: Human path (e.g. '/Documents/foo.pdf') or Graph item id
                    of the FILE to download.
        dest_filename: Optional local filename to save as (defaults to the
                       OneDrive file's own name). Saved under DOWNLOAD_DIR.

    Returns {local_path, size_bytes, mime_type, source_name}. The file is
    always saved to disk; base64 content is not returned to keep responses
    small and to safely handle large files.
    """
    try:
        return graph_client.download_file(path_or_id, _download_dir(), dest_filename)
    except (GraphError, GraphNotFoundError) as exc:
        return _friendly_error(exc)


@mcp.tool()
def get_drive_info() -> dict:
    """Get basic OneDrive info (quota, owner, drive id) — useful as a smoke
    test that authentication is working.

    Returns {drive_id, drive_type, owner_name, quota_used, quota_total,
    quota_remaining, web_url}.
    """
    try:
        return graph_client.get_drive_info()
    except (GraphError, GraphNotFoundError) as exc:
        return _friendly_error(exc)


if __name__ == "__main__":
    mcp.run(transport="stdio")
