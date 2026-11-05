# Milestone 8B - Secure GitHub Actions CI Pipeline

## Status and Scope

The repository now contains a pre-first-push GitHub Actions workflow at
`.github/workflows/ci.yml`. It is CI-only: it validates code, dependencies,
browser behavior, and Compose configuration, but it does not deploy, publish
images, invoke Groq, call n8n, or modify application data.

The actual GitHub-hosted workflow has not run because no push was performed.
Local validation is evidence that the workflow is syntactically valid and that
its commands match the repository; it is not evidence of a successful GitHub
Actions execution.

## Workflow Architecture

The workflow triggers on:

- Pushes to `master`.
- Pull requests targeting `master`.
- Manual `workflow_dispatch` runs.

It uses `permissions: contents: read`, concurrency cancellation for superseded
runs, Ubuntu 24.04 runners, job timeouts, and no write-capable GitHub token
operations.

| Job | Responsibility |
|---|---|
| `backend-quality` | Install Python project dependencies; run Pytest, Ruff, and both Pyright scopes |
| `frontend-quality` | `npm ci`, ESLint, Vitest, coverage, and production build; upload coverage |
| `browser-e2e` | Install Chromium and Linux dependencies; run mocked Playwright suite; retain diagnostics |
| `security-audit` | Audit local Python dependencies, API runtime image, and both npm dependency scopes |
| `infrastructure-validation` | Validate Compose interpolation and service configuration with synthetic CI values |

The jobs are independent so a frontend or browser failure is visible without
waiting for unrelated quality work. No job starts the full Compose stack.

## Reproducible Environment

- Python: 3.12.7.
- Node.js: 20.20.2.
- Python dependencies: `python -m pip install -e "backend[dev,evaluation]"`.
- Frontend dependencies: `npm ci` from `frontend/` using the committed
  `package-lock.json`.
- Runtime Python dependencies: built from `backend/Dockerfile` and audited in
  a disposable container.

The Python project separates production dependencies from `dev` and
`evaluation` extras. The configured test suite uses isolated SQLite files by
default. PostgreSQL integration tests use `TEST_DATABASE_URL` only when a
caller supplies it, so CI preserves the existing intentional skips rather
than silently connecting to a shared database.

## Backend CI Commands

```powershell
python -m pytest -q
python -m ruff check backend
python -m ruff format --check backend
python -m pyright backend
python -m pyright
```

The historical local result is 260 passed, 14 skipped, and 6 warnings. The
workflow does not assert a fixed test count. The skipped tests remain
environment-dependent PostgreSQL and live-provider checks; no Groq key,
n8n credential, production database, or customer data is required.

## Frontend CI Commands

```powershell
Set-Location frontend
npm ci
npm run lint
npm test
npm run test:coverage
npm run build
npm run test:e2e
```

The frontend job uploads `frontend/coverage` for seven days. The browser job
uploads `frontend/test-results` and `frontend/playwright-report` when present,
also for seven days. These artifacts contain synthetic test data only.

Playwright uses the existing configuration from Milestone 8A:

- Chromium only.
- Dedicated Vite server at `127.0.0.1:4173`.
- One browser project and no arbitrary sleeps.
- Route interception before navigation.
- No backend, PostgreSQL, Groq, n8n, or external API calls.

## Security Audit Design

The security job runs:

```powershell
python -m pip_audit --local --format=columns
Set-Location frontend
npm audit --audit-level=high
npm audit --omit=dev --audit-level=high
```

It also builds the API image and runs `pip-audit` inside a disposable
`invoiceflow-api-ci` container. This distinguishes the local development and
evaluation environment from the actual API runtime dependency set. The local
unpublished `invoiceflow-api` distribution may be reported as skipped by
pip-audit; the workflow does not suppress that output or convert scan failures
to success.

No `npm audit fix --force`, vulnerability suppression, production credential,
personal access token, or secret is used. Compose validation receives only
synthetic CI values, including a CI-only database password.

The workflow uses maintained first-party action major tags (`checkout`,
`setup-python`, `setup-node`, and `upload-artifact`). Immutable commit-SHA
pinning should be considered when the repository is prepared for publication;
no unverifiable SHA values were invented during this pre-first-push milestone.

## Infrastructure Validation

The infrastructure job runs only:

```powershell
docker compose config --quiet
```

It does not start services, mount local volumes, run migrations, import n8n
workflows, or reset databases. The Compose file is evaluated with synthetic
`POSTGRES_*`, `DATABASE_URL`, `GROQ_API_KEY`, and `N8N_WEBHOOK_SECRET` values.

## Local Validation Evidence

| Check | Result |
|---|---|
| Workflow YAML parsed with PyYAML | PASS |
| actionlint | PASS, run in disposable `rhysd/actionlint` container |
| Backend Pytest | PASS - 260 passed, 14 skipped, 6 warnings |
| Ruff check and format | PASS |
| Backend/root Pyright | PASS - zero errors |
| Frontend Vitest | PASS - 10 passed |
| Frontend coverage | PASS - statements 69.23%, branches 69.23%, functions 60.34%, lines 88% |
| Playwright Chromium | PASS - 4 passed |
| ESLint, TypeScript, Vite build | PASS |
| npm audits | PASS - 0 vulnerabilities in full and production-only trees |
| Docker Compose config | PASS |
| `git diff --check` | PASS; only Windows line-ending notices |

The Vitest suite was repeated twice during Milestone 8A and remained stable.
The browser suite was run with the dedicated local server and mocked routes.

## Known Limitations

- GitHub-hosted execution remains pending until the first push.
- PostgreSQL-specific tests remain skipped unless a dedicated `TEST_DATABASE_URL`
  is supplied; this workflow does not add a database service.
- Linux runner behavior is represented by the actionlint and workflow design,
  but the complete GitHub-hosted environment cannot be proven locally.
- Action references use major tags rather than immutable SHAs pending the
  publication hardening review.
- Coverage is reported but has no universal threshold; the measured baseline
  is documented rather than used to conceal uncovered code.

## First GitHub Push Verification Checklist

After publication, verify the first run manually:

1. Confirm the repository default branch is `master`, or update the trigger
   before the first push if the repository policy differs.
2. Confirm the workflow appears under Actions and all five jobs start.
3. Confirm no secrets are requested and no external service credentials are
   present in logs.
4. Confirm backend skip counts match the expected environment-dependent tests.
5. Confirm the coverage and Playwright artifacts are downloadable and contain
   only synthetic data.
6. Confirm both npm audits and both Python audits complete with their real
   outputs.
7. Review the action versions and pin verified commit SHAs before accepting
   the repository as publicly published.

## Next Milestone

Milestone 8C should cover documentation, architecture presentation, and the
portfolio demonstration. Deployment and continuous delivery remain outside
this workflow and should be designed separately.
