# PR-01D.1 — Audit Architecture Gap Analysis

## Scope and baseline

This is a source-backed, read-only review of the PR-01D.1 baseline. The working
branch is `docs/pr01d-audit-architecture`; its working tree was clean and HEAD
is `583244ae5e1fe3e3a63d9f0ca591cccb3f83555c`, the expected PR-01C merge commit
and an ancestor of the branch. Alembic has one head, `f2c3d4e5f6a7`.

The review covers the approved PR-01 security documents, workflow persistence,
identity/session persistence, authorization, OIDC lifecycle, and n8n access.
It does not change application code or historical data.

## Evidence summary

| Area | Evidence | Assessment |
|---|---|---|
| Workflow event | `backend/app/models/invoice_workflow.py:122-174` | Events have sequence uniqueness, constrained types/states, actor fields, metadata, and timestamps. |
| Initial routing | `backend/app/services/invoice_workflow.py:115-189` | Invoice row is locked before extraction/validation, then workflow, event, and review case are committed together. |
| Review resolution | `backend/app/services/invoice_workflow.py:294-475` | Principal-derived actor attribution, invoice/workflow/review locks, request-ID replay checks, and one transaction are present. |
| Concurrency tests | `backend/tests/test_invoice_workflow_review_concurrency_integration.py:75-161` | PostgreSQL tests cover same-request idempotency and competing resolutions; the stale-currentness test begins at line 164. |
| OIDC identity | `backend/alembic/versions/e1b2c3d4e5f6_add_identity_and_sessions.py:73-110` | `(issuer, subject)` is unique and identities reference local accounts. |
| Roles | `backend/alembic/versions/e1b2c3d4e5f6_add_identity_and_sessions.py:141-179` | Active role grants have a partial unique index; revocation is retained. |
| Sessions | `backend/app/services/identity_persistence.py:184-329` | Only token hashes are stored; expiry and revocation are explicit. |
| n8n | `backend/app/services/integrations/n8n.py:1-119` and `backend/app/services/authorization.py:143-220` | Dedicated hashed service credential and queue-scoped authorization exist; outbound delivery is post-commit and best effort. |

## Findings

### High — audit context is not durable

`WorkflowEvent` has no request ID, correlation ID, command ID, or command
fingerprint. Its metadata currently contains workflow-domain details, not a
stable request context. A later operator cannot reliably connect a state change
to an HTTP request, an n8n command, or an authenticated session.

### High — idempotency is a mutable current-case field, not a durable command record

`ReviewCase.resolution_request_id` is nullable and has no uniqueness constraint
or payload fingerprint (`invoice_workflow.py:177-220`). Resolution compares the
current case's request ID, decision, actor, and note
(`invoice_workflow.py:395-425`). This correctly rejects the tested cross-actor
replay while the case remains available, but it cannot provide durable
actor-plus-key ownership independent of the current case and cannot prove that
the complete command payload was unchanged.

This is a confirmed architecture gap, not evidence that the current tested
review endpoint is freely replayable.

### Medium — administrative security actions are not audited

Role assignment/revocation, account lifecycle changes, session creation and
revocation, and OIDC identity/login decisions are persisted as operational
state, but there is no generic security-audit event stream. The `AUDIT_READ`
permission exists, but no corresponding audit read surface is part of this
baseline. This prevents reliable investigation of privilege changes and session
invalidations.

### Medium — database immutability is not enforced

The model docstring calls `WorkflowEvent` append-only, and application paths
only insert it, but the schema has no update/delete trigger, restricted writer
role, or hash chain. Sequence uniqueness prevents duplicate positions; it does
not prevent a privileged application/database writer from editing history.

### Medium — no retention or operational access policy

Events have timestamps but no documented retention, archival, deletion approval,
or admin query boundary. This is both an availability and privacy governance
gap. It should be resolved with explicit policy rather than an unbounded table
or ad-hoc deletion.

### Medium — correlation stops at the application event boundary

The n8n adapter authenticates outbound requests and logs event/workflow/invoice
identifiers, but there is no request/correlation identifier in the event payload
or log context. Delivery is intentionally after commit; a webhook failure does
not roll back the workflow mutation. That behavior is safe for transaction
consistency but remains best-effort delivery without an outbox.

### Low — historical null actors are a compatibility condition

Actor columns are nullable and existing system/legacy events may lack a local
actor. This is not a defect to repair by inference. Historical rows must remain
unchanged; new writes should require a trusted actor context except for
explicitly system-owned operations.

## Confirmed protections and false alarms

