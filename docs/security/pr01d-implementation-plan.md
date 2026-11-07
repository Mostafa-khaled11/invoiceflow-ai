# PR-01D — Implementation Plan

## PR-01D.2 — Persistence and service implementation

### Work items

1. Add immutable actor/request context construction at the HTTP, n8n, and
   internal-system boundaries.
2. Add additive Alembic migrations for workflow context fields, durable review
   command idempotency, and security audit events.
3. Implement canonical command fingerprinting and the actor-bound unique key.
4. Refactor review resolution to use the command record while retaining the
   existing review-case field for compatibility.
5. Add security audit writes to role/account/session/OIDC lifecycle operations.
6. Include safe event/request/correlation metadata in post-commit n8n payloads
   and structured logs.
7. Implement the selected immutability control and redaction allowlists.
8. Document retention and controlled operational access.

### Acceptance criteria

- No actor can be supplied by a command payload or overwrite trusted context.
- Same actor/key/same fingerprint is idempotent; same actor/key/different
  fingerprint conflicts; cross-actor replay cannot reuse another actor's result.
- Workflow state, review state, command record, and workflow event commit or
  roll back together.
- New events contain request/correlation context; historical null actors and
  historical request fields remain unchanged.
- Role, account, session, and OIDC administrative changes produce redacted
  security events.
- Raw credentials, tokens, cookies, invoice text, and provider output are absent
  from audit rows and logs.
- n8n delivery remains post-commit and queue-scoped.

### Migration and rollback strategy

Use expand/contract sequencing: create new tables and nullable columns first,
deploy code that writes both compatible fields, verify, then add only safe
constraints for new rows. Never drop or rewrite historical audit data. Rollback
means reverting application writes while leaving additive tables/columns in
place; destructive downgrade scripts are not part of deployment rollback.

Run migrations against a fresh disposable PostgreSQL database and a separate
general integration database. Never reset developer or production volumes.

## PR-01D.3 — Security regression, concurrency, and CI verification

### Test matrix

- Actor spoofing: body actor fields are ignored/rejected.
- Human, service, and system context produce the correct immutable actor.
- Request and correlation IDs are bounded, propagated, and redacted safely.
- Same actor/key retries, changed payload retries, and cross-actor replay.
- Concurrent same-command and competing-command PostgreSQL runs.
- Rollback after event, command, or security-audit insert failure.
- Direct database UPDATE/DELETE immutability checks and sequence uniqueness.
- Role grant/revoke, account disablement, session revocation, and OIDC audit.
- n8n payload correlation and secret/raw-document redaction.
- Historical rows with null actors remain byte-for-byte unchanged.

### CI gates

The hosted PostgreSQL job must run a fresh Alembic upgrade, migration smoke
checks, the required integration tests, the complete test suite, and the
JUnit zero-skip guard. Intentional external-provider skips remain explicitly
classified; mandatory PostgreSQL tests must execute when the integration job is
configured. Run Ruff, format check, Pyright, and `git diff --check`. No Groq or
other live provider calls are permitted.

### Acceptance criteria

- Full root suite and PostgreSQL integration suite pass with no unexpected
  skips.
- Fresh-database migration verification passes without `Base.metadata.create_all`
  standing in for Alembic.
- All concurrency and rollback assertions pass on PostgreSQL.
- Security audit records are queryable only through the approved operational
  boundary and contain no sensitive payloads.
- CI artifacts include JUnit output and migration/test evidence.

## Risks and release gates

The primary risks are uniqueness races, nullable-to-non-null rollout errors,
and accidentally logging sensitive context. Mitigate with additive migrations,
unique-violation retry/read logic, bounded identifiers, explicit allowlists, and
fresh-database CI. Production remains blocked until PR-01D.2 and PR-01D.3 are
reviewed and hosted PostgreSQL evidence is available.

## Decision closure

The architecture decision register in `pr01d-audit-gap-analysis.md` is the
authoritative closure for PR-01D.2:

- backend-generated UUID request IDs and separately validated correlation IDs;
- actor-bound uniqueness on `(actor_scope_key, request_key)`;
- request key sourced from the existing `resolution_request_id` UUID;
- versioned canonical fingerprint fields and conflict behavior;
- separate security-audit records for exposed authentication operations and
  internal administrative persistence operations;
- application-role append-only guarantee with an explicit DBA threat boundary,
  without a hash chain;
- post-commit best-effort n8n delivery, with outbox deferred;
- no automated audit deletion until a separately approved retention policy.

The Admin-only audit read API, retention duration, and archival mechanism are
operational follow-up decisions and are not prerequisites for implementing the
additive PR-01D.2 persistence/service layer. They remain production gates and
must not be silently assumed by code.
