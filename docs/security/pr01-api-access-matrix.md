# PR-01A — API Security Inventory and Access Matrix

As of PR-01C, protected invoice, validation, workflow, and review routes
require a resolved browser session or the explicitly scoped n8n service
credential. Expected failures are `401` for a missing/invalid identity and
`403` for an authenticated principal without the permission. Health/readiness
and OIDC boundary routes retain their explicit public or protocol-specific
policy.

This is the final PR-01A target matrix. Only liveness is intentionally public;
readiness must follow an explicit deployment health-check policy. No production
route may use a transitional anonymous bypass once PR-01C enforcement begins.

| Method and route | Current behavior | Sensitivity / IDOR risk | Target principal and permission | Expected coverage |
| --- | --- | --- | --- | --- |
| GET `/api/v1/health` | Liveness only; no DB query | Low, but useful for reconnaissance | Public liveness; rate-limit at edge | Public 200; no auth dependency |
| GET `/api/v1/ready` | Queries PostgreSQL and reports readiness | Low/medium operational disclosure | Public only behind trusted health-check policy, or `system:read` internally | 200/503 behavior and information minimization |
| POST `/api/v1/invoices/upload` | Validates and stores PDF; duplicate detection | High: creates sensitive invoice data | Operator `invoice:create` | 401/403; content/type/size; company scope |
| GET `/api/v1/invoices/{invoice_id}` | Returns invoice metadata | Medium/high; UUID is not authorization | Operator/Reviewer `invoice:read` | 404/403 for another object |
| POST `/api/v1/invoices/{invoice_id}/extract-text` | Reads private PDF and persists text metadata | High: source document access | Operator/Reviewer `invoice:extract` | 401/403; object scope; no raw text leakage |
| POST `/api/v1/invoices/{invoice_id}/extract-structured` | Calls configured server-side LLM and persists result | High: invoice content/provider cost | Operator/Reviewer `invoice:extract` | 401/403; provider remains server-side |
| POST `/api/v1/invoices/{invoice_id}/validate` | Persists deterministic findings | High: affects workflow input | Operator/Reviewer `invoice:validate` | 401/403; current extraction scope |
| GET `/api/v1/invoices/{invoice_id}/validation` | Returns findings and decision | High: financial and review data | Operator/Reviewer `validation:read` | 401/403; object scope |
| POST `/api/v1/invoices/{invoice_id}/workflow/route` | Creates/retrieves initial route and may notify n8n | High: creates business state/event | Reviewer or controlled system `workflow:route` | 401/403; idempotency/concurrency |
| GET `/api/v1/invoices/{invoice_id}/workflow` | Returns current workflow state | High: operational decision data | Operator/Reviewer `workflow:read` | 401/403; object scope |
| POST `/api/v1/invoices/{invoice_id}/workflow/review` | Accept Exception or Reject; writes event | Critical: human decision and audit history | Reviewer `review:resolve` | 401/403; actor-bound idempotency; concurrency |
| GET `/api/v1/review-cases` | Lists current/history cases with filters | High: cross-invoice data enumeration | Operator limited queue; Reviewer `review:read` | 401/403; tenant filters; pagination leakage |
| GET `/api/v1/review-cases/{review_case_id}` | Returns review, validation, and invoice context | High: direct object IDOR | Reviewer/Admin `review:read`; Operator and n8n queue principals denied detail | 401/403/404 policy tested |
| GET `/docs` and `/redoc` | FastAPI interactive documentation | Low/medium reconnaissance | Disable or protect in production; public only in dev | Production exposure check |
| GET `/openapi.json` | OpenAPI schema | Low/medium route disclosure | Disable/protect in production if policy requires | Deployment configuration test |

## Principal and coverage audit

The current route inventory is enforced by centralized principal resolution and
the permission registry. The service principal policy is:

- Browser users authenticate as Operator, Reviewer, Approver (PR-02), or Admin.
- The n8n aging-review monitor may receive a dedicated read-only service
  identity with `review:queue` only. It must not read case detail, resolve reviews, or upload
  invoices.
- The checked-in aging monitor sends a Bearer token from its runtime environment
  to `GET /api/v1/review-cases`; the API verifies only the configured SHA-256
  token hash. The service is read-only and queue-scoped.
- Post-commit webhook delivery remains an outbound FastAPI-to-n8n call using
  the existing webhook secret boundary; it is not a browser principal and does
  not own workflow state.
- Internal deterministic routing remains a system action and must not be
  presented as a human action.

