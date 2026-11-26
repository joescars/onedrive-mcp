# OneDrive read-only access

A local MCP server that lets an agent search, inspect, and download files from one person's Microsoft OneDrive without ever changing anything in it.

## Configuration

**Settings**:
The complete, validated runtime configuration for one process, fixed from the moment it starts until it exits.
_Avoid_: config, env, options

**Unconfigured**:
The state of Settings when no app registration (client ID) has been provided yet; tools are still discoverable, but nothing can reach OneDrive.
_Avoid_: missing config, not set up

**Config directory**:
The stable per-user location holding the `.env` file that Settings is read from.
_Avoid_: project root, cwd

**Data directory**:
The stable per-user location for persistent state: the token cache and the download directory.
_Avoid_: app dir, install dir

**Legacy checkout configuration**:
A `.env` found inside the source checkout, honored only when no config-directory `.env` exists; relative paths then resolve from the checkout.
_Avoid_: local config, fallback env

## Access

**Token source**:
The thing that supplies an access token for the signed-in account, refreshing or prompting sign-in as needed.
_Avoid_: auth, credentials, token provider

**Token cache**:
The owner-only file holding the signed-in account's tokens; treated as a credential.
_Avoid_: session file, credentials file

## Reading OneDrive

**Item**:
A single file or folder in the drive, addressed either by human path or by drive item id.
_Avoid_: object, entry, node

**Continuation**:
An opaque, signed token returned with a page of results that fetches the next page of the same query; it expires after an hour or a server restart.
_Avoid_: cursor, next link, page token

**Download directory**:
The local folder under the data directory where downloaded files are saved, bounded by per-file and per-directory byte limits.
_Avoid_: downloads folder, output dir
