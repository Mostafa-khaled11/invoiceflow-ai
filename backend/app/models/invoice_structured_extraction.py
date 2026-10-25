"""Persistence model for LLM-backed structured invoice extraction."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice
    from app.models.invoice_text_extraction import InvoiceTextExtraction


class StructuredExtractionStatus(StrEnum):
    """Provider extraction outcomes, separate from invoice lifecycle states."""

    SUCCESS = "success"
    FAILED = "failed"


class InvoiceStructuredExtraction(Base):
    """Auditable structured extraction result and provider provenance."""

    __tablename__ = "invoice_structured_extractions"
    __table_args__ = (
        CheckConstraint(
            "status in ('success', 'failed')",
            name="ck_invoice_structured_extractions_status",
        ),
        UniqueConstraint(
            "text_extraction_id",
            "provider",
            "model",
            "prompt_version",
            "schema_version",
            name="uq_invoice_structured_extractions_identity",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False
    )
    text_extraction_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("invoice_text_extractions.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    structured_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    raw_model_output: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    invoice: Mapped["Invoice"] = relationship(back_populates="structured_extractions")
    text_extraction: Mapped["InvoiceTextExtraction"] = relationship(
        back_populates="structured_extractions"
    )
