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
- Node.js 20.19+ (or 22.12+) and npm for host-side frontend checks

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

The digital-text extraction baseline is configured with `MAX_INVOICE_PAGES=50`
and `MIN_EXTRACTED_TEXT_CHARS=50`. A PDF with enough non-whitespace text is
classified as `text_extracted`; insufficient digital text is classified as
`needs_ocr` for a later OCR task. No OCR is performed by this PDF text-layer
stage.

Structured extraction is configured for Groq with
`GROQ_MODEL=openai/gpt-oss-120b`, prompt version `invoice-extraction-v1`, and
schema version `invoice-schema-v1`. `GROQ_API_KEY` is server-side only and must
never be placed in React or committed to Git. Requests use non-streaming strict
JSON Schema output with temperature 0. Invoice text is passed as untrusted
document data, separate from the system prompt.

The structured-extraction input limit is `MAX_LLM_INPUT_CHARS=20000`; oversized
text is rejected and persisted as `llm_input_too_large` rather than truncated.
Transient provider failures retry at most `LLM_MAX_RETRIES=2` times with
exponential backoff. Successful results are idempotent for the same text
extraction, provider, model, prompt version, and schema version. Missing values
remain `null`. The schema covers vendor identity, invoice number/dates, purchase
order, currency, subtotal/discount/tax/shipping/total, line items, and a small
controlled warning-code list.

Successful structured extractions are then checked by the deterministic
Business Validation Engine (ruleset `invoice-validation-v1`; see
`docs/business-validation-v1.md`). Validation is triggered explicitly through
the validate endpoint, reads only the persisted structured data, and stores its
run and findings separately; it never changes the extracted values.

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

### Local n8n automation

The optional n8n integration is disabled by default. Set `N8N_ENABLED=true`,
provide `N8N_WEBHOOK_SECRET`, and start the stack with `docker compose up -d`.
n8n is available at <http://localhost:5678>; complete its first-run owner setup,
then import the three JSON workflows from `n8n/workflows/`. FastAPI sends
post-commit events to n8n over the Compose network at `http://n8n:5678` and n8n
queries the review API at `http://api:8000`. n8n never receives database
credentials and never accesses PostgreSQL directly.

Configure an n8n Header Auth credential named `InvoiceFlow Webhook Secret` for
the two webhook workflows, using header `X-InvoiceFlow-Webhook-Secret` and the
same local value as `N8N_WEBHOOK_SECRET`; exported workflow JSON intentionally
contains no credential or secret values.

The Review Required and Review Resolution workflows use n8n execution output
as the local demonstration sink. The Aging Review Monitor runs every 15 minutes
and filters open, current review cases using `AGING_REVIEW_THRESHOLD_MINUTES`.
Automation delivery is best effort: business state is committed first, and a
delivery failure is logged without changing the API response. Persistent retry
and an outbox are future production enhancements.

### Live backend-to-n8n verification

