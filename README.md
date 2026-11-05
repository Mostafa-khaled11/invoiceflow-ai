# InvoiceFlow AI

InvoiceFlow AI is an AI-assisted invoice-processing and human-review workflow
platform. It combines structured extraction, deterministic financial
validation, PostgreSQL-backed workflow state, a React review interface, and
best-effort n8n automation in a reproducible local development stack.

This repository is a portfolio and development project. It is not presented as
production-certified software.

## Problem and solution

Invoice processing is more than extracting fields from a PDF. A useful system
must preserve the source document, make uncertainty visible, validate financial
relationships, route exceptions to a person, and expose an auditable workflow.

The current implementation connects:

- Groq structured extraction behind a versioned prompt and schema boundary.
- Deterministic guardrails and the invoice-validation-v1 business ruleset.
- PostgreSQL persistence for invoices, extraction results, validation findings,
  workflow state, review cases, and append-only workflow events.
- A React/TypeScript review queue and review-detail workflow.
- Authenticated, post-commit n8n notifications for review-required and
  review-resolution events.
- Backend, frontend, browser, security, and infrastructure quality checks.

## Implemented capabilities

- PDF intake validation, private named-volume storage, and SHA-256 duplicate
  prevention.
- Deterministic digital text-layer extraction with pypdf; OCR is not included.
- Versioned structured invoice extraction with null-preserving safety behavior.
- Financial/business validation with persisted rule findings and decisions:
  PASS, WARNING, REVIEW_REQUIRED, and BLOCKED.
- Initial workflow routing into READY, NEEDS_REVIEW, or BLOCKED.
- Human review resolution through ACCEPT_EXCEPTION and REJECT, with idempotency
  and state-versioned history.
- Review queue and detail screens with loading, empty, error, retry, and
  resolution states.
- Optional n8n workflows for review-required notifications, resolutions, and
  aging review monitoring.

## Architecture at a glance

~~~mermaid
flowchart LR
    User[Operator] --> UI[React + TypeScript]
    UI -->|HTTP| API[FastAPI API]
    API --> Intake[Invoice intake and private storage]
    API --> Extract[PDF text and structured extraction]
    Extract --> Guardrails[Deterministic guardrails]
    Guardrails --> Validate[Business validation]
    Validate --> Workflow[Workflow routing and review]
    Intake --> DB[(PostgreSQL)]
    Validate --> DB
    Workflow --> DB
    Workflow -->|post-commit webhook| N8N[n8n automation]
    Extract -->|server-side only| LLM[Groq provider]
~~~

The detailed implementation map is in
[docs/architecture.md](docs/architecture.md). The original design proposal is
intentionally preserved in [ARCHITECTURE.md](ARCHITECTURE.md) as historical
project evidence.

## Technology stack

| Area | Technologies |
| --- | --- |
| Backend | Python 3.12+, FastAPI, Pydantic Settings, SQLAlchemy, Alembic |
| Frontend | React 19, TypeScript, Vite |
| AI boundary | Groq structured-output provider, versioned prompts and schema |
| Database | PostgreSQL 17 |
| Automation | n8n webhooks and scheduled workflow |
| Testing | Pytest, Vitest, React Testing Library, MSW, Playwright |
| Quality/security | Ruff, Pyright, ESLint, TypeScript build, pip-audit, npm audit |
| Local infrastructure | Docker Compose, named volumes |

## Quick start on Windows PowerShell

Prerequisites: Docker Desktop with Compose v2, Python 3.12+, and Node.js
20.19+ (or 22.12+) for host-side checks.

~~~powershell
Copy-Item .env.example .env
# Edit POSTGRES_PASSWORD and the matching DATABASE_URL password in .env.

docker compose up --build -d
docker compose ps
Invoke-WebRequest http://localhost:18000/api/v1/health
Invoke-WebRequest http://localhost:18000/api/v1/ready
~~~

Open the review UI at http://localhost:5173, API documentation at
http://localhost:18000/docs, and n8n at http://localhost:5678 when needed.
The browser-visible frontend setting is VITE_API_BASE_URL=http://localhost:18000;
the internal Compose API hostname is http://api:8000 and must not be used by
the browser.

Stop the local stack without deleting data:

~~~powershell
docker compose down
~~~

Use docker compose down -v only when intentionally resetting local database,
invoice-storage, and n8n data volumes.

### Optional n8n setup

Set N8N_ENABLED=true and a local N8N_WEBHOOK_SECRET in .env, start the stack,
complete n8n's local owner setup, and import the three JSON workflows in
n8n/workflows/. Configure an n8n Header Auth credential with header
X-InvoiceFlow-Webhook-Secret. Exported workflows contain no credentials or
secret values. FastAPI commits workflow state before attempting delivery;
delivery is best effort and there is currently no transactional outbox.

The live harness is dry-run by default. The previously verified live procedure
is documented in docs/milestones/7c-quality-gates.md and docs/demo.md. Do not
rerun it merely to record this demo.

## Development checks

Backend setup and checks from the repository root:

~~~powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\backend[dev,evaluation]"
python -m pytest -q
python -m ruff check backend
python -m ruff format --check backend
pyright backend
~~~

The full command includes both backend tests and the root-level evaluation
tests. The default tests use fake providers and isolated SQLite fixtures. They
do not call Groq, n8n, or customer services. PostgreSQL integration tests are
environment-dependent and remain skipped unless TEST_DATABASE_URL is provided
explicitly.

