# Tool reference

[Back to README](../README.md)

The server exposes exactly five read-only MCP tools. Errors return a clean
`{"error": "..."}` object rather than a raw traceback.

## `search_onedrive`

```text
search_onedrive(query, top=20, next_link=None)
```

Searches file and folder names and indexed content across the drive.

| Argument | Description |
|---|---|
| `query` | Search text |
| `top` | Page size from 1 to 200 |
| `next_link` | Opaque continuation from the preceding response |

Returns:

```text
{query, count, items, next_link, has_more}
```

`count` is the number of items in the current page, not a drive-wide total.

## `list_folder`

```text
list_folder(path="/", top=50, next_link=None)
```

Lists the immediate children of a folder.

| Argument | Description |
|---|---|
| `path` | Human path, `root`, `/`, or a Graph item ID |
| `top` | Page size from 1 to 200 |
| `next_link` | Opaque continuation from the preceding response |

Returns:

```text
{items, next_link, has_more}
```

## `get_item_metadata`

```text
get_item_metadata(path_or_id)
```

Returns full metadata for one file or folder:

```text
{
  name,
  id,
  path,
  size,
  last_modified,
  is_folder,
  web_url,
  created,
  mime_type,
  child_count
}
```

The pre-authenticated Microsoft Graph download URL is deliberately excluded.
Use `download_file` to retrieve content.

## `download_file`

```text
download_file(path_or_id, dest_filename=None)
```

Downloads a file from OneDrive into the configured local `DOWNLOAD_DIR`.

| Argument | Description |
|---|---|
| `path_or_id` | Human path or Graph item ID for a file |
| `dest_filename` | Optional bare local filename |

`dest_filename` cannot contain path separators and must not already exist. If
omitted, the OneDrive file name is used.

Returns:

```text
{local_path, size_bytes, mime_type, source_name}
```

The file is saved with owner-only permissions. Content is not base64-encoded
into the MCP response, and no OneDrive content is modified.

## `get_drive_info`

```text
get_drive_info()
```

Returns basic drive, owner, and quota information:

```text
{
  drive_id,
  drive_type,
  owner_name,
  quota_used,
  quota_total,
  quota_remaining,
  web_url
}
```

This is a useful lightweight authentication check.

## Pagination

When `has_more` is true, call the same tool with its returned `next_link` and
the same query or path and `top` value. Do not fetch or modify the continuation
directly.

Continuations are opaque, signed, and bound to the original endpoint and page
size. They expire after one hour or a server restart. Begin pagination again
after expiration.

## Paths and local files

Human paths begin at the drive root, for example
`/Documents/report.pdf`. Parameters documented as `path_or_id` also accept a
raw Graph drive item ID.

`local_path` refers to the machine running the MCP server. In WSL, Remote-SSH,
or a remote Open WebUI deployment, the downloaded file remains on that host.
The MCP client needs separate filesystem access to open or summarize it.
