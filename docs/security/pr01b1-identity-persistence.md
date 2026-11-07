# PR-01B.1 — Identity Persistence Foundation

Status: implemented persistence only. Authentication, browser cookies, OIDC
callbacks, and authorization enforcement remain disabled and are deferred to
later PR-01B/C slices.

## Implemented records

| Table | Purpose | Security boundary |
| --- | --- | --- |
| `user_accounts` | Stable local UUID, `PENDING`/`ACTIVE`/`DISABLED` status, optional profile email/display name, timestamps | No password or provider token is stored; no users are seeded |
| `oidc_identities` | Verified external identity mapped to a local account | Unique `(issuer, subject)`; email is only profile metadata |
| `roles` | Catalog of Operator, Reviewer, Approver, and Admin roles | Role definitions are seeded; no user receives a role automatically |
| `user_role_assignments` | Grant/revoke history for local roles | Partial unique index permits one active assignment per user/role and later regrant |
| `user_sessions` | Opaque session verifier hash and lifecycle timestamps | Only a 64-character SHA-256 verifier digest is stored; raw credentials are never persisted |

The migration seeds role definitions only. It creates no accounts and no
administrator. The persistence service allows sessions only for active accounts,
retains revoked/expired session records, and keeps role revocation history.

## Migration

Alembic revision `e1b2c3d4e5f6` follows `d8f9a0b1c2d3`. It is additive and
does not alter invoice, validation, review, workflow, or historical audit rows.
The migration smoke test verifies the new tables, role catalog, foreign keys,
unique issuer/subject constraint, session hash constraint, active role index,
and latest revision.

Downgrade removes identity and session data and is therefore appropriate only
for a disposable environment. It must not be used as a production rollback
after authentication depends on these records. A production rollback keeps
the additive schema and disables new identity/session use through deployment
controls while preserving historical records.

## Deliberate non-goals

- No login, callback, logout, cookie, token issuance, or authentication API.
- No password fallback or default account.
- No route authorization or RBAC enforcement.
- No OIDC access, ID, refresh token, or client-secret persistence.
- No changes to invoice, validation, workflow, review, or approval behavior.
