# Milestone 7C — Quality Gates and Release Readiness

## Baseline

The initial full suite completed with `260 passed, 14 skipped, 6 warnings`.
Ruff lint, Ruff formatting, root Pyright, Docker Compose configuration, and the
runtime stack were healthy. Backend Pyright reported two errors:

- `backend/app/services/business_validation/rules.py`: optional `Decimal`
  narrowing in line-item arithmetic.
- `backend/tests/test_invoice_workflow.py`: optional structured-data access in
  a regression fixture.

The first frontend command was accidentally run from the repository root, where
no `package.json` exists. The documented frontend-directory commands passed.

## Fixes

### `backend/app/services/business_validation/rules.py`

Explicitly narrowed parsed discount and tax values before arithmetic. This
preserves the existing validation behavior while giving Pyright the actual
non-null contract established by the rule's missing-field branch.

### `backend/tests/test_invoice_workflow.py`

Added an explicit assertion that structured extraction data is present before
copying it. This improves fixture typing without changing production behavior.

## Final Quality Gates

| Gate | Result |
|---|---|
| Full Pytest | PASS — 260 passed, 14 skipped, 6 warnings |
| Ruff lint | PASS |
| Ruff formatting | PASS — 91 files formatted |
| Backend Pyright | PASS — 0 errors |
| Evaluation Pyright | PASS — 0 errors |
| TypeScript/build | PASS — `npm run build` |
| ESLint | PASS — `npm run lint` |
| Frontend automated tests | NOT APPLICABLE — no test script configured |
| Python dependency audit | FAIL - 11 advisory rows in 3 installed packages |
| Frontend dependency audit | FAIL - 8 dev-tree vulnerabilities; production-only audit clean |
| Docker Compose config | PASS |
| Docker runtime | PASS — db/api/frontend healthy, n8n running |
| API readiness | PASS — HTTP 200 with database `ok` |
| Alembic | PASS — `d8f9a0b1c2d3 (head)` |
| Git diff check | PASS |

## Skipped Tests and Warnings

The 14 skips are intentional and environment-dependent:

- PostgreSQL concurrency/readiness tests skip when `TEST_DATABASE_URL` is not
  configured for the host test process.
- The live Groq smoke test is disabled unless explicitly enabled and keyed.
- One audit foreign-key test is PostgreSQL-specific.

No skipped test was removed or relabeled. The six warnings are existing
FastAPI/httpx deprecation and integration-marker/cache warnings.

## Security and Dependency Audit

- `.env` is ignored and not tracked.
- Only `.env.example` is tracked; secret fields are blank or placeholders.
- No credentials were added to source or evidence artifacts.
- n8n webhook authentication remains enabled in the live environment.
- No sensitive values were printed.
- Python scan: **FAIL / remediation required**. Command:
  `.venv\Scripts\python.exe -m pip_audit --local --format=columns`.
  It reported 11 advisory rows affecting three installed packages: `pypdf`
  6.21.2 (CVE-2026-119203 through CVE-2026-119209; fixed in 6.21.3 or
  6.21.4 depending on the advisory), `pytest` 8.4.2 (PYSEC-2026-1845; fixed
  in 9.0.3), and `pdfminer.six` 20251107 (PYSEC-2026-1761; fixed in
  20251230). `pdfminer.six` is present through the installed evaluation
  environment (`pdfplumber`), and `pip-audit` scans the environment rather
  than only the application declaration. The local package itself was skipped
  because `invoiceflow-api` is not published on PyPI.
- Frontend scan: **FAIL / remediation required** for the complete install.
  `Set-Location frontend; npm audit --audit-level=high` reported 8
  vulnerabilities: one high Vite advisory family affecting Vite 7.1.12 and
  seven moderate advisories in `minimatch` through the TypeScript ESLint
  dependency tree. npm's suggested `npm audit fix --force` would install
  `vite@7.3.7` outside the stated dependency range and downgrade/change
  `typescript-eslint`, so it was not applied. Production-only verification
  passed: `npm audit --omit=dev --audit-level=high` reported 0 vulnerabilities.
