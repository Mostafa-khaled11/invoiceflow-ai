# PR-01D Final Security Closure

## Scope and baseline

This closure covers the PR-01 security workstream on branch
`feat/pr01d2-audit-persistence`, compared with `master` at `ac7f7e0`.

Verified baseline:

- HEAD: `65415bd` (`feat(audit): integrate sanitized security audit events`)
- Working tree: clean before this document was created
- Alembic source head: `a7b8c9d0e1f2`
- No production database, development database, or migration-rehearsal database
  was modified during final verification.

The project is an engineering portfolio implementation. This document does not
certify the system for commercial production deployment.

## Original security gaps

The PR-01 workstream addressed the following gaps:

- No authenticated OIDC/session boundary.
- No centralized backend permission enforcement.
- No durable actor-scoped review-command idempotency.
- Incomplete request and correlation context propagation.
- No durable security-audit event persistence or sanitization boundary.
- Unverified PostgreSQL transaction and concurrency behavior.
- Risk of duplicate post-commit workflow notifications on replay.

## Implemented controls

### Authentication and sessions

- OIDC Authorization Code + PKCE flow.
- Server-side opaque sessions with only SHA-256 token verifiers persisted.
- Session idle and absolute expiration.
- Session revocation and logout handling.
- CSRF and trusted-origin checks for logout.
- Production fail-closed authentication configuration checks.

Raw OIDC tokens, authorization codes, cookies, and session secrets are not
persisted.

### Authorization and RBAC

- Centralized `AuthorizationContext` resolution.
- Permission checks on protected invoice, workflow, review, and authentication
  operations.
- Human actors derive from the authenticated local account.
- n8n uses a scoped service principal.
- Review resolution does not accept actor identity from request bodies or
  headers.

### Request and correlation context

- Every HTTP request receives a backend-generated UUID request ID.
- Correlation IDs are independently validated and bounded.
- Duplicate correlation headers are rejected by generating a fresh server ID.
- Context is isolated with `ContextVar` and reset on success, exceptions, and
  cancellation.
- Middleware owns response request/correlation headers, including error paths.

### Durable review-command idempotency

Review commands use:

- Actor-scoped uniqueness on `(actor_scope_key, request_key)`.
- `request_key` sourced from the existing `resolution_request_id`.
- Versioned `review-resolution-v1` canonical SHA-256 fingerprints.
- Exact canonical fields: operation, invoice ID, workflow ID, review-case ID,
  decision, and nullable note.
- PostgreSQL row locks and savepoint recovery for uniqueness races.
- Atomic workflow mutation, review mutation, event insertion, and command
  completion.
- Successful same-actor replays return the recorded result without a duplicate
  event or n8n notification.

Historical review cases remain compatible and are not backfilled.

### Security-audit persistence

`security_audit_events` is written through a caller-transaction-owned audit
writer. The writer never commits independently.

Implemented event categories include:

- OIDC login started, succeeded, and failed.
- Logout.
- Session creation and revocation.
- Account creation.
- OIDC identity linking.
- Role assignment and revocation.

Actor identities are fixed server-side identities or authenticated local account
IDs. Anonymous authentication failures use the stable `system:anonymous`
classification because the schema does not define a separate anonymous actor
type.

Audit metadata uses an explicit allowlist. Arbitrary `reason` text is not an
allowed audit field; existing role/session `reason` arguments remain available
for their original business persistence fields but are not copied to audit
metadata.

### Transaction and PostgreSQL guarantees

Successful security state changes and their success audit records share the
caller transaction where the existing operation permits it. Authentication
failure audit records are written after rollback of failed authentication work.
Audit-write failures propagate rather than falsely reporting success.

PostgreSQL verification covered:

- Same-command concurrent review resolution.
- Competing review resolutions.
- Cross-invoice actor/request uniqueness races.
- Savepoint recovery after uniqueness conflicts.
- Review event cardinality and durable command state.
- Identity, session, role, and audit persistence constraints.

### n8n behavior

n8n delivery remains post-commit and best-effort. Request and correlation IDs
are included in workflow notification payloads. Successful idempotent replays
do not send a second notification. Delivery failure does not roll back already
committed business state.

No outbox or workflow redesign was introduced.

## Verification evidence

All state-mutating PostgreSQL tests used a separately named disposable database.
The existing `invoiceflow` database and retained rehearsal database were not
targeted.

### Current HEAD verification

Local full suite:

```text
330 passed, 28 skipped, 2 warnings
```

The skips include the repository's PostgreSQL-gated tests when run locally
without `TEST_DATABASE_URL`, optional capability skips, and the intentionally
disabled live Groq smoke test.

Final PostgreSQL integration run:

```text
25 passed, 1 skipped, 331 deselected
```

