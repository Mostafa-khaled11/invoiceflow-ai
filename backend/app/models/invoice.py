"""Invoice persistence model.

Deliberately limited to intake metadata: extraction fields (vendor, invoice
number, totals, dates, OCR text, AI output) belong to later tasks and must
not be added here until their schema tasks are designed.
"""

from datetime import datetime
from enum import StrEnum
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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InvoiceStatus(StrEnum):
    """Application-level lifecycle states persisted for invoices.

    Values are constrained at the database level by a CHECK constraint; new
    states require both this enum and the migration's constraint to move
    together.
    """

    RECEIVED = "received"


class Invoice(Base):
    """A privately stored invoice PDF plus its persisted intake metadata."""

    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("status in ('received')", name="ck_invoices_status"),
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
