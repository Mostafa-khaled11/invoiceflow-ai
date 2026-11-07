# PR-01A — Implementation Roadmap

PR-01A is architecture only. The following slices are independently
verifiable and must not be started without approval for the corresponding
implementation work.

## PR-01B.1 — Identity and session persistence foundation

Dependencies: approved provider tenant/issuer, callback URLs, secret-management
plan, and the fake OIDC test boundary. Microsoft Entra ID is the selected
production target; no tenant is configured by PR-01A.

Implemented in the merged PR-01B.1 work: additive PostgreSQL account, identity,
role, and opaque session persistence with migration smoke coverage. It does not
authenticate requests or enforce authorization.

## PR-01B.2 — OIDC authentication and session lifecycle

Dependencies: PR-01B.1 persistence, approved provider tenant/issuer, callback
URLs, secret-management plan, and the fake OIDC test boundary.

Implemented in this branch: provider-neutral OIDC Authorization Code + PKCE,
validated discovery/JWKS/ID-token claims, single-use state/nonce transactions,
opaque hashed PostgreSQL sessions, secure cookies, CSRF/trusted-origin checks,
and production fail-closed configuration. Existing invoice/workflow routes are
not yet protected by RBAC; that remains PR-01C.

Testing includes deterministic mocked discovery/token/JWKS responses, invalid
signature/issuer/audience/nonce/expiry cases, state replay and mismatch,
pending/unknown accounts, session rotation/revocation, cookie flags, CSRF, and
no-token persistence.

Acceptance for this slice: the auth boundary can create and revoke a local
session only for an approved active `(issuer, subject)` identity, and no raw
OIDC or session token is returned or persisted. Hosted CI remains required
before calling the slice release-ready. Production startup remains blocked
until PR-01C adds and verifies route authorization; the configuration flag does
not bypass that gate.

Rollback: disable OIDC login through deployment configuration while preserving
the additive identity/session schema; never silently open protected routes.

## PR-01B.2 follow-up — Controlled account lifecycle

Testing:

- unique `(issuer, subject)` mapping and changed-email behavior;
- pending/invited first-login behavior and no automatic role grant;
- explicit first-admin bootstrap, disablement, session revocation, and role
  downgrade;
- migration tests on a fresh and upgraded PostgreSQL database;
- no provider claims or email values can manufacture Admin/Reviewer access.

Acceptance: email is never the security identity key; disabled accounts cannot
create or use sessions; role changes take effect according to the documented
revocation policy; production has no default account or password.

Rollback: preserve identity rows and historical events, disable new
provisioning, and revoke sessions if needed. Do not roll back by reopening
anonymous access to sensitive routes.

## PR-01C — Backend RBAC and service authorization

Dependencies: PR-01B.2 principal/session context and approved account bootstrap,
and approved role matrix.

Likely components: authorization dependencies, permission registry, invoice and
review route adapters, invoice workflow/review services, list-query scope
helpers.

Testing:

- matrix tests for every route and role;
- IDOR tests for invoice, validation, workflow, and review IDs;
- field-level review queue tests;
- direct service-call authorization tests;
- concurrent authorization and state-transition tests on PostgreSQL.

Acceptance: deny by default; no UI-only checks; operators cannot resolve
reviews; Admin has no implicit financial approval; Approver remains PR-02.

Rollback: revoke permissions/configuration, not route guards; maintain a safe
closed state if authorization configuration is unavailable.

## PR-01D — Authenticated audit actor propagation

Dependencies: PR-01B.1/PR-01B.2 principal and PR-01C authorization.

Likely components: audit context object, workflow service signatures, event
model/migration, request/correlation middleware, n8n delivery metadata.

Database changes: additive nullable correlation/request fields first; backfill
only system semantics where evidence exists; preserve historical null actors;
then enforce new writes through application checks. Migration must be tested on
clean PostgreSQL and upgraded databases, with reversible deployment sequencing.

Testing:

- human/system/service actor propagation;
- atomic state plus event commit/rollback;
- immutable event fields and ordering;
- actor-bound idempotency keys and concurrent competing decisions;
- redacted logs and webhook payloads.

Acceptance: human actions cannot be recorded as system actions; retries by a
different actor conflict; audit event and state mutation are one transaction.

## PR-01E — Frontend integration and complete security testing

Dependencies: PR-01B.1 through PR-01D stable API contracts and deployment
configuration, including the n8n service-credential plan.

Likely components: frontend API client, auth bootstrap, route guards, 401/403
UX, CSRF handling, review permission states, Playwright fixtures.

Testing: Vitest/MSW auth states, browser login callback fixture, expired-session
recovery, forbidden UI states, direct API negative tests, security scans, and
PostgreSQL concurrency/integration coverage.

Acceptance: frontend never stores bearer tokens in localStorage, displays no
sensitive data before authorization, handles 401/403 distinctly, and browser
tests use synthetic identities only.

## Secure rollout and migration gates

1. Deploy schema and adapter code with authentication enforcement disabled only
   in a local/test profile. Production configuration must reject this profile.
2. Deploy principal resolution and protect invoice, extraction, validation,
   workflow, review, and storage operations. Keep only the explicitly approved
   health endpoint public.
3. Migrate frontend requests and existing API tests to authenticated fixtures.
   Migrate the n8n queue reader to its scoped service identity before enabling
   protected review endpoints.
4. Enable service-layer authorization and object/company scope, then run the
   PostgreSQL migration smoke suite, route matrix, IDOR, CSRF, concurrency, and
   anonymous-route sweep.
5. Enable production traffic only after all acceptance gates pass. Rollback
   keeps protected routes closed, preserves additive identity/session data, and
   uses credential/session revocation rather than an anonymous compatibility
   flag.

Existing review idempotency records must remain valid. During PR-01D, historical
records are preserved with their existing nullable actor fields. New commands
store the authenticated actor and command fingerprint. A retry with the same
actor and fingerprint remains idempotent; a different actor or decision using
the same request key returns a conflict. No historical human identity is
invented from email, logs, or request payloads.

## Cross-cutting release gates

Each slice requires full existing Pytest/Ruff/Pyright/frontend gates, migration
verification on PostgreSQL 17, negative authorization tests, secret scanning,
and explicit documentation of changed API contracts. No slice may add PR-02
approval states or weaken an existing assertion.
