"""Centralized permission and principal resolution for protected API routes."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import Settings
from app.models.identity import (
    ApplicationRole,
    Role,
    UserAccount,
    UserRoleAssignment,
)
from app.services.authentication import AuthenticatedSession, load_authenticated_session


class Permission(StrEnum):
    """Named permissions enforced by routes and service boundaries."""

    SYSTEM_READ = "system:read"
    INVOICE_CREATE = "invoice:create"
    INVOICE_READ = "invoice:read"
    INVOICE_EXTRACT = "invoice:extract"
    INVOICE_VALIDATE = "invoice:validate"
    VALIDATION_READ = "validation:read"
    WORKFLOW_ROUTE = "workflow:route"
    WORKFLOW_READ = "workflow:read"
    REVIEW_QUEUE = "review:queue"
    REVIEW_READ = "review:read"
    REVIEW_RESOLVE = "review:resolve"
    AUDIT_READ = "audit:read"
    USER_MANAGE = "user:manage"
    ROLE_MANAGE = "role:manage"
    APPROVAL_DECIDE = "approval:decide"


ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    ApplicationRole.OPERATOR.value: frozenset(
        {
            Permission.INVOICE_CREATE,
            Permission.INVOICE_READ,
            Permission.INVOICE_EXTRACT,
            Permission.INVOICE_VALIDATE,
            Permission.VALIDATION_READ,
            Permission.WORKFLOW_READ,
            Permission.REVIEW_QUEUE,
        }
    ),
    ApplicationRole.REVIEWER.value: frozenset(
        {
            Permission.INVOICE_CREATE,
            Permission.INVOICE_READ,
            Permission.INVOICE_EXTRACT,
            Permission.INVOICE_VALIDATE,
            Permission.VALIDATION_READ,
            Permission.WORKFLOW_ROUTE,
            Permission.WORKFLOW_READ,
            Permission.REVIEW_QUEUE,
            Permission.REVIEW_READ,
            Permission.REVIEW_RESOLVE,
        }
    ),
    # Approver remains a future PR-02 role and receives no PR-01 command power.
    ApplicationRole.APPROVER.value: frozenset(),
    ApplicationRole.ADMIN.value: frozenset(
        {
            Permission.SYSTEM_READ,
            Permission.INVOICE_CREATE,
            Permission.INVOICE_READ,
            Permission.INVOICE_EXTRACT,
            Permission.INVOICE_VALIDATE,
            Permission.VALIDATION_READ,
            Permission.WORKFLOW_ROUTE,
            Permission.WORKFLOW_READ,
            Permission.REVIEW_QUEUE,
            Permission.REVIEW_READ,
            Permission.REVIEW_RESOLVE,
            Permission.AUDIT_READ,
            Permission.USER_MANAGE,
            Permission.ROLE_MANAGE,
        }
    ),
}


@dataclass(frozen=True)
class AuthorizationContext:
    """Trusted request principal and its effective permissions."""

    principal_type: str
    account_id: UUID | None
    account: UserAccount | None
    roles: frozenset[str]
    permissions: frozenset[Permission]
    service_name: str | None = None

    @property
    def is_service(self) -> bool:
        """Return whether this request uses a machine credential."""

        return self.principal_type == "service"

    @property
    def is_limited_review_reader(self) -> bool:
        """Return whether review access is limited to the operational queue."""

        return Permission.REVIEW_QUEUE in self.permissions and (
            Permission.REVIEW_READ not in self.permissions
        )

    def can(self, permission: Permission) -> bool:
        """Check one explicit named permission."""

        return permission in self.permissions


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _forbidden() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="The authenticated principal lacks this permission",
    )


def _service_context(settings: Settings, token: str) -> AuthorizationContext | None:
    configured_hash = settings.n8n_service_token_hash
    if not configured_hash or len(configured_hash) != 64:
        return None
    try:
        int(configured_hash, 16)
    except ValueError:
        return None
    candidate_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(candidate_hash, configured_hash.lower()):
        return None
    return AuthorizationContext(
        principal_type="service",
        account_id=None,
        account=None,
        roles=frozenset(),
        permissions=frozenset({Permission.REVIEW_QUEUE}),
        service_name=settings.n8n_service_name,
    )


def _human_context(
    session: Session,
    authenticated: AuthenticatedSession,
) -> AuthorizationContext:
    role_rows = session.execute(
        select(Role.name)
        .join(UserRoleAssignment, UserRoleAssignment.role_id == Role.id)
        .where(
            UserRoleAssignment.user_id == authenticated.account.id,
            UserRoleAssignment.revoked_at.is_(None),
        )
    ).all()
    roles = frozenset(row[0] for row in role_rows)
    permissions = frozenset(
        permission
        for role in roles
        for permission in ROLE_PERMISSIONS.get(role, frozenset())
    )
    return AuthorizationContext(
        principal_type="human",
        account_id=authenticated.account.id,
        account=authenticated.account,
        roles=roles,
        permissions=permissions,
    )


def resolve_principal(
    request: Request,
    session: Session,
) -> AuthorizationContext:
    """Resolve exactly one human session or n8n service credential."""

    settings: Settings = request.app.state.settings
    authorization = request.headers.get("authorization")
    if authorization is not None:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthorized()
        service = _service_context(settings, token.strip())
        if service is None:
            raise _unauthorized()
        return service

    authenticated = load_authenticated_session(session, request, settings)
    if authenticated is None:
        raise _unauthorized()
    return _human_context(session, authenticated)


def require_permission(permission: Permission):
    """Create a reusable FastAPI dependency for one permission."""

    def dependency(
        request: Request,
        session: Annotated[Session, Depends(get_db)],
    ) -> AuthorizationContext:
        principal = resolve_principal(request, session)
        if not principal.can(permission):
            raise _forbidden()
        return principal

    dependency.__name__ = f"require_{permission.value.replace(':', '_')}"
    dependency.required_permission = permission
    return dependency


require_invoice_create = require_permission(Permission.INVOICE_CREATE)
require_invoice_read = require_permission(Permission.INVOICE_READ)
require_invoice_extract = require_permission(Permission.INVOICE_EXTRACT)
require_invoice_validate = require_permission(Permission.INVOICE_VALIDATE)
require_validation_read = require_permission(Permission.VALIDATION_READ)
require_workflow_route = require_permission(Permission.WORKFLOW_ROUTE)
require_workflow_read = require_permission(Permission.WORKFLOW_READ)
require_review_queue = require_permission(Permission.REVIEW_QUEUE)
require_review_read = require_permission(Permission.REVIEW_READ)
require_review_resolve = require_permission(Permission.REVIEW_RESOLVE)