Existing coverage includes domain/API behavior and the PR-01C authorization
boundary. Relevant
files are `backend/tests/test_health.py`,
`backend/tests/test_ready_integration.py`, `backend/tests/test_invoice_storage.py`,
`backend/tests/test_invoice_persistence.py`, `backend/tests/test_invoice_upload.py`,
`backend/tests/test_invoice_text_extraction.py`,
`backend/tests/test_invoice_validation_api.py`,
`backend/tests/test_invoice_workflow_api.py`,
`backend/tests/test_invoice_workflow.py`,
`backend/tests/test_invoice_workflow_review_concurrency_integration.py`, and
`backend/tests/test_live_e2e_harness.py`. Review-case API coverage is included
in the workflow API tests. Existing PR-01C tests now provide an explicit
authenticated synthetic identity, role-denial, service-principal, and actor
attribution coverage. Cross-company object scope is not modeled in the current
single-company schema. Those are explicit
remaining PR-01D/E gaps, not silently inferred coverage.

PR-01C adds `backend/tests/test_authorization_api.py` for anonymous denial,
role denial/revocation, disabled accounts, scoped service credentials, and
human actor attribution. The database remains single-company, so cross-company
scope cannot be tested without inventing a schema that does not exist. Full
actor/idempotency correlation and audit administration remain PR-01D work.

| Surface | Human principal | Service principal | Existing coverage | Security coverage gap |
| --- | --- | --- | --- | --- |
| Health/readiness | None required by current design | Deployment health checker | `test_health.py`, `test_ready_integration.py` | Production exposure and information-disclosure policy |
| Invoice upload/metadata/extraction | Operator or Reviewer | None | `test_invoice_upload.py`, `test_invoice_persistence.py`, `test_invoice_text_extraction.py`, `test_structured_invoice_extraction.py` | Authentication, object scope, and cost authorization |
| Validation | Operator or Reviewer | None | `test_invoice_validation_api.py`, `test_invoice_validation_persistence.py` | Authentication and object scope |
| Workflow route/read | Reviewer for route; Operator/Reviewer for read | Controlled system only if a future automation calls route | `test_invoice_workflow_api.py`, `test_invoice_workflow.py`, concurrency tests | Authorization, object scope, and system identity boundary |
| Review queue/detail | Reviewer; Operator limited queue | n8n monitor queue-only identity | Workflow API and authorization tests | Scoped list/detail access, pagination leakage, and service negative tests |
| Review resolution | Reviewer only | None; n8n must not resolve | `test_invoice_workflow_api.py`, `test_invoice_workflow.py`, `test_invoice_workflow_review_concurrency_integration.py` | Authenticated actor propagation and actor-bound idempotency |
| Outbound n8n webhook | Not a browser-callable route | FastAPI webhook client with rotated secret | `test_n8n_integration.py`, `test_live_e2e_harness.py` | Secret rotation, replay handling, and durable delivery strategy |

## Role-permission proposal

| Permission | Operator | Reviewer | Approver (PR-02) | Admin |
| --- | ---: | ---: | ---: | ---: |
| `system:read` | — | — | — | ✓ |
| `invoice:create` | ✓ | ✓ | — | ✓ |
| `invoice:read` | ✓ | ✓ | future | ✓ |
| `invoice:extract` | ✓ | ✓ | — | ✓ |
| `invoice:validate` | ✓ | ✓ | — | ✓ |
| `validation:read` | ✓ | ✓ | future | ✓ |
| `workflow:route` | — | ✓ | — | ✓ |
| `workflow:read` | ✓ | ✓ | future | ✓ |
| `review:read` | limited queue | ✓ | future | ✓ |
| `review:resolve` | — | ✓ | — | ✓, not approval |
| `audit:read` | — | limited | future | ✓ |
| `user:manage` | — | — | — | ✓ |
| `role:manage` | — | — | — | ✓ |
| `approval:decide` | — | — | PR-02 only | — |

Admin must not receive automatic financial approval authority. Approver is a
future role only; PR-01 does not add approval states or endpoints.

## Authorization rules

- Deny by default at route and service layers.
- Never use a UUID, filename, query filter, or frontend route as authorization.
- Apply company/account scope before returning list totals or detail records.
- Do not let operators enumerate another company’s invoice IDs through 403/404
  differences without an intentional documented policy.
- Return only fields allowed for the role; invoice PDFs and raw extraction text
  require explicit permission.
- Re-evaluate role/account status for every sensitive command or use short-lived
  sessions with immediate revocation checks.
- Role revocation is linearized at principal resolution: a revocation committed
  before that check denies the request; a request already past authorization may
  finish its current transaction, but subsequent requests are denied.

## Enforcement and migration rules

- `401` means no valid browser session or service credential. `403` means a
  valid principal lacks the permission or object/company scope. The response
  policy must avoid leaking whether another tenant’s UUID exists.
- Existing frontend tests must use explicit fake authenticated principals,
  never a hidden Admin default. Existing direct service tests must pass an
  explicit system or test actor context after service signatures change.
- `POST /workflow/review` must bind `resolution_request_id` to the authenticated
  actor and command fingerprint. A different actor using an existing key must
  receive a conflict; a retry by the same actor must remain idempotent.
- The n8n queue reader receives only `review:queue` list access. It cannot read
  case detail, call review resolution, user management, role management,
  invoice upload, or approval operations.
