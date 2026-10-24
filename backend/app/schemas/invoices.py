"""Pydantic models for the invoice intake API."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class InvoiceUploadResponse(BaseModel):
    """Intake metadata returned after a successful invoice PDF validation."""

    upload_id: UUID
    filename: str = Field(examples=["invoice.pdf"])
    content_type: str = Field(examples=["application/pdf"])
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["validated"] = "validated"
