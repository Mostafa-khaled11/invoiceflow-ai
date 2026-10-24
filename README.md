# InvoiceFlow AI

InvoiceFlow AI is a portfolio-grade foundation for an AI-assisted invoice
processing and workflow automation platform. This first runnable slice provides
the local API, React shell, PostgreSQL connectivity, and migration scaffolding.
Invoice extraction, uploads, authentication, approvals, workers, and n8n remain
later implementation phases.

## Tech stack

- Backend: Python 3.12+, FastAPI, Pydantic Settings, SQLAlchemy, Alembic
- Frontend: React 19, TypeScript, Vite
- Database: PostgreSQL 17
- Local orchestration: Docker Compose
- Quality: Pytest, Ruff, Pyright, ESLint, TypeScript

## Project structure

```text
.
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/health.py
│   │   ├── api/v1/endpoints/invoices.py
│   │   ├── core/config.py
│   │   ├── db/session.py
│   │   ├── models/invoice.py
│   │   ├── schemas/invoices.py
│   │   └── services/
│   ├── alembic/
│   ├── tests/
│   ├── Dockerfile
│   └── pyproject.toml
├── frontend/
│   ├── src/App.tsx
│   ├── package.json
│   └── Dockerfile
├── compose.yaml
├── .env.example
└── README.md
```

## Prerequisites

- Docker Desktop with Docker Compose v2
- Python 3.12+ for host-side backend checks
- Node.js 22+ and npm for host-side frontend checks

## Environment setup

Copy `.env.example` to `.env` and replace the local PostgreSQL password
placeholder. The password is used only by the local Compose database and API.
Do not use production credentials in this file.

```powershell
Copy-Item .env.example .env
```

If the password contains reserved URL characters, URL-encode it in
`DATABASE_URL`.

`INVOICE_STORAGE_PATH` selects the private directory where validated invoice
PDFs are stored. The default `storage` location resolves relative to the
working directory for host-side runs; the Compose `api` service overrides it
with an absolute path on the `invoice_uploads` named volume. The directory is
never served by static routes or exposed in API responses.

## Run locally with Docker

```powershell
docker compose up --build -d
docker compose ps
```

Open the frontend at <http://localhost:5173>. The API is available at
<http://localhost:8000>, and interactive API documentation is at
<http://localhost:8000/docs>.

Stop the services with:

```powershell
docker compose down
```

The PostgreSQL data remains in the named `postgres_data` volume. Remove it only
when intentionally resetting local database state:

```powershell
docker compose down -v
```

## Backend checks

Create a virtual environment and install the backend with development tools:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".\backend[dev]"
```

Run tests, lint, and type checks:

```powershell
python -m pytest backend
python -m ruff check backend
python -m ruff format --check backend
pyright backend
```

The PostgreSQL readiness integration test is skipped when `DATABASE_URL` is not
available to the test process. To run it against the Compose database from a
shell that has the environment loaded, use the database URL with `db` replaced
by `localhost`:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://invoiceflow:<local-password>@localhost:5432/invoiceflow"
python -m pytest backend/tests/test_ready_integration.py -m integration
```

## Frontend checks

```powershell
Set-Location frontend
npm install
npm run lint
npm run build
```

## API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/health` | Lightweight API liveness check |
| GET | `/api/v1/ready` | API plus PostgreSQL readiness check; returns 503 when unavailable |
| POST | `/api/v1/invoices/upload` | Validates and stores one PDF invoice (max 10 MB); returns 201 with stored metadata, or 409 when the same PDF content already exists |
| GET | `/api/v1/invoices/{invoice_id}` | Returns stored invoice metadata; 404 when unknown |

Duplicate prevention is content-based: the SHA-256 digest of the uploaded
PDF is unique at the database level, so uploading identical bytes again
returns a 409 conflict with the existing invoice id and stores no second
file. Different PDFs may freely reuse the same original filename.

Storage strategy: accepted PDFs are written to a private staging directory
next to `INVOICE_STORAGE_PATH`, then atomically renamed to
`<invoice_uuid>.pdf` inside the same transaction window in which the invoice
row is inserted. If anything fails before the commit, the transaction rolls
back and both the staged and the moved file are removed, so a rejected or
failed intake leaves neither a duplicate record nor an orphan file.
| POST | `/api/v1/invoices/upload` | Intake validation for one PDF invoice (max 10 MB); returns upload metadata, no persistence yet |

The health response is:

```json
{
  "status": "ok",
  "service": "invoiceflow-api"
}
```

## Troubleshooting

- If Compose reports missing variables, confirm that `.env` exists in the
  repository root and contains all values from `.env.example`.
- If port 5173 or 8000 is occupied, stop the conflicting process or change the
  host-side port mapping in `compose.yaml`.
- If `/api/v1/ready` returns 503, inspect `docker compose logs db api` and wait
  for the PostgreSQL health check to pass.
- If the frontend says the backend is unavailable, verify that the browser can
  reach <http://localhost:8000/api/v1/health> and that `VITE_API_BASE_URL` is
  the browser-visible API URL, not the internal Compose hostname.

## Current limitations

This release includes PDF upload intake with private storage, PostgreSQL
persistence, and SHA-256 duplicate prevention. It deliberately does not
include invoice extraction, validation rules, PDF download streaming,
authentication, authorization, worker processing, approval actions, or n8n
integration. Those features require the next design and implementation tasks
described in the project documents.
