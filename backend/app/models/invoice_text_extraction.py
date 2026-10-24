"""Persistence model for deterministic PDF text extraction results."""

from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.invoice import Invoice


class InvoiceTextExtractionStatus(StrEnum):
    """Persisted outcomes of the digital-text extraction baseline."""

    TEXT_EXTRACTED = "text_extracted"
    NEEDS_OCR = "needs_ocr"
    EXTRACTION_FAILED = "extraction_failed"


class InvoiceTextExtraction(Base):
    """One persisted extraction outcome per invoice."""

    __tablename__ = "invoice_text_extractions"
    __table_args__ = (
        CheckConstraint(
            "status in ('text_extracted', 'needs_ocr', 'extraction_failed')",
            name="ck_invoice_text_extractions_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    invoice_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    pages_with_text: Mapped[int] = mapped_column(Integer, nullable=False)
    character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    non_whitespace_character_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    processing_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    invoice: Mapped["Invoice"] = relationship(back_populates="text_extraction")
