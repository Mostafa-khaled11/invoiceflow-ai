# InvoiceFlow AI — Project Requirements

**Document ID:** IF-PRD-001  
**Version:** 0.1.0 (Requirements baseline)  
**Status:** Approved for architecture planning  
**Project type:** Portfolio-grade AI-powered invoice processing and workflow automation  
**Language of supported invoices:** English only (MVP and v1.0)

> This document defines **what** InvoiceFlow AI must do and how its behavior will be accepted. System diagrams, API endpoint shapes, table-level database design, component deployment topology, and vendor/model selection are deliberately deferred to Tasks 1.2 and 1.3. An accepted requirement does not mean the feature has been implemented.

## 1. Business problem

Finance operations frequently involve manually receiving vendor invoices, transcribing their data, verifying amounts, identifying duplicates, requesting approval, and recording the outcome. Manual processing can introduce errors, delays, and duplicate records.

**Proposed solution:** InvoiceFlow AI ingests invoices, extracts structured information, validates it, detects duplicates, routes exceptions for correction, requires human approval for final decisions, and tracks the lifecycle in a dashboard. The project will initially use a fictional single-company setting and synthetic test invoices. The illustrative business volume of 100 invoices/day is a scenario, **not** a measured result or capacity guarantee.

### 1.1 Product goals

- Demonstrate a reliable end-to-end document-processing flow from submission to auditable approval/rejection.
- Reduce repetitive manual transcription without silently accepting uncertain or inconsistent extracted fields.
- Show measurable extraction quality, workflow correctness, error handling, and processing latency.
- Produce a reproducible, technically explainable engineering portfolio project.

### 1.2 Target users

- **Operator:** Submit invoices, inspect processing results, correct extracted values, and submit validated records for approval.
- **Approver:** Review invoices awaiting a decision and explicitly approve or reject them. Approval/rejection must be authorized and auditable.

The exact authentication implementation is a later design decision; authorization must be enforced by the backend, not only hidden in the React UI.

## 2. Approved product decisions

| Area | Decision | Rationale / boundary |
| --- | --- | --- |
| Backend | Python + FastAPI | Central home for domain rules and persistence logic |
| Workflow automation | n8n | Orchestration and integration, not the primary database or domain-rule authority |
| Frontend | React + TypeScript | Operator/approver web interface |
| Database | PostgreSQL | Source of truth for invoice records, statuses, and audit events |
| File support | PDF, maximum 10 MB, one invoice per PDF | Constrained, testable scope |
| Language | English invoices only | Minimize OCR and evaluation complexity initially |
| Documents in v0.1 | Text-based PDF | First end-to-end working slice |
| Documents added in v1.0 | Scanned/image-based PDF via OCR | Iterative complexity increase |
| Tenancy | One fictional company | No multi-tenant SaaS complexity |
| Currency | Preserve source currency; no conversion | Avoid external FX behavior |
| Processing mode | Manual React upload first | Establish core logic before external integrations |
| Automation integration | n8n and Gmail in v1.0 | Add after the core flow is validated |
| Approval | Always human for final approve/reject | No automated financial authorization |
| Development data | Synthetic/test invoices only | Avoid handling real confidential financial records |
| Development environment | Docker-based local demo | Reproducible setup before optional hosting |
| Agent design | Standard extraction pipeline first | LangGraph, RAG, or multiple agents only if justified later |

## 3. Release boundaries

### 3.1 MVP v0.1 — first end-to-end working release

The MVP is complete when a user can:

1. Upload an eligible English, text-based PDF in React.
2. Receive a persistent document identifier and visible processing status.
3. Obtain AI-extracted core invoice fields with missing/uncertain values flagged rather than fabricated.
4. Run deterministic field and arithmetic checks and duplicate detection.
5. Inspect the record in React, correct fields, and resubmit for validation.
6. Route a valid record to an authorized approver, who can approve or reject it.
7. See the final status and a minimal change/decision history saved in PostgreSQL.
8. See actionable error states if processing fails; retry must not create another payable record.

Basic authentication/authorization, validation, duplicate prevention, and auditability are **MVP requirements**, not optional post-MVP polish.

### 3.2 v1.0 — automation/reliability/portfolio-ready release

- OCR support for English scanned PDF invoices.
- n8n-triggered workflow runs and a Gmail test-inbox integration.
- Status/error notifications.
- Explicit retry/failure handling across integration boundaries.
- Expanded tests, CI, benchmark evaluation, and reproducible deployment instructions.
- Operations/quality metrics in the dashboard, with a GitHub README, diagrams, and a demo.

### 3.3 Out of scope (both initial releases)