- OIDC identity ownership is protected by the database unique constraint on
  `(issuer, subject)`; email is not the identity key.
- Raw OIDC tokens and raw session tokens are not persisted. Session persistence
  stores a verifier hash and lifecycle timestamps.
- Role grants are revocable and active duplicates are constrained. Existing
  authorization resolves active roles on the server and fails closed.
- Review resolution has a clear PostgreSQL linearization point: the invoice row
  lock is acquired before currentness is checked and retained through commit.
  Existing integration tests demonstrate same-request idempotency and competing
  decisions.
- The current cross-actor check is not a false positive; it is useful protection
  for the current case. The gap is durability and explicit actor-bound key
  constraints, not permission to bypass the check.
- The scoped n8n service principal and separate outbound webhook secret are
  intentional boundaries. No provider or live integration was accessed.

## Risk ranking

| Severity | Finding | Required treatment |
|---|---|---|
| High | Missing durable request/correlation context | PR-01D.2 additive context and event fields |
| High | No durable actor-bound command fingerprint | PR-01D.2 idempotency table and transactional command service |
| Medium | Administrative actions not in a security audit stream | PR-01D.2 security audit persistence |
| Medium | No DB-level immutability/tamper evidence | PR-01D.2 design choice plus PostgreSQL verification |
| Medium | No retention/operational access policy | Decide before production enablement |
| Medium | Post-commit n8n delivery has no durable outbox/correlation | Add correlation now; decide outbox separately |
| Low | Historical null actors | Preserve; document compatibility semantics |

No production deployment should be enabled solely on the strength of the
current workflow event table. The findings are suitable for the planned PR-01D.2
design and implementation review.

## Architecture Decision Register

The following decisions close the PR-01D.1 design questions. “Implemented”
means present on the current baseline; “proposed” means work for PR-01D.2.

| ID | Decision | Status | Verification criteria |
|---|---|---|---|
| ADR-01D-01 | Generate a backend `request_id` UUID for every API request. Accept an optional external `X-Correlation-ID` only after bounded syntax validation; keep it separate from `request_id`, and generate a correlation ID when absent. | Proposed | API tests prove generation, validation, separation, propagation, and absence of secret/header-value logging. |
| ADR-01D-02 | Use a dedicated command record with unique `(actor_scope_key, request_key)`. `request_key` is the existing body `resolution_request_id`; actor scope is `human:<local-account-uuid>`, `service:<service-name>`, or `system:<stable-name>`. | Proposed | PostgreSQL unique-constraint and concurrent insert tests prove same-actor replay behavior and cross-actor isolation. |
| ADR-01D-03 | Fingerprint version `review-resolution-v1` uses canonical JSON fields: `operation`, `invoice_id`, `workflow_id`, `review_case_id`, `decision`, and exact nullable `note`. Actor identity is represented by the unique actor scope, not duplicated in the payload. | Proposed | Canonicalization vectors, changed-field conflicts, and version-mismatch tests pass. |
| ADR-01D-04 | Preserve `ReviewCase.resolution_request_id` as a nullable compatibility field; do not backfill historical records. New command records are authoritative. | Proposed | Migration inspection and historical-row regression test show no rewrite and correct legacy reads. |
| ADR-01D-05 | Add a separate append-only security-audit stream for operations that actually exist: OIDC login/callback security outcomes, session creation/revocation/logout, and internal role/account/identity persistence changes. No administrative HTTP endpoints are invented. | Proposed | Service/API tests verify actor attribution, event type, target, outcome, and redaction. |
| ADR-01D-06 | Guarantee append-only behavior for the application role through PostgreSQL privileges and a narrow writer boundary; explicitly exclude a compromised DBA/superuser from the guarantee. Do not add a hash chain in PR-01D.2. | Proposed | Direct UPDATE/DELETE using the application role is rejected; controlled migration/admin role behavior is documented. |
| ADR-01D-07 | Keep n8n delivery post-commit and best effort. Add correlation metadata, but defer a durable outbox to a separate production-hardening milestone. | Existing behavior retained; correlation proposed | Commit-before-delivery and delivery-failure tests show workflow state is not rolled back and no credentials are logged. |
| ADR-01D-08 | Retain audit rows without automated deletion in PR-01D.2. A separately approved legal/business retention period and archival/deletion process is required before production retention automation; no destructive downgrade is permitted. | Proposed policy gate | Retention metadata, operational ownership, and an approved policy are reviewed before production enablement. |

These decisions are sufficient to implement PR-01D.2. ADR-01D-08 remains a
production governance gate, not an invitation to invent a retention period in
application code.
