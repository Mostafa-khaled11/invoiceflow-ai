# PR-01A — Authentication, Authorization & Audit Security Architecture

Status: proposed architecture; implementation intentionally deferred.

## Scope and verified baseline

InvoiceFlow AI is currently a single-company modular monolith. FastAPI owns
workflow state and database decisions, PostgreSQL is the source of truth, React
is the operator UI, and n8n is an optional downstream automation boundary.

The repository currently has no authentication, authorization, user identity,
session, CSRF, or account lifecycle implementation. API dependencies expose a
request-scoped database session, storage service, settings, and server-side LLM
provider, but no authenticated principal. Existing workflow events use
`actor_type="system"` even when the API command represents a human review
decision; the current review request contains a client-supplied idempotency UUID
but no authenticated actor identity.

The existing workflow states and decisions are preserved. Final approval is
out of scope for PR-01A and belongs to PR-02.

## Security objectives

1. Require an authenticated principal for invoice and workflow data access.
2. Enforce permission and object-level checks in trusted backend code.
3. Preserve transactional workflow state and audit history under retries and
   concurrency.
4. Make human and machine actions distinguishable and attributable.
5. Support account/session/service-credential revocation without deploying a
   new application version.
6. Keep local development usable without creating a default production account
   or embedding credentials in source, React, or n8n exports.

## Trust boundaries

| Boundary | Threat | Required control |
| --- | --- | --- |
| Browser → FastAPI | Forged requests, CSRF, IDOR | OIDC login, secure session cookie, CSRF, backend authorization |
| FastAPI → PostgreSQL | Privilege bypass or forged audit data | Service-layer checks, transactions, constrained actor fields |
| FastAPI → Groq | Secret leakage or document injection | Server-side key, existing untrusted-document boundary |
| FastAPI → n8n | Machine credential compromise or replay | Dedicated service identity, scoped credential, rotation, authenticated delivery |
| Operator → review command | Unauthorized resolution or replay | Reviewer permission, object check, actor-bound idempotency |
| Logs/telemetry | Invoice or credential leakage | Structured redacted logging and correlation IDs |

## Recommended authentication architecture

Select standards-based OIDC Authorization Code with PKCE backed by a
server-side PostgreSQL session. The identity provider authenticates users; the
application maps the verified subject to a local account and role assignment.
The browser receives only an opaque session identifier in a secure cookie.

This avoids storing passwords and custom cryptography while retaining local
revocation, role changes, audit attribution, and deterministic integration
testing. The application should not accept arbitrary client-provided user IDs
as proof of identity.

### Alternatives considered

| Option | Advantages | Disadvantages | Decision |
| --- | --- | --- | --- |
| PostgreSQL local accounts + sessions | Self-contained, easy offline tests, immediate revocation | Password reset, MFA, rate limiting, hashing, and account security become application responsibilities | Dev fallback only, not production default |
| OIDC + server-side sessions | Standards-based identity, no password storage, central disable/MFA, revocable local sessions | Requires an IdP and callback/configuration management | Recommended |
| Self-contained JWTs | Simple stateless API validation and service interoperability | Revocation and role changes are difficult; browser token storage/rotation risks; larger claim surface | Not recommended for the browser workflow |

### Identity-provider decision

The selected architecture is a provider-neutral OIDC relying-party adapter,
with Microsoft Entra ID as the primary production integration target for the
portfolio deployment. This is an architecture decision, not a claim that an
Entra tenant, app registration, callback URL, or credential has been
configured or verified. The application must pin the configured issuer and
audience and must not derive local authorization from an arbitrary email or
unverified claim.

| Provider | Production operational complexity | Local development and CI | Cost/external dependency | Lifecycle/security responsibility | Decision |
| --- | --- | --- | --- | --- | --- |
| Keycloak | Highest: application owner operates the IdP, upgrades, realm backup, TLS, admin protection, and availability | Strong: disposable local realm and deterministic configuration are practical | No provider license cost, but infrastructure and maintenance remain required | Application owner is responsible for the IdP deployment and its hardening | Offline integration-test provider only; not the production default |
| Microsoft Entra ID | Low application-side complexity; managed tenant, app registration, enterprise lifecycle and disablement | Live tenant is unsuitable for deterministic CI; adapter tests must be offline | External tenant and possible licensing/billing dependency | Microsoft secures the service; application owner remains responsible for tenant, app, redirect, issuer, consent, and role policy | Primary production target |
| Auth0 | Low application-side complexity; managed tenant and hosted identity UX | Good developer setup, but live tenant calls must not gate CI | External SaaS and plan/vendor dependency | Provider manages identity platform; application owner remains responsible for tenant settings, connections, actions, callbacks, and authorization mapping | Viable managed alternative, not selected |

