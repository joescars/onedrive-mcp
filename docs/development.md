# Development

[Back to README](../README.md)

Use the project's virtual environment and existing dependency locks. Automated
tests must never call a real Microsoft account or Microsoft Graph endpoint.

## Test suite

```bash
./venv/bin/python -m pytest -v
```

The tests mock Graph and MSAL traffic and cover:

- Authentication configuration and token-cache concurrency.
- HTTP retry, refresh, and error sanitization.
- Signed pagination continuations.
- Atomic downloads, storage limits, and transfer integrity.
- Bridge configuration and startup behavior.
- A real MCP stdio handshake from an unrelated working directory.

POSIX permission checks run on Linux and are skipped on Windows. Passing
Windows tests is not evidence that credentials have secure Windows ACLs.

GitHub Actions tests Linux on Python 3.10, 3.12, and 3.14, plus Windows on
Python 3.12. It also runs an installed-dependency audit on pushes, pull
requests, and a weekly schedule.

## Live smoke test

After completing device-code sign-in:

```bash
./venv/bin/python scripts/smoke_test.py
```

This manually calls `get_drive_info()` and `list_folder('/')` against the real
account. It is intentionally excluded from pytest.

## Dependency locks

Python 3.11 or newer is required for maintenance tools. The `.txt` files
declare direct inputs; generated `.lock` files pin transitive versions and
artifact hashes across supported platforms. Do not edit lockfiles manually.

Install the maintenance tools and regenerate locks in dependency order:

```bash
./venv/bin/pip install --require-hashes -r requirements-dev.lock
./venv/bin/python -m uv pip compile requirements.txt --universal --python-version 3.10 --generate-hashes --output-file requirements.lock
./venv/bin/python -m uv pip compile requirements-bridge.txt --universal --python-version 3.11 --generate-hashes --constraint requirements.lock --output-file requirements-bridge.lock
./venv/bin/python -m uv pip compile requirements-dev.txt --universal --python-version 3.11 --generate-hashes --constraint requirements-bridge.lock --output-file requirements-dev.lock
./venv/bin/pip install --require-hashes -r requirements-dev.lock
./venv/bin/python -m pytest -v
./venv/bin/python -m pip_audit --local
```

Add `--upgrade` to the compile commands to refresh already-locked versions.
Review all lockfile changes together and retain the `mcp<2.0.0` constraint
unless the server import and stdio integration are updated for MCP 2.x.

## Project structure

```text
onedrive-mcp/
  server.py                     MCP server and five tool definitions
  graph_client.py               GET-only Microsoft Graph client
  auth.py                       MSAL device-code flow and token cache
  scripts/
    setup_auth.py               Interactive one-time sign-in
    smoke_test.py               Live integration check
    run_mcp_bridge.py           API-key-protected HTTP bridge launcher
  tests/
    test_auth.py
    test_bridge.py
    test_graph_client.py
    test_hardening.py
    test_server.py
  deploy/
    onedrive-mcpo.service       systemd bridge template
  docs/                         User and maintainer guides
  requirements*.txt            Dependency inputs
  requirements*.lock           Hash-locked dependencies
  .github/workflows/ci.yml      Tests and dependency audit
```

## Change guidelines

- Preserve the GET-only Microsoft Graph invariant.
- Keep Graph semantics in `graph_client.py`, authentication in `auth.py`, and
  MCP response handling in `server.py`.
- Add mocked tests for changes to retry behavior, response shaping,
  pagination, downloads, or error handling.
- Verify the full pytest suite and the real stdio handshake before completing
  a code change.
- Update the relevant guide when user-facing behavior changes.
