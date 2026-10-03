# Comprehensive code review

Review date: 2026-10-02

## Scope

This review covered the complete tracked repository: authentication and token
cache handling, Microsoft Graph requests, MCP tool boundaries, downloads,
pagination, the optional HTTP bridge, deployment configuration, automated
tests, dependency management, and user documentation.

The review prioritized functional correctness, the read-only invariant,
credential and signed-URL handling, filesystem safety, failure behavior,
portability, maintainability, and test coverage.

## Executive summary

The design is compact and generally well defended. Graph access is centralized
in one GET-only helper; errors are sanitized; pagination continuations are
signed and bounded; downloads are size-limited, locked, atomic, owner-only on
POSIX systems, and refuse overwrites; token-cache updates are serialized and
atomic; and the MCP surface remains limited to five read-style tools.

No unresolved application-code correctness or security defect was found. The
dependency audit did identify vulnerable locked versions, which were refreshed.
One stale operator reference was corrected, and regression coverage was added
for the authorization-header contract because real Graph authentication
depends on it.

## Findings

| ID | Severity | Area | Finding | Resolution |
| --- | --- | --- | --- | --- |
| CR-1 | High | `requirements*.lock` | The installed locked environment contained 18 published advisories affecting PyJWT 2.13.0, urllib3 2.7.0, and the maintenance environment's setuptools 79.0.1. | Refreshed all locks from their declared inputs and explicitly constrained the audited maintenance setuptools version to a fixed release. |
| CR-2 | Low | `scripts/run_mcp_bridge.py` | The missing-key error referred operators to `README.md §6`, but the README no longer has that section after the documentation restructure. | Fixed the message and test to point to `docs/deployment.md`. |
| CR-3 | Low | `tests/test_hardening.py` | HTTP tests mocked token acquisition but did not explicitly assert that Graph requests use the acquired token with the Bearer scheme. A future authorization-header regression could therefore be obscured by permissive mocks. | Added a synthetic-token request-header regression test. |
| CR-4 | Low | `.github/workflows/ci.yml` | CI exercised behavior and dependencies but had no static check for unused imports and basic Python errors. | Added Ruff to the maintenance lock, CI, and documented local validation flow. |
| CR-5 | Low | `docs/setup.md` | Setup stated the minimum Python version but did not make interpreter selection part of the installation flow, so an older system `python3` could create an unusable environment. | Added an explicit version preflight and recovery guidance. |

## Confirmed strengths

- The Graph client contains no write verbs and keeps Microsoft Graph traffic
  behind the guarded `_get()` helper.
- Graph requests use the acquired token with the Bearer authorization scheme.
  The separate CDN request is GET-only, omits that header, and sanitizes
  transfer exceptions so signed URLs do not enter MCP responses.
- HTTP 401 refresh and HTTP 429 retry budgets are independent, bounded, and
  covered by tests.
- Caller-visible pagination values are authenticated, request-bound,
  size-bounded, and time-limited.
- Download paths reject traversal, reserved temporary names, alternate data
  stream syntax, and existing destinations.
- Download publication uses an atomic hard link under a directory-wide lock;
  incomplete temporary files are removed on failures.
- Relative configuration paths are anchored to the repository rather than the
  launcher working directory.
- The token cache uses an inter-process lock, atomic replacement, and owner-only
  permissions on the supported POSIX deployment target.
- The stdio integration test verifies the real MCP handshake, exact tool set,
  and generated input bounds without network access.
- Documentation consistently states the Linux security boundary and avoids
  claiming that POSIX modes provide Windows ACL protection.

## Non-blocking recommendations

1. Pin GitHub Actions by immutable commit SHA if the project adopts a stricter
   CI supply-chain policy. The current `actions/checkout@v4` and
   `actions/setup-python@v5` major tags are conventional but mutable.
2. Consider structured response models if the MCP surface grows. Plain
   dictionaries are appropriate for five small tools today, but explicit
   models would make larger future response-contract changes easier to review.

## Validation results

- Ruff static checks passed.
- The full credential-free suite passed: 122 tests.
- The MCP stdio handshake passed from an unrelated working directory.
- `pip check` reported no broken requirements.
- `pip-audit` reported no known vulnerabilities after the lock refresh.

No live OneDrive smoke test was run because it requires a human-owned credential
cache and real account access.
