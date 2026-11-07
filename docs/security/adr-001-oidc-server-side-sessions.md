# ADR-001 — OIDC Identity with Server-Side PostgreSQL Sessions

Status: accepted architecture; implementation deferred to PR-01B.1.

## Context

InvoiceFlow AI currently has no authentication or authorization boundary. It is
a single-company browser application with sensitive invoice and human-review
data. PR-01B.1 adds persistence records for future identity and session use but
does not make any route authenticated.
The system needs revocation, role changes, actor attribution, concurrency-safe
review commands, and testable local behavior without creating a password system
or storing browser bearer tokens.

## Decision

Use a standards-based OIDC Authorization Code flow with PKCE for user
authentication and create an opaque server-side session persisted in PostgreSQL.
The backend validates the identity-provider response, maps the immutable OIDC
subject to a local account, and issues a secure HttpOnly session cookie.

Use a provider-neutral OIDC adapter. Microsoft Entra ID is the primary
production integration target for this portfolio deployment because it offers a
managed enterprise identity lifecycle with low application-side operational
burden. Keycloak is reserved for optional disposable local integration testing;
Auth0 remains a viable managed alternative. No provider is currently
configured or verified in this repository.

Authorization uses local permission assignments evaluated by the backend. The
session is revocable and role/account status is rechecked for sensitive
commands. State-changing cookie requests require CSRF protection. OIDC issuer
and subject, not email, are the external identity key; local account activation
and role assignment are explicit administrative actions.

## Alternatives

- Local PostgreSQL accounts: retained only as a controlled development fallback;
  rejected as the production default because password reset, MFA, rate limits,
  credential recovery, and account security become application obligations.
- Self-contained JWTs: rejected for the browser workflow because revocation and
  role changes are difficult and browser token storage increases exposure.
- A second local-password system: rejected as a production fallback because it
  duplicates credential, reset, MFA, rate-limit, and breach-response duties.
  Test fixtures may mock the OIDC boundary, but they must not create a second
  authentication architecture.

## Consequences

Positive:

- No application password hashing or custom cryptographic protocol.
- Central identity, MFA, disablement, and account lifecycle support.
- Immediate local session revocation and auditable actor mapping.
- Deterministic fake-provider/session tests remain possible.

Costs and risks:

- Requires an OIDC provider, callback configuration, and secret management.
- PostgreSQL session storage adds a migration and cleanup process.
- Provider availability affects login, though existing sessions can continue
  until expiry subject to revocation policy.
- Trusted-origin, CSRF, cookie, and proxy HTTPS configuration must be correct.

## Security constraints

- No default production user or password.
- No OIDC bypass controlled by an ordinary debug flag.
- Require issuer, client, redirect, session, HTTPS, and trusted-origin settings
  in production.
- Rotate session IDs after login and privilege changes.
- Store only hashed session tokens.
- Bind audit actor IDs to the verified local account, never request payloads.

## Required implementation gates

PR-01B.1 must not begin until the provider tenant ownership, issuer, audience,
redirect URIs, secret-management location, first-admin bootstrap procedure,
and local/CI fake-provider contract are approved. Its acceptance gate is
offline deterministic coverage for state, nonce, PKCE, token validation,
session rotation, expiry, logout, revocation, CSRF, and disabled-account
behavior. A live IdP smoke test is optional and cannot replace those tests.