- Financial payments, bank transfers, or autonomous payment authorization.
- Production ERP/accounting write-back or real vendor onboarding.
- Multi-company tenancy, complex RBAC, or enterprise SSO.
- Arabic, handwriting, non-PDF document formats, and multi-invoice PDF splitting.
- Foreign exchange conversion, accounting/tax advice, and regulatory compliance guarantees.
- Predictive analytics, RAG knowledge bases, or multi-agent orchestration without a proven requirement.
- Publishing or training on real confidential invoices.

## 4. Functional requirements

**Priority semantics:** `MUST (v0.1)` is required for the first end-to-end MVP; `MUST (v1.0)` is required for the later automation release; `LATER` is excluded unless explicitly re-scoped.

| ID | Requirement | Priority | Description |
| --- | --- | --- | --- |
| FR-01 | Invoice upload | MUST (v0.1) | Accept one eligible PDF per submission and create a persistent ID/status. |
| FR-02 | Core AI extraction | MUST (v0.1) | Extract fields into a validated, typed structure; support nullable/unknown values. |
| FR-03 | Business validation | MUST (v0.1) | Check required fields, dates, currency, amount consistency, and report issues. |
| FR-04 | Duplicate prevention | MUST (v0.1) | Detect exact-file resubmissions and probable business duplicates. |
| FR-05 | Human review/correction | MUST (v0.1) | Allow an operator to inspect and correct extracted fields and rerun validation. |
| FR-06 | Human decision | MUST (v0.1) | Require authorized approve/reject actions, each recorded in an audit log. |
| FR-07 | Persistent records | MUST (v0.1) | Store invoice data, status, validation flags, processing metadata, and history. |
| FR-08 | React dashboard | MUST (v0.1) | Show list, status, detail, correction, and decision views appropriate to role. |
| FR-09 | Basic error handling | MUST (v0.1) | Expose failed states and safe controlled reprocessing. |
| FR-10 | Backend authorization | MUST (v0.1) | Protect access and enforce operator/approver permissions on the API. |
| FR-11 | Scanned-PDF OCR | MUST (v1.0) | Extract text from scanned English PDFs; preserve processing diagnostics. |
| FR-12 | n8n orchestration | MUST (v1.0) | Trigger/coordinate ingestion through APIs; do not duplicate domain logic. |
| FR-13 | Gmail integration | MUST (v1.0) | Ingest eligible invoices from a dedicated test mailbox without duplicates. |
| FR-14 | Notifications | MUST (v1.0) | Notify relevant test users on review/decision/error events. |
| FR-15 | Evaluation and metrics | MUST (v1.0) | Report extraction accuracy, processing success, duplicates, latency, review rate, and cost. |
| FR-16 | Integration reliability + CI | MUST (v1.0) | Retry/backoff where safe, actionable logging, automated tests and CI. |
| FR-17 | Multi-agent/RAG/ERP | LATER | Only via a later change proposal grounded in product needs. |

## 5. Invoice data contract (conceptual)

### 5.1 Core fields for extraction

- `invoice_number`: original and normalized value (string; required to move to approval).
- `vendor_name`: original and normalized value (string; required to move to approval).
- `invoice_date`: normalized date (`YYYY-MM-DD`; required to move to approval).
- `currency`: normalized three-letter currency code when identifiable (required to move to approval).
- `subtotal`: decimal amount (needed for the initial arithmetic consistency rule).
- `tax_amount`: decimal amount (needed for the initial arithmetic consistency rule).
- `total_amount`: decimal amount (required to move to approval).

Optional extraction fields include due date, line items, discount, and shipping/other adjustments. A missing monetary component must **not** automatically be interpreted as zero. Arithmetic validation should run when all applicable components are known; otherwise flag the invoice for operator review.

Monetary values must use decimal-safe representations, never binary floating point for business calculations. Preserve the source text/trace needed to explain corrections and validation issues when practical.

### 5.2 Validation rules

- Missing, malformed, or ambiguous essential fields -> `NEEDS_REVIEW`, not a fabricated value.
- Dates must be parseable and normalized; unusual/inconsistent dates are flagged.
- Amount fields must be numeric; negative/credit-note behavior is out of the MVP scope and must be flagged rather than silently reinterpreted.
- Arithmetic checks consider subtotal, tax, and any documented discounts/extra charges. Tolerances must respect currency precision and be recorded as explicit rules in implementation.
- Validation outcome is separate from invoice lifecycle status; a successfully parsed JSON schema does **not** establish factual correctness.

### 5.3 Duplicate semantics

- **Exact file duplicate:** matching content checksum must not create a second active invoice; record an ingestion attempt/duplicate event as appropriate.
- **Business duplicate:** same normalized vendor and invoice number is a potential duplicate even if a different PDF was uploaded; block creating a second payable record and flag it for review.
- Duplicate prevention must remain correct on retries and near-simultaneous submissions through a database-backed uniqueness/idempotency mechanism.
- Do not silently delete existing invoice records or their decision history.

