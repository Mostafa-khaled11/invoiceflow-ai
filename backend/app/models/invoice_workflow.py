"""Persistence models for validation-aware workflow audit history."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.invoice_validation import InvoiceValidation


class WorkflowState(StrEnum):
    """Persisted workflow states approved for the initial workflow model."""

    READY = "READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    BLOCKED = "BLOCKED"
    EXCEPTION_ACCEPTED = "EXCEPTION_ACCEPTED"
    REJECTED = "REJECTED"


class WorkflowEventType(StrEnum):
    """Supported audit events for routing and human review resolution."""

    ROUTED = "ROUTED"
    EXCEPTION_ACCEPTED = "EXCEPTION_ACCEPTED"
    REJECTED = "REJECTED"


class ReviewCaseStatus(StrEnum):
    """Persisted review-case lifecycle states."""

    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class ReviewResolution(StrEnum):
    """Persisted review-case resolution values."""

    ACCEPT_EXCEPTION = "ACCEPT_EXCEPTION"
    REJECT = "REJECT"


_WORKFLOW_STATES = (
    "'READY', 'NEEDS_REVIEW', 'BLOCKED', 'EXCEPTION_ACCEPTED', 'REJECTED'"
)


class InvoiceWorkflow(Base):
    """One workflow run linked to exactly one validation run."""

    __tablename__ = "invoice_workflows"
    __table_args__ = (
        CheckConstraint(
            f"state in ({_WORKFLOW_STATES})",
            name="ck_invoice_workflows_state",
        ),
        CheckConstraint(
            "state_version >= 1",
            name="ck_invoice_workflows_state_version",
        ),
        UniqueConstraint(
            "validation_id",
            name="uq_invoice_workflows_validation_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    validation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("invoice_validations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    state_version: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=1, server_default="1"
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

    validation: Mapped["InvoiceValidation"] = relationship(
        back_populates="workflow",
        passive_deletes=True,
    )
    events: Mapped[list["WorkflowEvent"]] = relationship(
        back_populates="workflow",
        passive_deletes=True,
        order_by="WorkflowEvent.sequence_number",
    )
    review_case: Mapped["ReviewCase | None"] = relationship(
        back_populates="workflow",
        uselist=False,
        passive_deletes=True,
    )


class WorkflowEvent(Base):
    """Append-only audit event belonging to one workflow run."""

    __tablename__ = "workflow_events"
    __table_args__ = (
        CheckConstraint(
            "event_type in ('ROUTED', 'EXCEPTION_ACCEPTED', 'REJECTED')",
            name="ck_workflow_events_event_type",
        ),
        CheckConstraint(
            "sequence_number >= 1",
            name="ck_workflow_events_sequence_number",
        ),
        CheckConstraint(
            f"from_state is null or from_state in ({_WORKFLOW_STATES})",
            name="ck_workflow_events_from_state",
        ),
        CheckConstraint(
            f"to_state in ({_WORKFLOW_STATES})",
            name="ck_workflow_events_to_state",
        ),
        UniqueConstraint(
            "workflow_id",
            "sequence_number",
            name="uq_workflow_events_workflow_sequence",
        ),
        Index("ix_workflow_events_workflow_id", "workflow_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("invoice_workflows.id", ondelete="RESTRICT"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_state: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        nullable=False,
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workflow: Mapped["InvoiceWorkflow"] = relationship(back_populates="events")


class ReviewCase(Base):
    """At-most-one human-review record for a workflow run."""

    __tablename__ = "review_cases"
    __table_args__ = (
        CheckConstraint(
            "status in ('OPEN', 'RESOLVED')",
            name="ck_review_cases_status",
        ),
        CheckConstraint(
            "resolution is null or resolution in ('ACCEPT_EXCEPTION', 'REJECT')",
            name="ck_review_cases_resolution",
        ),
        UniqueConstraint(
            "workflow_id",
            name="uq_review_cases_workflow_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    workflow_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("invoice_workflows.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    resolution: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_request_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    workflow: Mapped["InvoiceWorkflow"] = relationship(back_populates="review_case")
