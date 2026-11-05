# Milestone 8D — Final Public Release Audit

## Scope

This audit covers the intended first public release candidate for InvoiceFlow AI:

- Current tracked and intended untracked files.
- Reachable Git history and refs.
- Secret and privacy exposure.
- Dependency and container security.
- GitHub Actions workflow safety.
- Documentation, reproducibility, runtime health, and quality gates.

No commit, push, remote creation, tag, live benchmark, Groq call, or destructive
database workflow was performed.

## Backend test-count investigation

The discrepancy was caused by different pytest scopes, not missing tests.

| Command | Collected | Passed | Skipped |
| --- | ---: | ---: | ---: |
| python -m pytest backend --collect-only -q | 268 | — | — |
| python -m pytest -q --collect-only | 274 | — | — |
| python -m pytest -q | 274 | 260 | 14 |

The six additional root-level tests are in
tests/test_pdfplumber_comparison.py. They are included by the root release/CI
command but excluded by the backend-only command.

The working tree and HEAD contain the same 30 backend test files. There are no
working-tree changes to backend tests, pytest configuration, markers, dependency
groups, or test discovery. The archived HEAD tree also collected 268 backend
tests. No tests were removed, renamed, merged, or unintentionally skipped.

Classification: Expected scope difference; historical report clarified.

The current release command is documented as python -m pytest -q.

## Secret scan

Tools and scope:

- Gitleaks v8.30.1 in a disposable, network-isolated, read-only container.
- Current intended release-file copy, excluding .env, caches, virtual
  environments, runtime data, and build artifacts.
- Sanitized bare Git mirror containing reachable history and refs.
- Additional non-printing regex checks for common API keys, cloud keys, PATs,
  private-key headers, and provider-token formats.

Results:

- Current intended release files: no leaks found.
- 30 reachable Git commits: no leaks found.
- Regex checks: no findings.
- No secret values were printed.

The external pre-cleanup backup bundle was not scanned or included in release
scope.

## Privacy review

No confirmed personal emails, API keys, webhook secrets, private keys, real
customer records, or private invoice contents were found in intended release
files.

Findings:

- Historical milestone documents contain the development path D:\InvoiceFlow AI;
  this is acceptable historical evidence but is not needed in new public-facing
  documents.
- .env is ignored and was excluded from the sanitized release scan.
- .env.example contains placeholders only.
- CI uses synthetic values such as a CI-only database password.
- Portfolio screenshots contain synthetic invoice identifiers and UI data only.
- .runtime, virtual environments, node_modules, coverage, test results,
  generated evaluation data, and local caches are ignored and excluded.

Classification: no must-remove privacy finding. Historical paths are
acceptable development evidence.

## Git history and refs

- Intended branch: master.
- Current HEAD: 9f3c70b, Milestone 7C quality/security gate commit.
- No Git remote is configured.
- No tags exist.
- No unexpected local branch exists.
- Two refs/codex/... turn-diff/checkpoint refs exist. They are not branches,
  are not intended for publication, and must not be pushed.
- Secret scanning found no leaks in reachable history.

The first push must name the intended branch explicitly. Do not use
git push --mirror or indiscriminate git push --all.

## Large files and artifacts

Intentional portfolio assets:

| File | Size |
| --- | ---: |
| docs/assets/review-queue.png | 41,971 bytes |
| docs/assets/review-detail.png | 48,491 bytes |

Both are reasonable-sized PNGs generated from mocked Playwright data.

The Git ignore rules cover local secrets, virtual environments, node_modules,
coverage, Playwright artifacts, runtime data, generated evaluation output,
diagnostics, logs, and local databases. Temporary audit copies under
.runtime/release-scan are ignored and are not intended for release.

## License

No LICENSE file exists.

Practical choices:

- MIT: permissive portfolio/open-source reuse with minimal restrictions.
- Apache-2.0: permissive reuse with explicit patent provisions.
- No License: source may be viewable, but reuse and redistribution are not
  granted by default.

## LICENSE DECISION REQUIRED

## GitHub Actions security review

Workflow: .github/workflows/ci.yml

Verified controls:

- permissions: contents: read.
- No write permissions, PAT, deployment step, or pull_request_target.
- Push, pull-request, and manual triggers target master.
- Concurrency cancels superseded runs.
- All jobs have timeouts.
- No production credentials are used.
- Compose validation uses synthetic environment values.
- Browser and coverage artifacts have seven-day retention.
- No external services, Groq, n8n, or live database are used by CI jobs.
- YAML parsing passes; actionlint was previously run locally and passed.
- The current workflow was not executed on GitHub because no push occurred.

Action pinning:

| Action | Current reference | Maintainer | Pinning status | Recommendation |
| --- | --- | --- | --- | --- |
| actions/checkout | v4 | GitHub | Major tag | Pin to a verified full commit SHA before public hardening |
| actions/setup-python | v5 | GitHub | Major tag | Pin to a verified full commit SHA before public hardening |
| actions/setup-node | v4 | GitHub | Major tag | Pin to a verified full commit SHA before public hardening |
| actions/upload-artifact | v4 | GitHub | Major tag | Pin to a verified full commit SHA before public hardening |