## 6. Invoice lifecycle and review policy

The precise database schema and transition API are designed in Task 1.3. The conceptual states are:

- `RECEIVED` — accepted submission.
- `PROCESSING` — extraction/validation is underway.
- `NEEDS_REVIEW` — missing/uncertain data or validation failure requires operator correction.
- `PENDING_APPROVAL` — validation passed and awaits an approver.
- `APPROVED` — explicitly approved by a permitted user.
- `REJECTED` — explicitly rejected by a permitted user with a reason.
- `FAILED` — a processing failure occurred with a recorded cause and controlled retry path.
- `DUPLICATE` — submission recognized as a duplicate; no new active payable record created.

Typical path: `RECEIVED -> PROCESSING -> PENDING_APPROVAL -> APPROVED|REJECTED`.

Exception paths: `PROCESSING -> NEEDS_REVIEW`, `PROCESSING -> FAILED`, or duplicate-detection outcome; `NEEDS_REVIEW -> PENDING_APPROVAL` only after correction and revalidation. `FAILED -> PROCESSING` is allowed only through an explicit safe retry operation. No approval from `RECEIVED`, `PROCESSING`, `FAILED`, `DUPLICATE`, or unresolved `NEEDS_REVIEW`.

The **Operator** can submit, review, and correct; the **Approver** can decide on records that are `PENDING_APPROVAL`. Every correction, retry, approval, rejection, and status transition must have a timestamp and traceable actor/source. The Approver cannot bypass validation by changing a flag in the UI.

## 7. Acceptance criteria

Acceptance tests are expressed as observable input -> expected behavior. The MVP cannot be declared complete until AC-01 through AC-12 are met. v1.0 adds AC-13 through AC-17. Detailed test fixtures will be created in implementation phases.

| ID | Release | Given / When | Then |
| --- | --- | --- | --- |
| AC-01 | v0.1 | A permitted operator uploads an English, text-based PDF <= 10 MB containing one invoice. | A persistent document ID is returned and an auditable `RECEIVED` state is created. |
| AC-02 | v0.1 | A file is not a valid PDF, is corrupted/protected beyond support, contains multiple invoices, or exceeds 10 MB. | The request/processing is rejected or flagged clearly, with no accidental approval; errors are understandable. |
| AC-03 | v0.1 | A valid, legible, text-based test invoice is processed. | The system returns structured extracted fields, plus explicit unknowns/issues, and persists the output. |
| AC-04 | v0.1 | The invoice has missing/ambiguous essential fields. | The invoice is marked `NEEDS_REVIEW`; the system does not invent values or auto-approve. |
| AC-05 | v0.1 | Known subtotal/tax/adjustment components disagree with the total beyond a documented currency-aware tolerance. | A validation issue is shown and approval is blocked until resolved. |
| AC-06 | v0.1 | The same PDF is uploaded twice. | No second active payable invoice is created; duplicate detection is visible and auditable. |
| AC-07 | v0.1 | A different PDF carries the same normalized vendor and invoice number. | The potential business duplicate is flagged/blocked rather than silently accepted as a new payable invoice. |
| AC-08 | v0.1 | An operator corrects a field on an invoice requiring review. | The old/new values and actor/time are recorded; validation reruns before approval routing. |
| AC-09 | v0.1 | An authenticated approver approves or rejects a `PENDING_APPROVAL` invoice. | Its final status, actor, timestamp, and rejection reason (if rejected) are saved and displayed. |
| AC-10 | v0.1 | A user without approver permission attempts a decision through the backend API. | The action is denied without changing the invoice status, regardless of UI controls. |
| AC-11 | v0.1 | AI extraction fails, times out, or a retry is requested. | Error/correlation information is recorded; retry cannot duplicate a payable record. |
| AC-12 | v0.1 | An authenticated operator/approver views the dashboard. | Records and permitted actions appear by role, including status, fields, validation issues, and decision history. |
| AC-13 | v1.0 | A supported scanned English PDF is submitted. | OCR output enters extraction/validation with visible diagnostics and no unsupported silent acceptance. |
| AC-14 | v1.0 | An eligible synthetic invoice arrives in the dedicated test mailbox. | n8n triggers ingestion via the backend; repeat triggers do not create duplicate payable records. |
| AC-15 | v1.0 | A review/decision/processing-error event occurs. | The configured test recipient receives a relevant notification and the event is traceable. |
| AC-16 | v1.0 | The evaluation fixture set is processed. | A reproducible report presents extraction scores, sample size, failures, time, cost, review rate, and methodology. |
| AC-17 | v1.0 | A developer follows the README on a clean local setup and runs CI checks. | The documented demo starts with required configuration and automated tests report pass/fail. |

