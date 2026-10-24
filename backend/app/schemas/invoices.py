"""Pydantic models for the invoice intake API."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.invoice import InvoiceStatus


class InvoiceResponse(BaseModel):
    """Public invoice metadata returned by intake and detail endpoints."""

    invoice_id: UUID
    filename: str = Field(examples=["invoice.pdf"])
    content_type: str = Field(examples=["application/pdf"])
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: InvoiceStatus = InvoiceStatus.RECEIVED
    created_at: datetime


class InvoiceDuplicateDetail(BaseModel):
    """Body of the 409 response when the same PDF was already stored."""

    message: Literal["Invoice already exists for the submitted content."]
    existing_invoice_id: UUID