Frontend checks:

~~~powershell
Set-Location frontend
npm ci
npm run lint
npm test
npm run test:coverage
npx playwright install chromium
npm run test:e2e
npm run build
npm audit --audit-level=high
npm audit --omit=dev --audit-level=high
~~~

The browser suite uses synthetic fixtures and intercepted API routes on port
4173; it does not touch the Docker stack.

Infrastructure and Python security checks:

~~~powershell
Set-Location ..
python -m pip install pip-audit==2.10.1
docker compose config --quiet
python -m pip_audit --local --format=columns
~~~

See docs/milestones/8b-ci-pipeline.md for the matching GitHub Actions design.
The workflow is locally validated but has not run on GitHub until the first
push.

## API surface

All application endpoints are under /api/v1.

| Method | Path | Responsibility |
| --- | --- | --- |
| GET | /api/v1/health | Liveness without a database query |
| GET | /api/v1/ready | PostgreSQL readiness; returns 503 when unavailable |
| POST | /api/v1/invoices/upload | Validate and persist one PDF |
| GET | /api/v1/invoices/{id} | Read invoice metadata |
| POST | /api/v1/invoices/{id}/extract-text | Persist digital text extraction metadata |
| POST | /api/v1/invoices/{id}/extract-structured | Run configured structured extraction |
| POST | /api/v1/invoices/{id}/validate | Persist deterministic validation |
| GET | /api/v1/invoices/{id}/validation | Read current validation findings |
| POST | /api/v1/invoices/{id}/workflow/route | Create/read current workflow route |
| GET | /api/v1/invoices/{id}/workflow | Read current workflow state |
| POST | /api/v1/invoices/{id}/workflow/review | Resolve an open review case |
| GET | /api/v1/review-cases | List current or historical review cases |
| GET | /api/v1/review-cases/{id} | Read review and validation context |

## Project structure

~~~text
backend/app/                 FastAPI routes, services, models, schemas, prompts
backend/tests/               Backend, evaluation, integration, and safety tests
backend/alembic/             PostgreSQL migration history
frontend/src/                React UI, API client, unit/component tests
frontend/e2e/                Mocked Chromium workflows
evaluation/                  Synthetic benchmark tooling and diagnostics
n8n/workflows/               Exported local automation workflows
docs/                        Current architecture, demo, roadmap, milestones
.github/workflows/ci.yml     Pre-first-push quality and security pipeline
compose.yaml                 Local API, frontend, PostgreSQL, and n8n topology
~~~

## Security posture

- .env and other local environment files are ignored; .env.example has
  placeholders only. No provider key or webhook secret belongs in React,
  workflow exports, or Git.
- Accepted PDFs live in a private Docker named volume and are not served as
  static frontend files.
- Groq is a server-side provider boundary. Invoice text is passed as document
  data, not as executable instructions.
- n8n receives authenticated webhook requests and never accesses PostgreSQL
  directly.
- Dependency audits run for local Python dependencies, the API runtime image,
  and both full and production-only npm trees.
- The local frontend is bound to loopback by Compose. This stack does not yet
  provide HTTPS, user authentication, authorization, or production monitoring.

## Latest verified local evidence

The latest local evidence recorded in the milestone reports is:

- Backend and evaluation suite: 260 passed, 14 skipped, 7 warnings in the
  current release-candidate run.
- Frontend: 10 Vitest tests passed; 4 Chromium Playwright tests passed.
- Frontend coverage: statements 69.23%, branches 69.23%, functions 60.34%,
  lines 88.00%.
- Ruff, Pyright, ESLint, TypeScript/Vite build, npm audits, Python audits, and
  Compose validation: PASS.
- Milestone 7B n8n workflow: live-verified previously; the current CI workflow
  has not yet executed on GitHub.

These are local development results, not a claim of production certification
or remote CI success. Detailed evidence remains in
docs/milestones/7c-quality-gates.md, 8a-frontend-testing.md, and
8b-ci-pipeline.md.

## Known limitations

- n8n delivery is post-commit best effort; there is no durable transactional
  outbox or persistent retry queue.
- The Docker topology is local-development focused and has no HTTPS, auth,
  production monitoring, or deployment configuration.
- Playwright currently targets Chromium only.
- OCR is intentionally not implemented; PDFs without adequate digital text are
  classified for future OCR work.
- The current frontend is a review workspace, not a complete invoice-upload
  product.
- AI extraction evaluation remains a synthetic/offline engineering tool rather
  than a claim of measured production accuracy.

## Roadmap

The post-release direction is InvoiceFlow AI → Intelligent Invoice Data
Platform, with Data Engineering first: analytics foundations and quality, then
BI, AI extraction evaluation, duplicate/anomaly detection, MLOps, and only
later predictive intelligence when suitable historical data exists. See
docs/roadmap.md.

## Portfolio walkthrough

The concise 3–5 minute demo script is in docs/demo.md. It uses synthetic,
mocked frontend data and previously verified n8n evidence; it does not require
live provider calls or data mutation.

Synthetic portfolio captures are available in
[docs/assets](docs/assets): [review queue](docs/assets/review-queue.png) and
[review detail](docs/assets/review-detail.png).

## License

This project is released under the MIT License. See [LICENSE](LICENSE).
