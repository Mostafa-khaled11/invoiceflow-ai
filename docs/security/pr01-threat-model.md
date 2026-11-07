# PR-01A — Focused STRIDE Threat Model

This is an engineering threat assessment, not an OWASP certification or formal
compliance result. Severity reflects the current local-development application
and the proposed production direction. The control vocabulary is informed by
[OWASP ASVS 5.0](https://owasp.org/www-project-application-security-verification-standard/)
and the OWASP cheat sheets for
[Authentication](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html),
[Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html),
[CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html),
and [Authorization](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html).

| Threat / attack scenario | Component | Severity | Mitigation | Verification test | Residual risk |
| --- | --- | --- | --- | --- | --- |
| Stolen password or IdP session is reused | Browser, OIDC, sessions | Critical | OIDC/MFA at IdP, secure cookies, rotation, logout/revocation, HTTPS | Session theft/rotation/logout tests | Compromised IdP/device remains possible |
| Session fixation or token leakage | Auth callback/session store | High | Rotate on login/privilege change; opaque hashed tokens; no localStorage tokens | Before/after-login cookie identity test | Browser/extension compromise |
| Cross-site review resolution | Browser/API | Critical | SameSite cookie, CSRF token, Origin validation, POST-only command | Cross-origin forged Accept/Reject request rejected | Misconfigured trusted origins |
| IDOR through invoice UUID or review-case UUID | Invoice/review APIs | Critical | Company/object authorization in route and service layers | User A requests User B object; 403/404 and no data | Incorrect future scope query |
| Privilege escalation via Admin or role update | RBAC/account service | Critical | Deny-by-default permissions; separate role management; no Admin auto-approval | Matrix and negative authorization tests | Admin account compromise |
| Credential stuffing/login abuse | OIDC callback/local dev fallback | High | Prefer IdP; rate limit callback/local fallback; alerting | Repeated invalid auth is throttled | Edge enforcement dependency |
| Unauthorized invoice download/content access | Storage/API | Critical | Private volume, authenticated object checks, no static route, scoped extraction | Direct path/API access tests | Host/container compromise |
| Forged audit event or false human actor | Workflow service/database | Critical | Immutable actor context from principal; constrained actor types; service-layer writes | Human/system/service actor propagation tests | DBA or application-owner compromise |
| Review decision replay or cross-actor idempotency abuse | Review resolution | High | Actor-bound request ID and command fingerprint; transactional unique record | Same key/different actor/decision conflict tests | Lost client request ambiguity |
| Machine credential compromise | n8n/FastAPI webhook/API | High | Dedicated queue-only service identity, hash verification, rotation, no human command access | Service principal permission-negative tests | n8n host compromise |
| Sensitive invoice/provider data in logs | API/provider/n8n | High | Redaction, structured allowlist, no cookies/secrets/raw document logging | Log capture assertions and secret scan | Third-party infrastructure logs |
| Misconfigured development settings reach production | Settings/Compose | High | Fail-closed production validation; explicit environment; secure defaults; deployment checks | Production config refuses missing OIDC/HTTPS secrets | Operator deployment error |
| OIDC issuer, audience, redirect, state, nonce, or PKCE misconfiguration | OIDC adapter/callback | Critical | Pin issuer and audience; exact redirect allowlist; validate state, nonce, PKCE, signature, expiry, and JWKS; no live IdP dependency in CI | Malformed, replayed, wrong-issuer, wrong-audience, and callback-confusion tests | Provider outage or tenant misconfiguration can deny login |
| Account collision or privilege grant through email/claims | Identity mapping/provisioning | Critical | Key identity by `(issuer, subject)`; first login is pending/invited; explicit role grant; email is profile data only | Changed-email, duplicate-subject, first-user, and untrusted-role-claim tests | Administrative provisioning error remains possible |
| Authentication bypass during rollout | Route guards/deployment | Critical | Protected routes fail closed; fake provider only in test configuration; staged frontend/n8n migration; no production debug bypass | Anonymous route sweep and deployment-config negative tests | Incorrect rollout sequencing can cause outage |
| Confused browser, n8n, and webhook credentials | API/n8n boundaries | High | Separate HttpOnly user sessions, scoped service credentials, and outbound webhook secret; independent revocation and rotation | Credential-cross-use and permission-negative tests | Secret-management or network compromise remains possible |
| Metadata/review enumeration through list filters | Review queue API | High | Scope filters before counts/pagination; permission-specific fields | Cross-scope list and total tests | Query regression |
| Webhook replay or event confusion | n8n integration | Medium/high | Event ID deduplication, authenticated payload, event version, timestamp/correlation | Duplicate and stale event tests | Best-effort delivery lacks durable outbox |

## Highest-priority controls

1. Authentication and secure session boundary.
2. Object-level authorization on every invoice/review route.
3. Actor-bound transactional audit events.
4. CSRF and trusted-origin enforcement.
5. Scoped, rotatable n8n service identity.
6. Production fail-closed configuration and redacted observability.
