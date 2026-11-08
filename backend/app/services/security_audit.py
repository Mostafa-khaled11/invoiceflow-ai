"""Trusted, sanitized security-audit event persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.request_context import get_request_context
from app.models.audit import SecurityAuditEvent

AUDIT_EVENT_TYPES = frozenset(
    {
        "auth.login_started",
        "auth.login_succeeded",
        "auth.login_failed",
        "auth.logout",
        "session.created",
        "session.revoked",
        "role.granted",
        "role.revoked",
        "account.created",
        "identity.linked",
    }
)
_ALLOWED_METADATA_KEYS = frozenset(
    {
        "failure_category",
        "role",
        "session_count",
        "account_status",
        "auth_method",
    }
)
_MAX_METADATA_STRING = 128


def record_security_audit_event(
    session: Session,
    *,
    event_type: str,
    actor_type: str,
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
    request_id: UUID | None = None,
    correlation_id: UUID | None = None,
) -> SecurityAuditEvent:
    """Add one sanitized event to the caller-owned transaction.

    This function deliberately never commits. Only trusted application code
    supplies actor fields; HTTP request data is used only as bounded metadata
    after allowlist filtering.
    """

    if event_type not in AUDIT_EVENT_TYPES:
        raise ValueError(f"Unsupported security audit event type: {event_type}")
    if actor_type not in {"human", "service", "system"} or not actor_id:
        raise ValueError("Security audit events require a trusted actor identity")

    context = get_request_context()
    sanitized = _sanitize_metadata(metadata or {})
    event = SecurityAuditEvent(
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        request_id=request_id
        if request_id is not None
        else (context.request_id if context else None),
        correlation_id=correlation_id
        if correlation_id is not None
        else (context.correlation_id if context else None),
        target_type=target_type,
        target_id=None if target_id is None else str(target_id),
        meta=sanitized,
    )
    session.add(event)
    session.flush()
    return event


def _sanitize_metadata(metadata: dict[str, Any]) -> dict[str, str | int | bool]:
    sanitized: dict[str, str | int | bool] = {}
    for key, value in metadata.items():
        if key not in _ALLOWED_METADATA_KEYS:
            continue
        if isinstance(value, bool):
            sanitized[key] = value
        elif isinstance(value, int):
            sanitized[key] = value
        elif isinstance(value, str):
            sanitized[key] = value[:_MAX_METADATA_STRING]
    return sanitized
