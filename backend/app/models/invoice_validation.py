"""Persistence models for deterministic business validation runs."""

from datetime import datetime
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
    from app.models.invoice import Invoice
    from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
    from app.models.invoice_workflow import InvoiceWorkflow


class InvoiceValidation(Base):
    """One deterministic validation execution against one structured extraction.

    Idempotent per (structured_extraction_id, ruleset_version), so re-running
    validation for the same extraction under the same ruleset returns the
    stored run instead of creating a duplicate.
    """

    __tablename__ = "invoice_validations"
    __table_args__ = (
        CheckConstraint(
            "final_decision in ('PASS', 'WARNING', 'REVIEW_REQUIRED', 'BLOCKED')",
            name="ck_invoice_validations_final_decision",
        ),
        UniqueConstraint(
            "structured_extraction_id",
            "ruleset_version",
            name="uq_invoice_validations_extraction_ruleset",
        ),
        Index("ix_invoice_validations_invoice_id", "invoice_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    structured_extraction_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("invoice_structured_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ruleset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    final_decision: Mapped[str] = mapped_column(String(32), nullable=False)
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False)
    not_evaluable_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    invoice: Mapped["Invoice"] = relationship(back_populates="validations")
    structured_extraction: Mapped["InvoiceStructuredExtraction"] = relationship(
        back_populates="validations"
    )
    findings: Mapped[list["InvoiceValidationFinding"]] = relationship(
        back_populates="validation",
        cascade="all, delete-orphan",
        order_by="InvoiceValidationFinding.sequence_number",
    )
    workflow: Mapped["InvoiceWorkflow | None"] = relationship(
        back_populates="validation",
        uselist=False,
        passive_deletes=True,
    )


class InvoiceValidationFinding(Base):
    """Explainable result for one rule within a validation run.

    A foreign key from a finding reaches the validated extraction through the
    parent run, so findings never drift away from their source.
    """

    __tablename__ = "invoice_validation_findings"
    __table_args__ = (
        CheckConstraint(
            "status in ('PASS', 'FAIL', 'NOT_EVALUABLE')",
            name="ck_invoice_validation_findings_status",
        ),
        CheckConstraint(
            "severity in ('INFO', 'WARNING', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_invoice_validation_findings_severity",
        ),
        UniqueConstraint(
            "validation_id",
            "rule_id",
            name="uq_invoice_validation_findings_rule",
        ),
        Index("ix_invoice_validation_findings_validation_id", "validation_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    validation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("invoice_validations.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actual_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    difference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    affected_fields: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    # `metadata` is a reserved Declarative attribute; the Python-side access
    # name is `meta`, mapped onto the `metadata` database column.
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    validation: Mapped["InvoiceValidation"] = relationship(back_populates="findings")
