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

GitHub Actions tests Linux on Python 3.11, 3.12, and 3.14, plus Windows on
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
./venv/bin/python -m uv pip compile requirements.txt --universal --python-version 3.11 --generate-hashes --output-file requirements.lock
./venv/bin/python -m uv pip compile requirements-test.txt --universal --python-version 3.11 --generate-hashes --constraint requirements.lock --output-file requirements-test.lock
./venv/bin/python -m uv pip compile requirements-bridge.txt --universal --python-version 3.11 --generate-hashes --constraint requirements.lock --output-file requirements-bridge.lock
./venv/bin/python -m uv pip compile requirements-dev.txt --universal --python-version 3.11 --generate-hashes --constraint requirements-bridge.lock --output-file requirements-dev.lock
./venv/bin/pip install --require-hashes -r requirements-dev.lock
./venv/bin/python -m ruff check .
./venv/bin/python -m pytest -v
./venv/bin/python -m pip_audit --local
npm test
npm pack --dry-run
```

Add `--upgrade` to the compile commands to refresh already-locked versions.
Review all lockfile changes together and retain the `mcp<2.0.0` constraint
unless the server import and stdio integration are updated for MCP 2.x.

## Distribution release

`pyproject.toml` and `package.json` must use the same version. The
`.github/workflows/publish.yml` workflow checks this against the GitHub release
tag (`v<version>`) and builds/validates both distributions.

Use **Actions → Publish packages → Run workflow** for a validation-only dry
run. It builds the Python wheel and sdist, checks them with Twine, runs the npm
tests, and runs `npm pack --dry-run`; it does not publish. Publishing is
triggered only when a GitHub Release is published.

In GitHub repository settings, create environments named `pypi` and `npm`
before publishing. Protect them with authorized reviewers and limit deployments
to release tags such as `v*` so an arbitrary workflow run cannot publish.

Configure the PyPI GitHub Actions Trusted Publisher before publishing the
first Python release. For an unpublished PyPI project, add a pending publisher
with project `joescars-onedrive-mcp`, owner `joescars`, repository
`onedrive-mcp`, workflow `publish.yml`, and environment `pypi`. The first
published release converts it to a normal publisher; a pending publisher does
not reserve the project name.

npm Trusted Publishing is configured on an existing npm package, so the first
npm release is a one-time bootstrap. For `0.1.0`, publish Python through the
GitHub release workflow, confirm it is available on PyPI, then publish the npm
package from an authorized account:

```bash
npm login
npm publish --access public
```

After `@joescars/onedrive-mcp@0.1.0` exists, add its npm Trusted Publisher for
GitHub user `joescars`, repository `onedrive-mcp`, workflow `publish.yml`, and
environment `npm`. Configure it shortly before the next release: npm currently
requires a new publisher configuration to complete its first successful
publish within two days. The workflow intentionally skips npm publishing for
`v0.1.0`; later releases publish to PyPI first, wait for the matching Python
version to appear, and then publish npm using OIDC. No registry write tokens
are stored in GitHub Actions.

Never publish an npm version before the matching Python distribution is
available. Registry versions are immutable; use a new matching version to
correct a release.

## Project structure

```text
onedrive-mcp/
  server.py                     MCP server and five tool definitions
  graph_client.py               GET-only Microsoft Graph client
  auth.py                       MSAL device-code flow and token cache
  pyproject.toml                Python distribution metadata and console scripts
  package.json                  Version-pinned npm launchers
  bin/                          npm MCP and interactive setup commands
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
  .github/workflows/publish.yml Release validation and OIDC publishing
```

## Change guidelines

- Preserve the GET-only Microsoft Graph invariant.
- Keep Graph semantics in `graph_client.py`, authentication in `auth.py`, and
  MCP response handling in `server.py`.
- Run Ruff before tests to catch basic static errors and unused imports.
- Add mocked tests for changes to retry behavior, response shaping,
  pagination, downloads, or error handling.
- Verify the full pytest suite and the real stdio handshake before completing
  a code change.
- Update the relevant guide when user-facing behavior changes.
