"""Versioned strict contract for structured invoice extraction."""

from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

DECIMAL_STRING = StrictStr | None


class WarningCode(StrEnum):
    """Small, controlled set of extraction uncertainty signals."""

    AMBIGUOUS_CURRENCY = "ambiguous_currency"
    AMBIGUOUS_INVOICE_DATE = "ambiguous_invoice_date"
    MISSING_INVOICE_NUMBER = "missing_invoice_number"
    MISSING_TOTAL = "missing_total"
    UNREADABLE_LINE_ITEMS = "unreadable_line_items"


class InvoiceLineItem(BaseModel):
    """A line item represented exactly as supported by the source text."""

    model_config = ConfigDict(extra="forbid")

    description: StrictStr | None
    quantity: DECIMAL_STRING
    unit_price: DECIMAL_STRING
    discount: DECIMAL_STRING
    tax: DECIMAL_STRING
    line_total: DECIMAL_STRING


class StructuredInvoiceData(BaseModel):
    """Invoice schema v1 returned by the structured-output provider."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: StrictStr | None
    vendor_tax_id: StrictStr | None
    invoice_number: StrictStr | None
    invoice_date: Annotated[StrictStr, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None
    due_date: Annotated[StrictStr, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None
    purchase_order_number: StrictStr | None
    currency: Annotated[StrictStr, Field(pattern=r"^[A-Z]{3}$")] | None
    subtotal: DECIMAL_STRING
    discount_total: DECIMAL_STRING
    tax_total: DECIMAL_STRING
    shipping_total: DECIMAL_STRING
    total_amount: DECIMAL_STRING
    line_items: list[InvoiceLineItem]
    warnings: list[WarningCode]

    @field_validator("invoice_date", "due_date")
    @classmethod
    def validate_calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value

    @field_validator(
        "subtotal",
        "discount_total",
        "tax_total",
        "shipping_total",
        "total_amount",
    )
    @classmethod
    def validate_decimal_string(cls, value: str | None) -> str | None:
        _parse_decimal(value)
        return value


class StructuredExtractionResponse(BaseModel):
    """Public response containing structured data and safe provenance."""

    invoice_id: UUID
    structured_extraction_id: UUID
    status: str
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    data: StructuredInvoiceData
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int | None


def _parse_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("must be a valid decimal string") from exc
    if not parsed.is_finite():
        raise ValueError("must be a finite decimal string")
    return parsed


def validate_structured_invoice_semantics(data: StructuredInvoiceData) -> None:
    """Recheck semantic constraints after provider/schema validation."""

    for field_name in (
        "subtotal",
        "discount_total",
        "tax_total",
        "shipping_total",
        "total_amount",
    ):
        _parse_decimal(getattr(data, field_name))

    for item in data.line_items:
        for value in (
            item.quantity,
            item.unit_price,
            item.discount,
            item.tax,
            item.line_total,
        ):
            _parse_decimal(value)

    for field_name in ("invoice_date", "due_date"):
        value = getattr(data, field_name)
        if value is not None:
            date.fromisoformat(value)

    if data.currency is not None and not (
        len(data.currency) == 3 and data.currency.isascii() and data.currency.isupper()
    ):
        raise ValueError("currency must be an uppercase three-letter code")
