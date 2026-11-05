# InvoiceFlow AI — 3–5 Minute Portfolio Demo

This walkthrough uses synthetic review data and the existing mocked Playwright
flows. It demonstrates the product boundary and engineering decisions without
calling Groq, mutating a live database, or rerunning the n8n harness.

## Before recording

For a local UI walkthrough, start the stack with docker compose up --build -d
and open http://localhost:5173. For a deterministic, service-independent
walkthrough, use the mocked flows in frontend/e2e/review-workflow.spec.ts and
capture only the resulting UI. Do not include terminal paths, credentials, or
real invoice/customer data.

Generated synthetic captures are available as [review queue](assets/review-queue.png)
and [review detail](assets/review-detail.png) references. They were produced by
the existing mocked Playwright fixtures, not by manually fabricating UI data.

## Spoken flow

### 1. Problem — 20 seconds

“An invoice system needs to do more than extract text. It has to preserve the
document, make uncertain fields visible, validate financial relationships, and
give an operator a controlled way to resolve exceptions.”

### 2. Architecture — 35 seconds

Show the diagram in architecture.md. Explain that FastAPI owns state and
decisions, PostgreSQL is the source of truth, Groq is behind a provider
boundary, and the React UI is an operator surface. n8n is an external
automation boundary, not a second business database.

### 3. Review queue — 40 seconds

Open the review queue. Show the synthetic case, its validation decision,
NEEDS_REVIEW state, and the distinction between actionable and historical
cases. Mention that the API supports filters and the frontend has explicit
loading, empty, retry, and error states.

### 4. Review detail and Accept Exception — 55 seconds

Open the case. Show validation findings, ruleset version, workflow state
version, and the optional reviewer note. Choose Accept exception, confirm, and
show the resolved state. Explain that this changes workflow state and persists
an event; it does not rewrite the failed validation or pretend that the invoice
passed validation.

### 5. Reject — 40 seconds

Return to a synthetic open case or use the mocked Reject scenario. Confirm
Reject invoice and show REJECTED. Explain that both resolution paths use the
same idempotent API command with a request ID and are protected by the backend
workflow lock.

### 6. n8n — 30 seconds

Show the previously captured Milestone 7B evidence rather than starting a live
run. Explain that the state commit happens first, then FastAPI sends an
authenticated event to n8n. Delivery is best effort; a failed webhook does not
change the committed business state, and there is no transactional outbox yet.

### 7. Engineering quality — 45 seconds

Show the milestone reports and .github/workflows/ci.yml: 269 backend/evaluation
tests passed with 14 intentional skips in the latest local run, 10 Vitest tests passed, 4 mocked Chromium
tests passed, and selected frontend coverage is 69.23% statements, 69.23%
branches, 60.34% functions, and 88% lines. Mention Ruff, Pyright, ESLint,
build, npm audit, Python audit, and Compose validation. State that historical
GitHub-hosted CI Run #2 passed all five jobs; the current release-candidate
commit still needs a new hosted run.

### 8. Roadmap — 20 seconds

Close with roadmap.md: data engineering and governed analytics come first, then
extraction evaluation, duplicate/anomaly detection, MLOps, and predictive
intelligence once suitable historical data exists.

## Demo guardrails

- Use synthetic or mocked records only.
- Do not reveal .env, secrets, local usernames, or private invoice files.
- Do not claim exactly-once n8n delivery or production certification.
- Do not call Groq solely for recording the demo.