The provider choice does not create a second local authentication system. Local
development and CI use a fake OIDC boundary or mocked discovery/JWKS/token
exchange behind the same adapter contract. Tests must cover issuer, audience,
signature/JWKS, state, nonce, PKCE, expiry, callback errors, disabled users,
and role changes without contacting any external IdP. A live-provider smoke
test may be run separately by an explicitly configured operator, but it is not
required for unit, pull-request, or PostgreSQL integration verification.

Provider references: [Keycloak Server Administration](https://www.keycloak.org/docs/latest/server_admin/),
[Microsoft identity platform OIDC](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc),
and [Auth0 OIDC protocol](https://auth0.com/docs/authenticate/protocols/openid-connect-protocol).

### Session requirements

- Use an opaque, high-entropy, hashed session token stored server-side.
- Cookie: `HttpOnly`, `Secure` in production, `SameSite=Lax` or stricter,
  narrow `Path`, explicit expiry, and no exposure to JavaScript.
- Rotate the session identifier after login and privilege changes.
- Enforce absolute and idle expiry; revoke on logout, account disable, role
  downgrade, and suspected compromise.
- Store only a hash of the cookie token and minimal identity metadata.
- Bind authorization decisions to the current account/role state rather than
  trusting stale session claims.
- Require CSRF tokens for state-changing cookie-authenticated requests and
  validate `Origin`/`Referer` where appropriate.
- Require HTTPS and a trusted host/origin configuration in production.
- Apply login and callback rate limits at the edge/application boundary.
- Require OIDC authorization-code flow with PKCE, unguessable state, nonce
  validation, exact registered redirect URI matching, issuer/audience checks,
  signing-key validation, and bounded clock-skew handling. Never accept an ID
  token as proof solely because it is syntactically a JWT.
- Behind a reverse proxy, trust forwarded HTTPS headers only from an explicit
  proxy configuration; otherwise production must reject insecure callback and
  cookie assumptions.

### Identity and session data model proposal

The following additive model is proposed for PR-01B. Names are logical and do
not authorize a migration in PR-01A:

| Record | Required fields and constraints | Security purpose |
| --- | --- | --- |
| `users` | Local immutable UUID, status (`ACTIVE`, `DISABLED`, `PENDING`), display name, timestamps, last-login timestamp | Stable local principal independent of email or provider subject |
| `oidc_identities` | `user_id`, exact normalized issuer, exact OIDC `sub`, timestamps, last-seen profile fields; unique `(issuer, subject)` | Prevents account collision and makes email changes safe |
| `roles` / `user_roles` | Explicit role identifiers, unique assignment, grant/revoke timestamps and actor metadata | Deny-by-default authorization and auditable role changes |
| `sessions` | User FK, hash of opaque token, created/last-seen/idle/absolute expiry, revoked-at/reason, optional authentication context; unique token hash | Server-side revocation without browser bearer tokens |
| `review_command_idempotency` | Request key, authenticated user/service actor, command fingerprint, result/event reference, timestamps; unique actor/key policy | Prevents cross-actor reuse and decision substitution |

The immutable security key is `(issuer, subject)`, never email. Email is a
display/profile attribute and may change or be absent; changes must not merge
accounts automatically. A first login creates only a `PENDING` or explicitly
invited account. Activation and role assignment require a controlled bootstrap
or an existing Admin action; no email claim, group claim, or first user receives
privilege automatically. Disablement revokes sessions and blocks new sessions.
The first administrator is bootstrapped through a one-time deployment procedure
or pre-approved issuer/subject allowlist, recorded as an operator action, then
removed or locked down.

### Account lifecycle

The OIDC subject is immutable identity data. Local account fields should include
provider, subject, display name, status, created/updated timestamps, and role
assignment. Disabled accounts cannot create sessions; existing sessions are
revoked on disable. The first administrator must be bootstrapped through an
explicit deployment/operator procedure or IdP group claim, never a default
password or hard-coded account.

Development may use a documented local test principal or fake OIDC verifier in
tests only. It must be impossible to enable that bypass in production through a
normal debug flag.

### Local authentication strategy

There is one authentication architecture: OIDC at the application boundary
and server-side sessions after successful OIDC authentication. Local accounts
with application-managed passwords are not retained as a production fallback.
For deterministic development and CI, tests replace the OIDC discovery,
authorization callback, token exchange, and JWKS boundary with a fake provider
or mocked adapter. The same issuer, subject, claim-validation, provisioning,
and session code paths are exercised. A disposable Keycloak environment may be
used for an optional protocol smoke test, but it is not required by the normal
test suite and does not become a second application authentication system.

## Authorization model

Use deny-by-default named permissions and backend dependencies/service checks.
Roles are bundles of permissions, not security decisions embedded in the UI.
The proposed role matrix is in
[pr01-api-access-matrix.md](pr01-api-access-matrix.md).

Every invoice, validation, workflow, review case, and audit read must be
checked against the authenticated company scope. UUIDs are identifiers, not
authorization. Sensitive commands must repeat authorization in the service
layer so direct API calls cannot bypass route dependencies.

Admin receives administrative visibility and account/role management, but no
automatic financial approval privilege. Approver is reserved for PR-02; no
approval endpoint or state is introduced by PR-01A.

## Audit architecture

The target request path is:

```text
HTTP request
  → verified principal and correlation ID
  → route permission and object authorization
  → service command with immutable actor context
  → one PostgreSQL transaction
  → state mutation + audit event
  → commit
  → optional best-effort n8n delivery
```

The existing `WorkflowEvent` is a useful foundation: it already stores event
type, previous/resulting state, actor type/id columns, reason, metadata,
sequence number, and timestamp. The target model should additionally persist a
request/correlation ID and enforce actor semantics:

- Human resolution: `actor_type=human`, `actor_id` from the authenticated
  local account; never system or a client-supplied reviewer ID.
- Deterministic routing: `actor_type=system`, stable service actor ID or null
  only where historical compatibility requires it.
- n8n or other automation: `actor_type=service`, dedicated service identity.
- Preserve existing events without inventing identities retroactively.

The resolution request UUID remains an idempotency key. Its durable record must
be bound to the actor and command fingerprint. Reusing it by another actor, or
with a different decision, must return a conflict. The workflow state update,
review-case mutation, and audit event must commit atomically; failed delivery
must not roll back the committed business action.

## n8n integration direction

Current n8n integration is outbound from FastAPI to webhook endpoints using the
`X-InvoiceFlow-Webhook-Secret` header. The checked-in aging monitor also makes
an unauthenticated internal `GET /api/v1/review-cases` request today, so the
network boundary is not a substitute for a service identity. n8n does not own
workflow state and does not directly access PostgreSQL. Preserve that boundary.

PR-01 should introduce a distinct machine identity for n8n-facing API access
only where the workflows call protected API endpoints, with scoped permission
such as `review:read` and no review-resolution or approval permission. Browser
requests use the user session cookie; n8n requests use a separate service
credential; outbound FastAPI-to-n8n delivery continues to use its own webhook
credential. These are three separate trust relationships and must not share
cookies, user sessions, or Admin credentials.

The n8n service identity must be non-human, uniquely named, revocable, scoped
to the minimum read operations, and rejected with `401` when missing/invalid or
`403` when insufficient. Secrets must be injected through deployment
configuration, rotated by replacement, and never placed in exported workflow
JSON. Existing webhook secret compatibility requires a staged rotation window
and dual-secret validation only if necessary; the old secret must have an
explicit expiry. CI uses fake service credentials and local HTTP test doubles,
never real n8n or production secrets.

### Secure rollout boundary

Authentication enforcement must be introduced as a closed sequence:

1. Add identity/session schema and adapter tests while current routes remain a
   documented local-only state; production deployment is not permitted during
   this stage.
2. Add principal resolution and explicit public health policy. Protected
   invoice, validation, workflow, review, storage, and extraction routes then
   fail closed with `401` when no principal is present.
3. Add route and service authorization, object/company scope, and the n8n
   service identity. No compatibility flag may bypass these controls in a
   production configuration.
4. Migrate frontend API calls and tests to authenticated fixtures, then enable
   production traffic only after PostgreSQL migration, negative authorization,
   concurrency, and rollback gates pass.

During development, an isolated fake OIDC provider may be enabled only by test
fixtures or a non-production test application configuration that cannot be
selected through a normal production debug flag. Existing unauthenticated
clients must receive a deliberate `401` after enforcement; they must not be
silently upgraded to an Admin or service identity.

## Configuration and operational controls

Production configuration must require explicit OIDC issuer/client settings,
session secret material, trusted origins/hosts, HTTPS, and service credentials.
Startup should fail closed when required production security settings are absent.
Development defaults must not be interpreted as production security. Secrets
remain environment/secret-manager inputs and are never sent to React.

## Verification principles

- Test unauthenticated, authenticated-but-forbidden, and authorized requests
  separately.
- Test every object route with another invoice/review-case identifier.
- Test session rotation, expiry, logout, disable, role change, CSRF, and origin
  enforcement.
- Test actor propagation and idempotency under PostgreSQL transactions and
  concurrent review requests.
- Test service credentials cannot call human-only commands.
- Test logs and webhook payloads do not contain cookies, tokens, provider keys,
  or raw invoice content.

This document is an architecture proposal, not a claim that the application
currently provides these controls.