No SHA values were guessed or substituted.

## Dependency and supply-chain results

Python:

- Local development/evaluation environment: PASS — no known vulnerabilities.
- Unpublished invoiceflow-api package was reported as not present on PyPI and
  therefore skipped as a package identity; its installed dependencies were
  audited.
- API runtime image built successfully from backend/Dockerfile.
- Runtime image pip-audit: PASS — no known vulnerabilities.
- Application dependency versions are pinned in backend/pyproject.toml.

Frontend:

- npm ci: PASS using the committed lockfile.
- Full npm audit: online advisory endpoint was unavailable in the first run;
  offline audit reported 0 vulnerabilities.
- Production-only npm audit: 0 vulnerabilities in offline audit.
- No dependency changes were made during this audit.
- No npm audit fix --force was run.

Dockerfiles use python:3.12-slim and node:22-alpine. Runtime Python
dependencies are installed from the pinned backend project dependencies; the
frontend image uses the committed package-lock.json.

## Quality-gate results

| Gate | Result |
| --- | --- |
| Full Pytest | PASS — 260 passed, 14 skipped, 7 warnings |
| Backend-only Pytest | PASS — 254 passed, 14 skipped, 1 warning |
| Ruff check | PASS |
| Ruff format check | PASS |
| Backend Pyright | PASS — 0 errors |
| Root/evaluation Pyright | PASS — 0 errors |
| Vitest | PASS — 10 passed |
| Frontend coverage | PASS — statements 69.23%, branches 69.23%, functions 60.34%, lines 88% |
| Playwright Chromium | PASS — 4 passed |
| ESLint | PASS |
| TypeScript/Vite build | PASS |
| Python pip-audit | PASS |
| API runtime image audit | PASS |
| npm full audit | PASS from offline cache; online advisory endpoint unavailable |
| npm production audit | PASS from offline cache |
| Docker Compose config | PASS |
| Documentation relative links | PASS |
| Git diff check | PASS |
| API health | PASS — HTTP 200 |
| API readiness/PostgreSQL | PASS — HTTP 200 |
| Frontend availability | PASS — HTTP 200 |
| n8n availability | PASS — HTTP 200 |

The 14 skipped tests are environment-dependent PostgreSQL/live-Groq tests and
one PostgreSQL foreign-key test. No live Groq request was made.

## Documentation audit

Reviewed README, current architecture, demo, roadmap, release checklist,
milestone reports, workflow configuration, and screenshot links.

Corrected or verified:

- Root README now uses the complete root pytest command.
- Current backend/evaluation result is documented as 260 passed, 14 skipped,
  and 7 warnings.
- Browser API port is documented as host port 18000.
- .env.example uses VITE_API_BASE_URL=http://localhost:18000.
- GitHub Actions is described as locally validated and remotely pending.
- No production-certification, exactly-once, or unmeasured AI-accuracy claim is
  made.
- Relative documentation and screenshot links resolve successfully.

## Intended release inventory

### Application

- backend/
- frontend/
- compose.yaml
- backend/Dockerfile
- frontend/Dockerfile
- .env.example

### Automated tests

- backend/tests/
- root tests/
- frontend/src/*.test.ts(x)
- frontend/e2e/
- Vitest and Playwright configuration

### CI

- .github/workflows/ci.yml

### Documentation

- README.md
- ARCHITECTURE.md
- PROJECT_REQUIREMENTS.md
- docs/architecture.md
- docs/demo.md
- docs/roadmap.md
- docs/release-checklist.md
- docs/business-validation-v1.md
- milestone reports

### Portfolio assets

- docs/assets/review-queue.png
- docs/assets/review-detail.png

### Configuration templates

- .env.example
- dependency manifests and lockfiles

No required application code is missing from the intended fileset, and no
ignored secret/runtime artifact is intended for publication.

## Remaining blockers

Concrete blockers:

1. License decision is required before public release.
2. Online npm advisory verification should be repeated when the registry
   advisory endpoint is available; offline audit is clean, with no vulnerability
   finding currently known.
3. Optional release hardening: pin GitHub Actions to verified immutable SHAs.

No blocker is raised for HTTPS, monitoring, durable outbox, or cloud deployment;
those are documented future production work.

## First-push checklist

Do not execute this sequence until the license decision and final staging
review are complete:

~~~powershell
git status --short
git diff --check
git branch --show-current
git remote -v

# After choosing a license and reviewing the intended files:
git add <explicit-release-files>
git diff --cached --check
git commit -m "release: prepare InvoiceFlow AI v0.1.0"

# Create an empty GitHub repository manually, without README/license/gitignore.
git remote add origin <repository-url>
git push --set-upstream origin master

# Observe the first GitHub Actions run, then address remote-only failures.
# Add a CI badge only after the repository path is known.
# Create and push v0.1.0 only after remote CI passes:
git tag -a v0.1.0 -m "InvoiceFlow AI v0.1.0"
git push origin v0.1.0
# Create GitHub Release notes manually after the tag is verified.
~~~

## Decision

MILESTONE 8D — RELEASE CANDIDATE APPROVED, LICENSE DECISION PENDING
