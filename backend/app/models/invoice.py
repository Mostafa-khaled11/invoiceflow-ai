"""Invoice persistence model.

The invoice row retains intake metadata and lifecycle state; text and structured
extraction payloads live in their dedicated one-to-one/versioned tables.
"""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
    from app.models.invoice_text_extraction import InvoiceTextExtraction
    from app.models.invoice_validation import InvoiceValidation


class InvoiceStatus(StrEnum):
    """Application-level lifecycle states persisted for invoices.

    Values are constrained at the database level by a CHECK constraint; new
    states require both this enum and the migration's constraint to move
    together.
    """

    RECEIVED = "received"
    TEXT_EXTRACTED = "text_extracted"
    NEEDS_OCR = "needs_ocr"
    EXTRACTION_FAILED = "extraction_failed"
    STRUCTURED_EXTRACTED = "structured_extracted"


class Invoice(Base):
    """A privately stored invoice PDF plus its persisted intake metadata."""

    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(
            "status in ("
            "'received', 'text_extracted', 'needs_ocr', 'extraction_failed', "
            "'structured_extracted'"
            ")",
            name="ck_invoices_status",
        ),
        UniqueConstraint("sha256", name="uq_invoices_sha256"),
        UniqueConstraint("storage_key", name="uq_invoices_storage_key"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(64))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(32), default=InvoiceStatus.RECEIVED.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    text_extraction: Mapped["InvoiceTextExtraction | None"] = relationship(
        back_populates="invoice",
        uselist=False,
        cascade="all, delete-orphan",
    )
    structured_extractions: Mapped[list["InvoiceStructuredExtraction"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
    validations: Mapped[list["InvoiceValidation"]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
    )
