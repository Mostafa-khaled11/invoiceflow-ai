# PR-01D.1 — Audit Architecture Proposal

## Design goals

Add trustworthy audit context without changing invoice or workflow business
rules, inventing actors for historical rows, or making n8n delivery part of the
database transaction. New audit writes must be attributable, replay-safe, and
transactionally coupled to the state mutation that they describe.

## Trusted context

Introduce a frozen internal `ActorContext` created at a trusted boundary:

```text
actor_type: human | service | system
actor_id: stable local account ID, service name, or explicit system identity
request_id: one request/command identifier
correlation_id: workflow/request chain identifier
```

HTTP dependencies derive human context from the already authenticated server
session. The n8n service dependency derives service context from the configured
hashed credential. Internal system jobs select an explicit system identity.
The review command must never accept actor type or actor ID from the request
body. The context is immutable after construction and is the only source for
new event actor fields.

Middleware generates a fresh UUID `request_id` for every API request. It may
accept `X-Correlation-ID` as an external correlation label only after a bounded
syntax/length check; it must never treat that value as actor authority. If the
header is absent, middleware generates a correlation ID. Request and
correlation IDs are distinct fields and must not contain secrets, cookies,
authorization headers, or invoice text.

## Durable command idempotency

Add a `review_command_idempotency` table rather than making
`ReviewCase.resolution_request_id` authoritative:

- UUID primary key;
- non-null `actor_scope_key` (canonical `human:<local-id>`,
  `service:<name>`, or `system:<name>`);
- bounded non-null client request key, populated from the existing
  `resolution_request_id` body field;
- `fingerprint_version = review-resolution-v1` and canonical SHA-256
  `command_fingerprint`;
- invoice/workflow/review-case IDs;
- request/correlation IDs;
- result event ID and minimal result snapshot;
- created/updated timestamps.

Enforce a unique constraint on `(actor_scope_key, request_key)`. The v1
canonical JSON object has exactly these fields: `operation` (`review.resolve`),
`invoice_id`, the locked current `workflow_id`, the locked current
`review_case_id`, `decision`, and exact nullable `note`. Serialize with sorted
keys, compact separators, UTF-8, and hash with SHA-256. Actor identity is not
inside the payload because it is already the uniqueness scope. If the same
actor/key is retried with the same fingerprint, return the recorded result. If
the key is reused with a different fingerprint or version, return a conflict.
A different actor may use the same client key without taking ownership of the
first actor's command.

The existing review-case field remains readable for compatibility during a
rolling migration; new command records become authoritative. No historical
rows are backfilled.

## Atomic review transaction

The command service should acquire locks in one documented order: invoice,
current extraction/validation, workflow, review case, then an existing
idempotency row when present. Insert races are resolved by the unique
constraint and a retry/read path. Within one transaction:

1. validate the trusted context and command fingerprint;
2. resolve or create the actor-bound idempotency record;
3. re-check current workflow/review state under lock;
4. mutate workflow and review case;
5. insert the workflow event and link the command result;
6. commit all records together.

The linearization point remains the invoice lock/current-state transaction. A
failed event insert or command write rolls back the workflow mutation. n8n is
called only after commit; its payload receives event, request, and correlation
identifiers, but a delivery failure does not roll back committed business state.

## Workflow event evolution

Add nullable `request_id`, `correlation_id`, `command_id`, and optionally
`command_fingerprint` columns to `workflow_events`. Nullable columns preserve
old deployments and historical rows. New application-created events must fill
the context fields; legacy rows remain unchanged, including null actors.

The PR-01D.2 guarantee is application-role append-only behavior: the runtime
database role can insert events but cannot update or delete them, and the
application service exposes no mutation path. A migration/admin role retains
controlled schema/data-maintenance capability. A DBA or PostgreSQL superuser is
an explicit threat-boundary exception; this design does not claim absolute
tamper resistance. A hash chain is rejected for this milestone because it adds
key-management and repair complexity without being required by the current
threat model. PostgreSQL tests must attempt UPDATE and DELETE with the runtime
role and assert denial.

## Security audit events

Use a separate `security_audit_events` table for identity and administration,
rather than forcing role/session events into invoice workflow state. Fields:

- event ID, event type, actor context, target type/ID;
- request and correlation IDs;
- bounded allowlisted metadata;
- created timestamp and optional outcome/error category.

The currently exposed authentication operations are OIDC login, callback,
session inspection, and logout (`backend/app/api/v1/endpoints/auth.py`). Role
assignment/revocation, account lifecycle, and OIDC identity linking are
currently persistence-service operations, not administrative HTTP endpoints;
PR-01D.2 must audit the service calls without inventing endpoints. Candidate
events include role granted/revoked, account enabled/disabled, session
created/revoked, OIDC identity linked, login success/failure category, and
security configuration failures. Never store raw tokens, authorization headers,
cookies, OIDC claims wholesale, invoice text, provider output, or free-form
secrets.

## Historical compatibility, retention, and access

Do not rewrite existing workflow events or infer actors. Additive migrations,
dual-readable fields, and nullable context columns allow a safe rollout. Define
retention by legal/business requirements, then implement an approved archival or
deletion process that itself emits an administrative event. Until an admin-only
read API is approved, restrict operational access to controlled database/support
queries. If an `AUDIT_READ` endpoint is added, it must be admin-only, paginated,
redacted, and non-mutating.

## Alignment with approved architecture

This proposal extends the existing PR-01A/PR-01C boundaries: server-side OIDC
sessions remain the human trust source, role resolution remains centralized,
n8n remains a scoped service principal, and production remains fail-closed until
the complete audit/authorization rollout is verified. It does not introduce
multi-tenancy, Redis, Celery, a new provider, or a change to invoice semantics.

The closed decisions are recorded in the Architecture Decision Register in the
gap analysis. An Admin-only audit read API remains explicitly deferred and is
not required for PR-01D.2. Retention automation is also deferred until an
approved legal/business policy exists; PR-01D.2 must not silently delete rows.
