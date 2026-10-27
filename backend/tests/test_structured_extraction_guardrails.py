"""Offline tests for deterministic structured-extraction guardrails."""

from __future__ import annotations

import pytest
from app.services.structured_extraction_guardrails import (
    apply_structured_extraction_guardrails,
    canonicalize_monetary_value,
    extract_labeled_date,
)

from tests.test_structured_invoice_extraction import (
    FakeProvider,
    _pdf_with_text,
    _upload,
    _use_provider,
    _valid_data,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("$658.00", "658.00"),
        ("$2,226.00", "2226.00"),
        ("USD 890.50", "890.50"),
        ("890.50 USD", "890.50"),
        ("- $2,226.00", "-2226.00"),
    ],
)
def test_monetary_canonicalization(source: str, expected: str) -> None:
    assert canonicalize_monetary_value(source) == expected


def test_monetary_null_and_malformed_values_are_not_guessed() -> None:
    assert canonicalize_monetary_value(None) is None
    malformed = "approximately 658.00"
    assert canonicalize_monetary_value(malformed) == malformed


def test_line_item_monetary_fields_are_normalized() -> None:
    result = apply_structured_extraction_guardrails(
        {
            "currency": "USD",
            "subtotal": "$658.00",
            "discount_total": None,
            "tax_total": "$98.70",
            "shipping_total": None,
            "total_amount": "$756.70",
            "line_items": [
                {
                    "description": "Service",
                    "quantity": "2",
                    "unit_price": "$329.00",
                    "discount": None,
                    "tax": "USD 98.70",
                    "line_total": "$756.70",
                }
            ],
        },
        "",
    )
    item = result.data["line_items"][0]
    assert item["unit_price"] == "329.00"
    assert item["tax"] == "98.70"
    assert item["line_total"] == "756.70"
    assert "line_items[0].unit_price" in result.normalized_fields


@pytest.mark.parametrize(
    ("field", "text", "expected"),
    [
        ("invoice_date", "Invoice Date: 2026-03-14", "2026-03-14"),
        ("invoice_date", "Document Date: March 14, 2026", "2026-03-14"),
        ("invoice_date", "Invoice Date: 14 Mar 2026", "2026-03-14"),
        ("invoice_date", "Invoice Date: 14/03/2026", "2026-03-14"),
        ("due_date", "Due Date: 13/04/2026", "2026-04-13"),
    ],
)
def test_labeled_date_fallback_parses_supported_explicit_dates(
    field: str, text: str, expected: str
) -> None:
    assert extract_labeled_date(text, field) == expected


def test_ambiguous_missing_and_unrelated_dates_remain_unresolved() -> None:
    assert extract_labeled_date("Invoice Date: 07/02/2026", "invoice_date") is None
    assert extract_labeled_date("Invoice Number: INV-2026-031", "invoice_date") is None
    assert extract_labeled_date("Payment terms: 30 days", "due_date") is None


def test_fallback_only_fills_null_and_does_not_overwrite_valid_date() -> None:
    result = apply_structured_extraction_guardrails(
        {"invoice_date": "2026-01-01", "due_date": None},
        "Invoice Date: 14/03/2026\nDue Date: 13/04/2026",
    )
    assert result.data["invoice_date"] == "2026-01-01"
    assert result.data["due_date"] == "2026-04-13"
    assert result.fallback_fields == ("due_date",)


def test_guardrails_do_not_introduce_values_for_absent_fields() -> None:
    result = apply_structured_extraction_guardrails(
        {"vendor_tax_id": None, "purchase_order_number": None},
        "Invoice Number: INV-2026-001\nPayment terms: 30 days",
    )
    assert result.data["vendor_tax_id"] is None
    assert result.data["purchase_order_number"] is None
    assert result.fallback_fields == ()


def test_integration_canonicalizes_invoice_011_like_prediction(client) -> None:
    provider = FakeProvider(
        _valid_data(
            invoice_date=None,
            subtotal="$658.00",
            tax_total="$98.70",
            shipping_total="$35.00",
            total_amount="$791.70",
            line_items=[
                {
                    "description": "Platform Configuration Services",
                    "quantity": "2",
                    "unit_price": "$329.00",
                    "discount": None,
                    "tax": "$98.70",
                    "line_total": "$756.70",
                }
            ],
        )
    )
    _use_provider(client, provider)
    invoice_id = _upload(
        client,
        _pdf_with_text(
            [
                "Vendor: Harbor Point Analytics\nInvoice Date: Feb 10, 2026\n"
                "Subtotal: $658.00 Total: $791.70"
            ]
        ),
    )
    client.post(f"/api/v1/invoices/{invoice_id}/extract-text")
    response = client.post(f"/api/v1/invoices/{invoice_id}/extract-structured")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["subtotal"] == "658.00"
    assert data["total_amount"] == "791.70"
    assert data["line_items"][0]["unit_price"] == "329.00"
    assert data["invoice_date"] == "2026-02-10"


def test_integration_recovers_invoice_012_like_labeled_dates_without_hallucination(
    client,
) -> None:
    provider = FakeProvider(_valid_data(invoice_date=None, due_date=None))
    _use_provider(client, provider)
    invoice_id = _upload(
        client,
        _pdf_with_text(
            [
                "Invoice Date: 14/03/2026\nDue Date: 13/04/2026\n"
                "Vendor Tax ID: absent and no purchase order"
            ]
        ),
    )
    client.post(f"/api/v1/invoices/{invoice_id}/extract-text")
    response = client.post(f"/api/v1/invoices/{invoice_id}/extract-structured")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["invoice_date"] == "2026-03-14"
    assert data["due_date"] == "2026-04-13"
    assert data["vendor_tax_id"] == "US123456789"