- These results are confirmed findings, not network failures. No dependency
  declaration or lockfile was changed during this audit.

## Runtime and Architecture

The Docker development stack remains operational. FastAPI/PostgreSQL retain
business-state ownership; n8n remains an external orchestration layer. Webhook
delivery remains best-effort post-commit without an outbox or durable retry
queue.

Milestone 7B live evidence remains preserved under the ignored `.runtime`
directory and was not regenerated.

## Known Limitations

- Pinned dependency advisories remain open: review and test the available
  `pypdf`, `pytest`, `pdfminer.six`, Vite, and TypeScript ESLint upgrade paths
  before release. Do not use `npm audit fix --force` without an explicit
  compatibility review.
- No frontend component-test framework is configured.
- HTTPS, production secret management, backups, monitoring, and durable
  webhook retry remain deployment concerns outside this local portfolio stack.
- The existing two-step best-effort webhook delivery can lose notifications if
  n8n is unavailable after the business transaction commits.

## Reproduction Commands

```powershell
Set-Location "D:\InvoiceFlow AI"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check backend
.venv\Scripts\ruff.exe format --check backend
.venv\Scripts\pyright backend
.venv\Scripts\pyright
Set-Location frontend
npm run build
npm run lint
npm audit --audit-level=high
npm audit --omit=dev --audit-level=high
Set-Location ..
.venv\Scripts\python.exe -m pip_audit --local --format=columns
docker compose config --quiet
docker compose ps
```

## Milestone 7C.2 - Controlled Dependency Security Remediation

### Scope and Environment Separation

This remediation updated only dependencies with confirmed audit findings and
the exposed Vite development-server binding. There is no Python lockfile; the
authoritative Python pins are in `backend/pyproject.toml`. The frontend uses
`frontend/package.json` and lockfile version 3 in `frontend/package-lock.json`.

The three audited environments are intentionally different:

- The local virtual environment contains production, development, and
  evaluation dependencies.
- The API image installs only `project.dependencies`; it contains `pypdf`, but
  not `pytest`, `pdfplumber`, or `pdfminer.six`.
- The frontend build/development tree contains Vite and TypeScript ESLint;
  the production browser dependency set contains React and React DOM only.

### Dependency Upgrade Matrix

| Package | Before | After | Origin and environment | Advisory / result |
|---|---:|---:|---|---|
| `pypdf` | 6.21.2 | 6.21.4 | Direct API runtime dependency | CVE-2026-119203 through CVE-2026-119209 resolved |
| `pdfplumber` | 0.11.8 | 0.11.10 | Direct evaluation dependency | Updates its exact `pdfminer.six` constraint |
| `pdfminer.six` | 20251107 | 20260107 | Transitive evaluation dependency via `pdfplumber` | PYSEC-2026-1761 resolved |
| `pytest` | 8.4.2 | 9.0.3 | Development/test only | PYSEC-2026-1845 resolved |
| `vite` | 7.1.12 | 7.3.7 | Direct frontend development/build dependency | All reported Vite advisories resolved |
| `minimatch` | 9.0.9 | 11.0.1 | Nested under `@typescript-eslint/typescript-estree` | GHSA-g3hv-75wg-m249 resolved by a targeted override |
| `pip` in API image | 25.0.1 | 26.2.2 | Python base-image tooling | 12 runtime-image audit rows resolved |

`pdfplumber 0.11.8` pinned `pdfminer.six==20251107`, so updating
`pdfminer.six` alone would have created an invalid environment. Version
0.11.10 was verified to resolve to `pdfminer.six==20260107` on Python 3.12.
`pytest-cov 6.2.1` accepts pytest 9.0.3.

npm suggested `typescript-eslint 8.36.0` to resolve the minimatch finding,
but that version requires TypeScript `<5.9.0` while this project pins 5.9.3.
Rather than force or downgrade TypeScript, the repository uses a single scoped
override for `@typescript-eslint/typescript-estree > minimatch` at 11.0.1.
This package is build/lint-only, its Node engine accepts the project Node 20
environment, and lint, TypeScript, build, and audit verification pass.

