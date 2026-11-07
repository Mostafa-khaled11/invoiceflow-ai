"""Durable review-command idempotency and security-audit records."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewCommandIdempotency(Base):
    """Persisted identity and result envelope for a review command."""

    __tablename__ = "review_command_idempotency"
    __table_args__ = (
        CheckConstraint(
            "length(actor_scope_key) between 1 and 160",
            name="ck_review_command_actor_scope_key_length",
        ),
        CheckConstraint(
            "length(request_key) between 1 and 128",
            name="ck_review_command_request_key_length",
        ),
        CheckConstraint(
            "fingerprint_version = 'review-resolution-v1'",
            name="ck_review_command_fingerprint_version",
        ),
        CheckConstraint(
            "length(command_fingerprint) = 64",
            name="ck_review_command_fingerprint_length",
        ),
        CheckConstraint(
            "status in ('PENDING', 'COMPLETED')",
            name="ck_review_command_status",
        ),
        UniqueConstraint(
            "actor_scope_key",
            "request_key",
            name="uq_review_command_actor_request",
        ),
        Index("ix_review_command_invoice_id", "invoice_id"),
        Index("ix_review_command_created_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    actor_scope_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint_version: Mapped[str] = mapped_column(String(64), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("invoices.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("invoice_workflows.id", ondelete="RESTRICT"), nullable=False
    )
    review_case_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("review_cases.id", ondelete="RESTRICT"), nullable=False
    )
    result_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("workflow_events.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="PENDING"
    )
    result_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    result_resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SecurityAuditEvent(Base):
    """Sanitized security event attributed to a trusted actor context."""

    __tablename__ = "security_audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type <> ''",
            name="ck_security_audit_event_type_nonempty",
        ),
        CheckConstraint(
            "actor_type in ('human', 'service', 'system')",
            name="ck_security_audit_actor_type",
        ),
        Index("ix_security_audit_events_created_at", "created_at"),
        Index("ix_security_audit_events_target", "target_type", "target_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    correlation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
