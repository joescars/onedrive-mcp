# AGENTS.md — onedrive-mcp

Guidance for AI coding agents (and humans) working in this repository.

## What this project is

A **read-only** Model Context Protocol (MCP) server that lets an AI agent
search and download files from a **personal Microsoft OneDrive** account via
the Microsoft Graph API. It runs as a local stdio process, registered with
Hermes directly, or bridged to HTTP/OpenAPI for Open WebUI via `mcpo`.

Full user-facing setup docs live in `README.md` — read that first for Azure
app registration, `.env` config, and how each interface (Hermes / Open WebUI)
consumes this server. This file is about how to safely modify the code.

## Non-negotiable safety invariant: READ ONLY

This is the single most important rule in this codebase:

- `graph_client.py` must NEVER call `requests.post/put/patch/delete` against
  Microsoft Graph. `_get()` is the only Graph network helper, and it
  hardcodes `requests.get` with an `assert`
  guarding against that being changed. Do not add a write/upload/delete/
  rename/move capability, even if asked to "optimize" or "extend" this file
  — if such a feature is ever wanted, it must be a new, clearly-labeled,
  explicitly-opt-in tool, not a change to `_get()` or the existing tools.
  A separate GET-only CDN request streams pre-authenticated download URLs
  without an Authorization header. Never expose those URLs in errors or logs.
- The MCP server (`server.py`) must only ever expose read-style tools:
  `search_onedrive`, `list_folder`, `get_item_metadata`, `download_file`
  (downloads FROM OneDrive TO local disk — never the reverse), and
  `get_drive_info`. Do not add `upload_file`, `delete_item`, `rename_item`,
  etc.
- Never log access tokens, refresh tokens, or the contents of
  `token_cache.bin`.

## Architecture

```
server.py        MCP server (FastMCP, stdio transport) — the 5 tool defs.
                 Converts graph_client's clean dicts into MCP tool
                 responses; converts GraphError/GraphNotFoundError into
                 friendly {"error": ...} dicts (never a raw traceback).
graph_client.py  Thin Microsoft Graph HTTP wrapper. Owns:
                 - the GET-only enforcement (see above)
                 - 401 → forced silent refresh, then retry once
                 - 429 → independent retry/sleep budgets honoring Retry-After
                 - 404 → GraphNotFoundError with a clean message
                 - path_or_id resolution (human path vs raw Graph item id)
                 - signed pagination continuations and bounded page sizes
                 - atomic downloads and directory/file byte limits
                 - response shaping (shape_drive_item / shape_drive_item_full)
auth.py          MSAL PublicClientApplication, device-code flow, token
                 locked, atomic cache load/save (chmod 600), silent token acquisition.
                 No Graph HTTP calls live here — that's graph_client.py's job.
scripts/
  setup_auth.py  One-time interactive device-code sign-in (run by the human).
  smoke_test.py  Manual integration check against real OneDrive (needs auth).
tests/
  test_graph_client.py   Unit tests, mocked HTTP (no network), covers
                          path resolution + response shaping + retry logic.
  test_auth.py            Unit tests for env/config parsing in auth.py.
deploy/
  onedrive-mcpo.service   systemd template for the mcpo HTTP bridge.
```

## Critical lesson: never assume cwd

**This project is launched by MCP hosts (Hermes, `mcp dev`, `mcpo`, etc.)
with an arbitrary working directory — never assume it's the project root.**

This bit us once already (see git history / commit "Fix .env and
relative-path resolution to use script dir, not launcher cwd"): `auth.py`
called `load_dotenv()` with no argument, and `TOKEN_CACHE_PATH`/`DOWNLOAD_DIR`
defaulted to relative paths like `./token_cache.bin`. Both resolved against
whatever directory the *launching process* happened to have as its cwd (e.g.
Hermes's own install dir), not this repo — so `.env` silently failed to load
and the token cache appeared "missing" even after a successful sign-in.

**Rule going forward**: any code in this repo that reads `.env` or resolves a
relative default path MUST anchor it to `Path(__file__).resolve().parent`
(or the shared `auth.PROJECT_ROOT` constant), never to `os.getcwd()` /
bare relative `Path("./...")`. If you add a new module that needs `.env` or a
default file path, follow the existing pattern in `auth.py` / `server.py`
rather than reintroducing `load_dotenv()` with no arguments.

## Testing conventions

- Unit tests (`tests/`) must never make real network calls. Mock Graph HTTP
  responses (the `responses` library is already a dependency) or use
  `unittest.mock`. Run with:
  ```bash
  ./venv/bin/python -m pytest -v
  ```
- `scripts/smoke_test.py` is intentionally excluded from the pytest suite —
  it requires a real signed-in `token_cache.bin` and hits live OneDrive. Only
  run it manually, and only after `scripts/setup_auth.py` has succeeded.
- When changing `graph_client.py`'s error handling, retry logic, or response
  shaping, add/update a mocked unit test in `tests/test_graph_client.py`
  rather than relying on manual smoke testing alone.
- After any change, verify the server still starts and lists its 5 tools
  (a real stdio handshake, not just `py_compile`) before considering the
  work done — this catches import-time errors that unit tests alone might
  miss (e.g. a broken `load_dotenv()` path, a bad top-level import).

## Environment / dependencies

- Python 3.10+ (developed against 3.12). Use the project's own `venv/`, not
  system Python.
- Linux is the secure deployment target. Windows is development-only; do not
  claim POSIX modes implement Windows ACLs. Optional bridge/maintenance tools
  require Python 3.11+.
- Install `requirements.lock` with `--require-hashes`. Dependency inputs and
  bridge/development lock regeneration are documented in README.md.
- `requirements.txt` pins `mcp<2.0.0` deliberately — `mcp` 2.x moved/renamed
  `FastMCP`. Do not upgrade past 2.0 without updating `server.py`'s import
  and verifying the stdio handshake still works.
- `.env` (git-ignored) holds `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` (default
  `consumers`), `TOKEN_CACHE_PATH`, `DOWNLOAD_DIR`. Never commit real values;
  `.env.example` documents the shape with placeholders.
- `token_cache.bin` (git-ignored, chmod 600) holds MSAL's serialized token
  cache — treat it like a credential file. Never read/print its contents in
  logs or agent output.

## Git workflow for this repo

- Do not push or open a PR unless the user explicitly requests it.
- Commit messages should explain the *why*, especially for anything touching
  auth, path resolution, or the read-only guarantees — future agents (and
  the human) need to understand why a given guard exists before "simplifying"
  it away.

## When making changes, in order

1. Understand which layer the change belongs to (`server.py` tool surface,
   `graph_client.py` Graph semantics, or `auth.py` token handling) — keep
   the separation of concerns intact.
2. If touching `graph_client.py`, re-confirm the GET-only invariant still
   holds (no new HTTP verb introduced, `_get()`'s assert untouched).
3. Add/update mocked unit tests for the new behavior.
4. Run the full pytest suite: `./venv/bin/python -m pytest -v`.
5. Verify the server still starts and exposes exactly the intended tool set
   via a real stdio handshake (see README's "Testing" section for the
   pattern), not just an import check.
6. Update `README.md` if user-facing behavior (tool signatures, setup steps,
   troubleshooting) changed.
7. Commit locally with a message explaining why, not just what.