### Vite Exposure Reduction

The Docker frontend previously published `5173:5173` on all host interfaces,
and the local Vite configuration listened on `0.0.0.0`. Local `npm run dev`
now listens on `127.0.0.1`; Docker still listens on all interfaces *inside the
container* as required for container networking, but Compose publishes it only
as `127.0.0.1:5173:5173`. Local browser access remains available and the
binding change is defense in depth, not a substitute for dependency patching.

### Audit Results

Baseline results:

- Local `pip-audit --local`: 11 advisory rows across `pypdf`, `pytest`, and
  `pdfminer.six`.
- Complete `npm audit --audit-level=high`: 8 vulnerabilities (1 high, 7
  moderate). `npm audit --omit=dev --audit-level=high`: 0 vulnerabilities.
- Disposable API-image audit additionally found 12 advisory rows in base-image
  `pip 25.0.1`.

Final results:

- Local `.venv`: `No known vulnerabilities found` from
  `.venv\Scripts\python.exe -m pip_audit --local --format=columns`.
- Resolved API image: `No known vulnerabilities found` from a disposable
  `invoiceflowai-api` container after installing the audit tool only in that
  disposable container. The audit correctly skips the unpublished local
  `invoiceflow-api` distribution.
- Frontend complete tree: `found 0 vulnerabilities`.
- Frontend production-only tree: `found 0 vulnerabilities`.

### Verification

| Gate | Result |
|---|---|
| PDF extraction/evaluation regression subset | PASS - 17 passed |
| Full pytest | PASS - 260 passed, 14 skipped, 6 existing warnings |
| Ruff check / format | PASS |
| Backend and root Pyright | PASS - 0 errors, 0 warnings |
| `npm ci` | PASS - lockfile-consistent, 0 vulnerabilities |
| ESLint / TypeScript / Vite build | PASS |
| Docker Compose configuration | PASS |
| Rebuilt API image | PASS - `pip=26.2.2`, `pypdf=6.21.4` |
| API health and readiness | PASS - HTTP 200 |
| PostgreSQL / frontend / n8n availability | PASS |
| Frontend Docker publication | PASS - `127.0.0.1:5173` only |

No live Groq request or live E2E harness execution occurred. API and frontend
were rebuilt and recreated without recreating the database or n8n services;
their volumes and Milestone 7B evidence were preserved.

### Modified Files

- `backend/pyproject.toml`: patched production, test, and evaluation pins.
- `backend/Dockerfile`: patched the API image's pip tooling.
- `frontend/package.json` and `frontend/package-lock.json`: Vite upgrade and
  targeted nested minimatch resolution.
- `frontend/vite.config.ts` and `compose.yaml`: localhost-only frontend
  development exposure.

The pre-existing Milestone 7C typing changes remain intact in
`backend/app/services/business_validation/rules.py` and
`backend/tests/test_invoice_workflow.py`.

### Remaining Risks and Limitations

- The frontend has no component or browser-test framework configured; none was
  added in this dependency-only milestone.
- The six pytest warnings (FastAPI/httpx deprecation and unregistered
  integration markers) predate this remediation and do not fail the suite.
- HTTPS, production secret management, backups, monitoring, and a durable
  webhook outbox/retry mechanism remain deployment architecture work.

### Reproduction Commands

```powershell
Set-Location "D:\InvoiceFlow AI"
.venv\Scripts\python.exe -m pip install -e "backend[dev,evaluation]"
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check backend
.venv\Scripts\ruff.exe format --check backend
.venv\Scripts\pyright backend
.venv\Scripts\pyright
.venv\Scripts\python.exe -m pip_audit --local --format=columns
Set-Location frontend
npm ci
npm run lint
npm run build
npm audit --audit-level=high
npm audit --omit=dev --audit-level=high
Set-Location ..
docker compose config --quiet
docker compose build api frontend
docker compose up -d --no-deps api frontend
docker compose ps
```

### Final Status

**MILESTONE 7C - COMPLETE, QUALITY AND SECURITY GATES PASSED.**
