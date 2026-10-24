"""Pydantic models for the invoice intake API."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.invoice import InvoiceStatus
from app.models.invoice_text_extraction import InvoiceTextExtractionStatus


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


class InvoiceTextExtractionResponse(BaseModel):
    """Metadata-only response for an invoice text extraction outcome."""

    invoice_id: UUID
    extraction_id: UUID
    status: InvoiceTextExtractionStatus
    extractor: str
    extractor_version: str
    page_count: int = Field(ge=0)
    pages_with_text: int = Field(ge=0)
    character_count: int = Field(ge=0)
    non_whitespace_character_count: int = Field(ge=0)
    error_code: str | None = None
    processing_ms: int = Field(ge=0)