## 8. Non-functional requirements

| ID | Quality attribute | Requirement |
| --- | --- | --- |
| NFR-01 | Security | Validate upload content/type/size, restrict file access, protect endpoints, and enforce permissions server-side. |
| NFR-02 | Secrets | Keep credentials in environment-based secret configuration; do not commit live keys or invoices containing real confidential data. |
| NFR-03 | Reliability | Maintain explicit processing states, avoid silent data loss, and make retries safe/idempotent. |
| NFR-04 | Observability | Link submission ID, processing job ID, status changes, and integration attempts through structured logs/records. |
| NFR-05 | Maintainability | Keep domain rules in FastAPI; keep n8n for orchestration and integrations; isolate AI provider interfaces. |
| NFR-06 | Testability | Use unit and integration tests for upload, parsing, validation, duplicates, authorization, and state transitions. |
| NFR-07 | Reproducibility | Document local configuration and startup; use Dockerized services where appropriate. |
| NFR-08 | Responsible AI | Treat model output as untrusted; enforce schema/business validation and human review when uncertain. |
| NFR-09 | Measurement | Instrument latency, errors, model usage/cost, and manual correction/review without claiming unmeasured improvements. |

## 9. Evaluation and success measurement

Before publishing performance claims, create a labeled **synthetic English invoice** fixture set. An initial evaluation size of at least 30 varied invoices is a planning minimum, not a statistical guarantee; broaden and stratify it to support the claims eventually made. Keep development/debug examples separated from final evaluation fixtures where feasible.

Track:

1. **Field-level extraction accuracy / precision / recall / F1:** with field normalization policy and explicit missing values.
2. **Invoice processing success rate:** successful terminal outcomes divided by eligible attempts, with errors broken down by category.
3. **Manual review and correction rate:** share routed to review and corrections per invoice.
4. **Duplicate prevention:** exact-file and business-key scenarios, including retry/concurrency tests.
5. **Latency:** average and P95 end-to-end processing time, split by upload/extraction/approval waiting when relevant.
6. **AI cost per processed invoice:** model/token usage and estimated provider cost; label estimates clearly.
7. **Time saved:** only with a measured manual-process baseline and the same defined workload.

**Numerical accuracy and latency targets: TBD after the first benchmark baseline** (Phase 6). Passing JSON/schema validation is not equivalent to extraction accuracy. No invented percentages should be used in the CV, README, or screenshots.

## 10. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| LLM hallucinates invoice fields | Schema validation, nullable fields, arithmetic checks, human correction. |
| PDF parsing differs by layout | Controlled corpus; begin with text PDFs; add OCR and expand fixtures later. |
| Duplicate triggered by retry/Gmail re-delivery | Content checksums, business-key checks, transaction-safe idempotency. |
| Role bypass through direct API | Server-side authentication and authorization tests. |
| Integration downtime/rate limits | Traceable errors, controlled retries and user-visible failures. |
| Scope creep from agents/features | v0.1/v1.0 boundaries; all new scope requires change review. |
| Benchmark overclaims | Labeled fixtures, documented methodology, transparent limitations. |

## 11. Phase 1 / Task 1.1 — Definition of Done

- [x] Business problem, users, and goals documented.
- [x] Architecture technology choices captured (not detailed system design).
- [x] English-only support and other initial constraints agreed.
- [x] v0.1 MVP explicitly separated from v1.0 automation release.
- [x] Functional and non-functional requirements prioritized.
- [x] Testable acceptance criteria specified.
- [x] Invoice lifecycle, human approval, and duplicate policy established at conceptual level.
- [x] Initial evaluation methodology and deferred numerical targets documented.
- [x] Out-of-scope features and phase boundaries recorded.

**Task 1.1 outcome:** Requirements baseline is ready for **Task 1.2 — System Architecture Design**. No implementation or Codex prompt is authorized by this document alone.

## 12. Open decisions reserved for the next tasks

These are **design details**, not unresolved business-scope blockers:

- **Task 1.2:** Component boundaries and call sequence, synchronous vs. asynchronous execution, file storage approach, OCR strategy, local deployment/service topology, authentication approach, and integration error paths.
- **Task 1.3:** Database schema and migration strategy, canonical invoice data models, API request/response contracts, idempotency keys, actor/audit schema, and transition validation.
- **Implementation/Evaluation:** LLM/OCR provider, invoice fixture design, numerical accuracy/latency targets after baseline, and optional cloud hosting.

## 13. Change control

Requirements are frozen as a **v0.1.0 planning baseline**. Any substantive change should be recorded with its reason, impacted FR/AC IDs, release target, and test implications, rather than silently added to an AI coding-agent prompt.