The deterministic harness is dry-run by default. From the repository root:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m app.scripts.live_e2e
..\.venv\Scripts\python.exe -m app.scripts.live_e2e --execute --output ..\.runtime\live-e2e\evidence.json
```

The first command performs no writes or webhook calls. The explicit
`--execute` command creates uniquely identified synthetic invoices through the
existing validation and workflow services, exercises Review Required,
ACCEPT_EXCEPTION, REJECT, and idempotent replay, then writes redacted evidence.
It never deletes records, resets migrations, truncates tables, or calls Groq.
After execution, open n8n **Executions** and match each evidence `event_id` to
the corresponding webhook execution payload. An HTTP 200 alone is only
`WEBHOOK_ACCEPTED`, not proof of `N8N_EXECUTION_VERIFIED`.

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

The backend test configuration intentionally uses `pythonpath = [".", ".."]`.
The first entry resolves the backend `app` package; the second resolves the
repository-level `evaluation` package and shared root test helpers when the
backend suite is run from `backend/`. This keeps root and backend test commands
reproducible without duplicating those packages.

The default test suite uses a fake provider and does not call Groq. An optional
synthetic live smoke test can be run once, only when explicitly enabled:

```powershell
$env:RUN_LIVE_LLM_TESTS = "1"
$env:GROQ_API_KEY = "<your-local-key>"
python -m pytest backend/tests/test_live_groq.py -m integration
```

Never use confidential invoice text for this test.

The PostgreSQL readiness integration test is skipped when `DATABASE_URL` is not
available to the test process. The standard Compose file keeps PostgreSQL on
the internal Compose network, so host-side integration tests require either an
existing local PostgreSQL instance or a temporary local port mapping. With the
database mapped to host port `15432`, use:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://invoiceflow:<local-password>@127.0.0.1:15432/invoiceflow"
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
| POST | `/api/v1/invoices/{invoice_id}/extract-text` | Extracts and persists PDF digital-text metadata; does not return raw text |
| POST | `/api/v1/invoices/{invoice_id}/extract-structured` | Extracts versioned structured invoice data through Groq; does not return raw text or prompts |
| POST | `/api/v1/invoices/{invoice_id}/validate` | Runs the deterministic `invoice-validation-v1` ruleset against the latest successful structured extraction and persists run plus findings; idempotent (200); 404 unknown invoice, 409 without successful extraction |
| GET | `/api/v1/invoices/{invoice_id}/validation` | Returns the persisted validation run with all findings for the canonical structured extraction; 404 when not validated, 409 without successful extraction |

### Business validation

`POST /api/v1/invoices/{invoice_id}/validate` requires a **successful
structured extraction** for the invoice (the latest one by creation time is
selected). If none exists, the API returns `409` with code
`structured_extraction_required` — it never triggers PDF extraction, structured
extraction, or Groq as a side effect of validation.

Validation is **idempotent** per `structured_extraction_id + ruleset_version`:
calling the endpoint again returns the already persisted run (same validation
id, same findings) instead of creating duplicates. This is enforced by a unique
database constraint, so concurrent requests cannot create two runs for the
same extraction and ruleset.

The GET endpoint first selects the latest successful structured extraction
(`created_at DESC`, then extraction ID DESC) and returns only the validation for
that exact extraction and current ruleset. If a newer successful extraction
exists but has not been validated, it returns `404 validation_not_found`; it
does not fall back to an older validation. Historical validation rows remain
stored for audit purposes.

The persisted decision (`final_decision`) is one of `PASS`, `WARNING`,
`REVIEW_REQUIRED`, or `BLOCKED`. Decision semantics are preserved exactly as
defined by the engine: **`WARNING` does not mean human review is required** —
for example, summary-tax invoices legitimately produce a `WARNING` because tax
consistency (`FIN004`) is not evaluable without line-item taxes. Only
`REVIEW_REQUIRED` represents a result that needs human review, and `BLOCKED`
remains reserved for the blocking conditions defined by the engine.

Validation is deterministic, performs **no LLM/provider calls**, and never
modifies the structured extraction it validates; every rule result is stored
as a separate finding row (rule id/name, status `PASS`/`FAIL`/`NOT_EVALUABLE`,
severity, message, actual/expected/difference values, affected fields, and
rule metadata) and returned by both endpoints.

Validation rows currently use cascading foreign keys from invoices and
structured extractions. If invoice deletion is introduced in a later task,
those cascades will also delete validation audit history and must be reviewed
before adding that feature.

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
persistence, SHA-256 duplicate prevention, deterministic pypdf text-layer
extraction, Groq-backed structured extraction with application format
validation, and deterministic business validation with persisted runs and
findings (`invoice-validation-v1`). It deliberately does not include OCR, PDF
download streaming, authentication, authorization, worker processing, a human
review queue, approval actions, or n8n integration. Those features require
later implementation tasks described in the project documents.