The single PostgreSQL-run skip was the intentional live Groq smoke test.

Fresh isolated Alembic migration smoke:

```text
1 passed, 1 warning
Alembic revision: a7b8c9d0e1f2
```

Static verification:

- Ruff check: PASS
- Ruff format check: PASS
- Pyright: PASS
- `git diff --check`: PASS

Earlier PR-01D.2 verification also recorded the focused PostgreSQL review
concurrency and cross-invoice race tests passing, including the durable
idempotency and rollback paths.

## PostgreSQL test-order limitation

The general test fixture uses `Base.metadata.create_all()` and
`Base.metadata.drop_all()` for per-test isolation. When the full suite shares a
PostgreSQL database, ordinary tests can remove Alembic-managed tables before
the migration smoke test executes.

The safe procedure is therefore:

1. Run the full application/integration suite against one disposable database.
2. Recreate a separate disposable database.
3. Run `alembic upgrade head` on that database.
4. Run migration smoke independently.
5. Remove only the disposable databases.

This limitation does not affect the application schema or the developer
database, but it must remain documented in CI execution procedures.

## Findings and dispositions

### Critical

None identified.

### High

None identified.

### Medium

No unresolved Medium findings remain within the agreed portfolio scope.

The previously identified arbitrary audit `reason` metadata issue was closed by
removing `reason` from the audit metadata allowlist and preserving it only in
its existing business persistence fields.

### Low and deferred limitations

The following are explicit scope limitations rather than hidden assumptions:

- Some internal lifecycle events use stable system attribution instead of a
  human administrator identity because no administrative HTTP operation exists.
- Invalid-Origin and CSRF logout rejections are not currently persisted as
  security events.
- The audit writer accepts trusted scalar actor fields rather than a dedicated
  typed actor-context object.
- Database-level runtime-role restrictions for audit append-only behavior are
  not configured in this application change.

## Explicitly deferred work

The following are intentionally outside PR-01D:

- Frontend OIDC integration.
- Cloud deployment and infrastructure hardening.
- Audit retention policy, archival, and an audit read API.
- Database-level audit privilege restrictions.
- Full production observability and alerting.
- Durable n8n outbox delivery.
- Administrative role-management HTTP endpoints.
- Typed actor context in the audit writer.
- Persisted invalid-Origin/CSRF logout rejection events.
- Human administrator attribution for generic internal lifecycle operations.

These items must not be interpreted as production certification.

## Threat boundaries and trade-offs

The implementation protects against application-level actor spoofing, accidental
secret persistence, duplicate review resolution, and partial transactional
state changes. PostgreSQL locking and uniqueness provide the concurrency
linearization boundary.

The design intentionally avoids hash chains, external audit infrastructure,
Redis, queues, multi-tenancy, and an outbox. Those additions would increase
operational and key-management complexity beyond the portfolio objective.

The database administrator/superuser remains an explicit threat-boundary
exception for audit tampering. Retention and operational access require a
separate approved policy.

## Objective assessment

The implementation meets the agreed PR-01 portfolio objectives:

- Authentication and authorization boundaries are explicit.
- Review commands are durable and replay-safe.
- Security events are transactionally persisted and sanitized.
- PostgreSQL concurrency behavior is verified in isolation.
- Deferred work and threat boundaries are documented.

The system is ready for review and merge as a portfolio security workstream,
subject to normal pull-request review. It is not commercially production-
certified.

## Pull Request description

### Summary

Complete the InvoiceFlow AI PR-01D security workstream with durable audit
persistence, actor-scoped review-command idempotency, trusted request context,
and PostgreSQL concurrency verification.

### Key implementation changes

- Added additive audit and review-command persistence.
- Added secure request/correlation middleware.
- Added canonical actor-scoped review fingerprints and replay handling.
- Integrated sanitized security events into OIDC, sessions, roles, accounts,
  and identity linking.
- Added post-commit n8n correlation propagation.

### Security controls

- Server-side OIDC sessions and CSRF/origin protection.
- Centralized RBAC enforcement.
- Trusted actor derivation.
- Explicit audit event and metadata allowlists.
- No raw token, cookie, authorization-code, or provider-payload persistence.
- PostgreSQL row locking, uniqueness, savepoint, and rollback verification.

### Test evidence

- Local suite: `330 passed, 28 skipped`.
- PostgreSQL integration: `25 passed, 1 skipped`.
- Migration smoke: `1 passed`.
- Ruff, format, Pyright, and diff checks passed.

### Known limitations

Audit retention, audit read APIs, database privilege hardening, production
observability, durable n8n delivery, frontend OIDC, and administrative role
endpoints remain intentionally deferred.

### Merge recommendation

Ready for pull-request review. Do not describe this project as commercially
production-certified.
