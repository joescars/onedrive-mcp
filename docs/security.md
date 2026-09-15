# Security model

[Back to README](../README.md)

This server provides read-only access to a personal OneDrive account. That
reduces risk but does not make the account data public or non-sensitive.

## Read-only enforcement

`graph_client.py` is the only module that communicates with Microsoft Graph.
Its `_get()` helper hardcodes `requests.get` and asserts that the HTTP function
has not been replaced with another verb.

The only other network request streams file content from a pre-authenticated
Microsoft CDN URL. It is also GET-only and never uploads data.

The MCP server exposes exactly five tools:

- `search_onedrive`
- `list_folder`
- `get_item_metadata`
- `download_file`
- `get_drive_info`

There are no upload, edit, delete, move, or rename operations.

## Credentials

The MSAL token cache contains access and refresh credentials. It is stored as
plaintext protected by owner-only POSIX permissions and must never be
committed, logged, or shared.

Cache updates use owner-only temporary files, atomic replacement, and a
cross-process lock. Relative `TOKEN_CACHE_PATH` values resolve from the
project directory rather than the process's working directory.

The project-local `.env` contains configuration rather than bearer credentials
by default, but it is also restricted to mode `0600` because environment files
commonly gain sensitive values over time.

## Platform security boundary

Linux is the supported production environment. Windows/non-POSIX execution is
development-only because POSIX permission modes do not create Windows ACLs.
Do not use real tokens or personal downloads on Windows unless the relevant
directories are independently protected with appropriate ACLs.

Use a local Linux filesystem for the checkout, token cache, and downloads.
Network shares, Windows-mounted filesystems, and cloud-synced directories may
not preserve owner-only permissions or the atomic filesystem behavior used by
downloads.

## Downloads

Files are saved only under `DOWNLOAD_DIR`, using a bare destination filename.
Path separators, traversal components, control characters, and internal
temporary names are rejected.

Each transfer is checked against:

- `MAX_DOWNLOAD_BYTES`, defaulting to 100 MiB per file.
- `MAX_DOWNLOAD_DIR_BYTES`, defaulting to 1 GiB in total.

Limits are enforced using Graph metadata and while streaming, including when
metadata is missing or incorrect. Transfers sharing a directory are serialized
to enforce the directory budget. Files written there by unrelated processes
remain outside this locking mechanism but still count toward the measured
usage.

Completed files are published only after transfer and size validation.
Publication never overwrites an existing path, including during filename
races, and requires a filesystem that supports atomic hard links. Interrupted
or oversized transfers remove temporary files. Files are never automatically
deleted to make room. Existing files, including abandoned temporary files,
count toward the directory limit. A crashed process may leave an owner-only
`.onedrive-part-*` file; remove it only after confirming no transfer is active.

Signed download URLs are never included in metadata, errors, logs, or MCP
responses. Transport failures return sanitized messages.

## Pagination

Search and folder listings accept page sizes from 1 to 200. When `has_more` is
true, pass the opaque `next_link` to the same tool with the original query or
path and page size.

Continuations are signed, bound to their original endpoint and page size, and
expire after one hour or a server restart. They are not raw URLs and should
never be fetched directly.

## HTTP bridge

The optional `mcpo` launcher requires an API key and defaults to
`127.0.0.1`. Loopback binding does not isolate the service from other accounts
on the same machine, so the API key remains mandatory. See
[Open WebUI deployment](deployment.md) for secure operation.

## Privacy

MCP tool results may include file names, paths, metadata, owner details, and
quota information. This data enters the calling client's chat context and may
be processed or retained according to the client and model provider's
policies.

Review tool approvals and use only accounts and files you intend to expose to
the configured client. A downloaded file remains on the server host; the tool
returns its local path rather than embedding its content in the response.
