# Milestone 8E.3-A — Final Release Preparation

## Scope

This phase prepares the local release candidate for InvoiceFlow AI v0.1.0. It
does not push, tag, publish a GitHub Release, call Groq, or execute live n8n
workflows.

## Changes

- Restricted the API host binding to `127.0.0.1:18000:8000`.
- Restricted the n8n host binding to `127.0.0.1:5678:5678`; internal Compose
  service networking remains unchanged.
- Pinned checkout, setup-python, setup-node, and upload-artifact to verified
  full commit SHAs for Node 24-compatible releases, with version comments.
- Added real CI and MIT badges plus the existing synthetic review screenshots
  to the README.
- Synchronized current architecture, demo, checklist, and release-note claims
  with the 8E.2 fixes and latest local evidence.
- Preserved the limitation that end-user authentication and authorization are
  not implemented.

## Verified action pins

| Action | Version | Commit SHA |
| --- | --- | --- |
| `actions/checkout` | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| `actions/setup-python` | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| `actions/setup-node` | v7.1.0 | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
| `actions/upload-artifact` | v6.0.0 | `b7c566a772e6b6bfb58ed0dc250532a479d7789f` |

The selected releases use the Node 24 action runtime and require GitHub
Actions runner `v2.327.1` or newer. The application test runtime remains
Python `3.12.7` and Node `20.20.2`; action runtime and application runtime are
separate concerns.

## Latest local evidence

- Full Pytest: 269 passed, 14 skipped, 7 warnings.
- Vitest: 10 passed.
- Playwright Chromium: 4 passed.
- Frontend coverage: 69.23% statements, 69.23% branches, 60.34% functions,
  88% lines.
- Ruff, Ruff format, Pyright, ESLint, Vite build, npm audits, Python audit,
  Docker Compose validation, and workflow YAML validation passed.
- Linux storage-path regression: 16 passed.

The effective Compose host bindings and service health were verified after the
port-binding change: API health/readiness, frontend, and n8n returned HTTP 200,
and PostgreSQL remained healthy. GitHub-hosted CI must still run against the
new release-preparation commit.

## Local release state

The release-preparation commit is created locally only after the applicable
gates pass. The v0.1.0 tag and GitHub Release remain pending until the new
commit passes hosted CI. No credentials, environment files, generated
benchmark artifacts, or runtime data are part of the release changes.
