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

import sys
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from auth import TokenSource
from graph_client import MAX_PAGE_SIZE, GraphError, GraphNotFoundError, OneDriveClient
from settings import ConfigError, load_settings

PageSize = Annotated[int, Field(ge=1, le=MAX_PAGE_SIZE, strict=True)]

_NOT_READY = "The OneDrive MCP server is not configured. Start it with `onedrive-mcp`."


def _friendly_error(exc: Exception) -> dict:
    """Never leak a raw stack trace to the calling agent — always a clean,
    actionable message."""
    return {"error": str(exc)}


def build_server(client: Optional[OneDriveClient]) -> FastMCP:
    """Create the MCP server whose five read-only tools use ``client``.

    Tools are always registered so clients can discover them. With
    ``client=None`` every tool returns a friendly error instead of failing.
    """
    mcp = FastMCP("onedrive-readonly")

    def run(operation) -> dict:
        if client is None:
            return {"error": _NOT_READY}
        try:
            return operation(client)
        except (GraphError, GraphNotFoundError, ConfigError) as exc:
            return _friendly_error(exc)

    @mcp.tool()
    def search_onedrive(query: str, top: PageSize = 20, continuation: Optional[str] = None) -> dict:
        """Search OneDrive for files/folders by name or content match.

        Args:
            query: Search text (Graph full-text search over file/folder names and content).
            top: Page size, between 1 and 200 (default 20).
            continuation: Continuation returned by this tool; keep query and top unchanged.

        Returns {query, count, items, continuation, has_more}. Repeat with continuation
        until has_more is false; count is the number of items in this page only.
        """
        def operation(c: OneDriveClient) -> dict:
            page = c.search_items(query, top=top, continuation=continuation)
            return {"query": query, "count": len(page["items"]), **page}

        return run(operation)


    @mcp.tool()
    def list_folder(path: str = "/", top: PageSize = 50, continuation: Optional[str] = None) -> dict:
        """List the contents of a OneDrive folder.

        Args:
            path: Folder path (e.g. '/Documents') or 'root'/'/' for the drive root.
                  A Graph item id is also accepted.
            top: Page size, between 1 and 200 (default 50).
            continuation: Continuation returned by this tool; keep path and top unchanged.

        Returns {items: [...], continuation, has_more}. Repeat with continuation until
        has_more is false. Opaque continuations are bound to this folder and page size,
        and expire after one hour or a server restart.
        """
        return run(lambda c: c.list_folder(path, top=top, continuation=continuation))


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
        return run(lambda c: c.get_item_metadata(path_or_id))


    @mcp.tool()
    def download_file(path_or_id: str, dest_filename: Optional[str] = None) -> dict:
        """Download a OneDrive file's content to local disk (read-only — the
        source file in OneDrive is never modified).

        Args:
            path_or_id: Human path (e.g. '/Documents/foo.pdf') or Graph item id
                        of the FILE to download.
            dest_filename: Optional bare local filename to save as (defaults to
                           the OneDrive file's own name). Saved under DOWNLOAD_DIR.
                           Must be a plain file name (no path separators) and must
                           not already exist — an existing destination raises an
                           error instead of being silently overwritten.

        Returns {local_path, size_bytes, mime_type, source_name}. The file is
        always saved to disk (owner-only permissions); base64 content is not
        returned to keep responses small and to safely handle large files.
        """
        return run(lambda c: c.download_file(path_or_id, dest_filename))


    @mcp.tool()
    def get_drive_info() -> dict:
        """Get basic OneDrive info (quota, owner, drive id) — useful as a smoke
        test that authentication is working.

        Returns {drive_id, drive_type, owner_name, quota_used, quota_total,
        quota_remaining, web_url}.
        """
        return run(lambda c: c.get_drive_info())

    return mcp


def main() -> None:
    """Build settings once, then run the MCP server over stdio.

    The server may start without AZURE_CLIENT_ID so MCP clients can discover
    tools; tool calls then return a friendly error before any network access
    or persistent write.
    """
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"OneDrive MCP configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    client = OneDriveClient(settings, TokenSource(settings))
    build_server(client).run(transport="stdio")


if __name__ == "__main__":
    main()
