# InvoiceFlow AI — Implementation Architecture

This document describes the architecture that exists in the repository today.
The root ARCHITECTURE.md is retained as the earlier design proposal and is not
silently rewritten as implementation evidence.

## System context

InvoiceFlow is a local-development modular monolith. FastAPI owns application
decisions and persistence; PostgreSQL is the durable source of truth; React is
the operator interface; n8n is an optional automation boundary; and Groq is an
external extraction provider.

~~~mermaid
flowchart TB
    Operator[Operator] --> Browser[React review workspace]
    Browser -->|localhost:18000| API[FastAPI /api/v1]
    API --> DB[(PostgreSQL 17)]
    API --> Files[(Private invoice_uploads volume)]
    API --> Text[InvoiceTextExtractor / pypdf]
    Text --> Structured[Structured extraction service]
    Structured --> Guardrails[Shared deterministic guardrails]
    Structured -->|server-side provider call| Groq[Groq]
    Guardrails --> Validation[Business Validation Engine]
    Validation --> Workflow[Workflow routing + human review]
    Workflow --> DB
    Workflow -->|authenticated post-commit event| N8N[n8n]
~~~

Compose exposes the API as host port 18000 while the API listens on 8000 inside
its container. The frontend uses the host-visible URL; n8n uses http://api:8000
on the internal Compose network.

## Backend architecture

The backend is organized as a modular monolith:

- app/api/v1/endpoints adapts HTTP requests to application services.
- invoice_files.py, invoice_intake.py, and invoice_storage.py validate uploads,
  prevent duplicate PDF content, and persist files with staging and atomic
  rename behavior.
- invoice_text_extraction.py uses pypdf to classify the digital text layer.
- invoice_llm.py is the provider boundary. Prompt selection comes from the
  registry and the provider returns structured candidate data.
- structured_extraction_guardrails.py applies shared deterministic monetary and
  labeled-date safety behavior before StructuredInvoiceData validation.
- structured_invoice_extraction.py persists versioned extraction results, raw
  provider output, errors, and attempt metadata.
- business_validation evaluates deterministic financial and completeness rules
  and invoice_validation.py persists every finding.
- invoice_workflow.py routes validation decisions, writes workflow events,
  creates review cases, and resolves human decisions under the invoice lock.
- review_cases.py provides current and historical operational read models.
- services/integrations/n8n.py delivers newly-created workflow events after the
  database commit.

The API currently exposes intake, extraction, validation, workflow, review
queue, and health/readiness routes. There is no separate worker process in the
current Compose topology; extraction and validation endpoints are synchronous
application operations.

## Persistence and migrations

PostgreSQL stores invoice metadata, structured and text extraction records,
validation runs and findings, workflow rows, review cases, and append-only
workflow events. Original PDF bytes are stored in the private
invoice_uploads named volume, not in PostgreSQL or the frontend image.

Alembic migrations in backend/alembic/versions define schema evolution.
Workflow identity is tied to a validation run, while review cases are tied to a
workflow. Current reads select the current validation context and distinguish
historical review cases from actionable ones.

## AI and deterministic boundaries

The external model proposes structured invoice fields; it does not own truth,
financial arithmetic, workflow transitions, or review decisions. The pipeline is:

~~~mermaid
sequenceDiagram
    participant API as FastAPI
    participant LLM as Groq provider
    participant G as Shared guardrails
    participant S as StructuredInvoiceData
    participant V as Validation engine
    participant DB as PostgreSQL
    API->>LLM: Send invoice text + selected prompt/schema
    LLM-->>API: Raw structured provider response
    API->>G: Apply current monetary/date guardrails
    G->>S: Validate final structured candidate
    S->>DB: Persist successful extraction and raw output
    API->>V: Explicit validation request
    V->>DB: Persist findings and decision
~~~

Null values are preserved when evidence is absent or ambiguous. The business
validation engine reads persisted structured data and never calls Groq or
changes extracted values.

## Workflow and human review

Initial routing maps PASS and WARNING to READY, REVIEW_REQUIRED to NEEDS_REVIEW,
and BLOCKED to BLOCKED. A REVIEW_REQUIRED route creates one open review case and
one initial ROUTED event. A reviewer can resolve an actionable case as
ACCEPT_EXCEPTION or REJECT; each resolution increments the state version and
writes an event. Repeating the same resolution request ID is idempotent;
reusing it with a different decision is rejected.

~~~mermaid
stateDiagram-v2
    [*] --> READY: PASS / WARNING
    [*] --> NEEDS_REVIEW: REVIEW_REQUIRED
    [*] --> BLOCKED: BLOCKED
    NEEDS_REVIEW --> EXCEPTION_ACCEPTED: Accept Exception
    NEEDS_REVIEW --> REJECTED: Reject
~~~

PostgreSQL locks coordinate current invoice workflow changes. The state change
is committed before n8n delivery is attempted, so a webhook failure does not
roll back a valid business decision. This is best-effort delivery, not
exactly-once delivery, and there is no transactional outbox yet.

## n8n boundary

FastAPI owns workflow state and sends an authenticated JSON event to one of two
webhook paths: review-required or review-resolved. n8n can notify or monitor
through the imported workflows but does not write PostgreSQL directly and does
not decide financial validation outcomes. The aging monitor reads the review
API. n8n is optional for core application tests and is disabled by default.

## Frontend

The React application is a focused operations workspace. It provides review
queue filtering, review detail, validation context, Accept Exception and Reject
confirmation flows, invoice lookup/detail, and explicit loading, empty, and
error states. src/api.ts is the single HTTP client and reads
import.meta.env.VITE_API_BASE_URL; local Compose resolves it to
http://localhost:18000.

## Testing architecture

~~~mermaid
flowchart TB
    Backend[Pytest: services, API, persistence, evaluation] --> Local[Local quality gates]
    Components[Vitest + RTL + MSW: UI and API client] --> Local
    Browser[Playwright Chromium: mocked browser workflows] --> Local
    Live[Milestone 7B: live Docker/n8n verification] --> Evidence[Historical evidence]
    Local --> CI[GitHub Actions design]
~~~

The frontend browser suite intercepts every API call and uses synthetic data. The
backend default suite uses fake providers and isolated fixtures. The live n8n
harness is separate and must be explicitly executed; it is not part of ordinary
tests.

## CI architecture

~~~mermaid
flowchart LR
    Event[GitHub push / PR / manual run] --> B[backend-quality]
    Event --> F[frontend-quality]
    Event --> E[browser-e2e]
    Event --> S[security-audit]
    Event --> I[infrastructure-validation]
~~~

.github/workflows/ci.yml contains these five jobs. Local YAML parsing and
command-equivalent checks pass; actionlint is not installed in the current
Windows environment. The initial hosted run
exposed a clean-checkout benchmark bootstrap gap and a cross-platform
storage-key issue; Milestone 8E.2 fixed both and hosted CI Run #2 passed all
five jobs. Release-candidate commits still require their own hosted run.

## Trust and release boundaries

Secrets are loaded from local environment configuration and ignored by Git.
Groq keys stay in the backend process. PDF storage is private to the API volume.
The current stack is intentionally local-development focused: it has no
authentication/authorization layer, HTTPS, durable outbox, production
monitoring, or deployment manifests.
