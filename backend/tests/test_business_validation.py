"""Unit tests for the deterministic business-validation core."""

from __future__ import annotations

from copy import deepcopy

from app.schemas.structured_extractions import StructuredInvoiceData
from app.services.business_validation import (
    FinalValidationDecision,
    ValidationRuleStatus,
    validate_invoice,
)
from app.services.business_validation.registry import RULE_REGISTRY
from app.services.business_validation.rules import (
    TaxPresentation,
    derive_tax_presentation,
)


def make_invoice(
    *,
    summary_tax: bool = False,
    discount_total: str | None = "10.00",
    shipping_total: str | None = "5.00",
    total_amount: str = "104.00",
    line_total: str = "99.00",
    tax_total: str | None = "9.00",
    warnings: list[str] | None = None,
) -> StructuredInvoiceData:
    return StructuredInvoiceData.model_validate(
        {
            "vendor_name": "Example Vendor",
            "vendor_tax_id": None,
            "invoice_number": "INV-001",
            "invoice_date": "2026-01-01",
            "due_date": "2026-01-31",
            "purchase_order_number": None,
            "currency": "USD",
            "subtotal": "100.00",
            "discount_total": discount_total,
            "tax_total": tax_total,
            "shipping_total": shipping_total,
            "total_amount": total_amount,
            "line_items": [
                {
                    "description": "Service",
                    "quantity": "2.00",
                    "unit_price": "50.00",
                    "discount": "10.00" if discount_total is not None else None,
                    "tax": None if summary_tax else "9.00",
                    "line_total": "90.00" if summary_tax else line_total,
                }
            ],
            "warnings": warnings or [],
        }
    )


def result(report, rule_id: str):
    return next(item for item in report.rule_results if item.rule_id == rule_id)


def test_valid_complete_line_tax_invoice_passes() -> None:
    report = validate_invoice(make_invoice())

    assert report.final_decision == FinalValidationDecision.PASS
    assert all(item.status == ValidationRuleStatus.PASS for item in report.rule_results)
    assert result(report, "FIN001").metadata["tax_presentation"] == "LINE_ITEM"


def test_summary_tax_is_derived_and_tax_total_is_not_inferred() -> None:
    report = validate_invoice(
        make_invoice(summary_tax=True, total_amount="104.00", line_total="90.00")
    )

    assert (
        derive_tax_presentation(make_invoice(summary_tax=True))
        == TaxPresentation.SUMMARY
    )
    assert result(report, "FIN001").status == ValidationRuleStatus.PASS
    assert result(report, "FIN004").status == ValidationRuleStatus.NOT_EVALUABLE
    assert report.final_decision == FinalValidationDecision.WARNING


def test_wrong_line_total_under_summary_tax_fails() -> None:
    data = make_invoice(summary_tax=True)
    data.line_items[0].line_total = "91.00"

    assert result(validate_invoice(data), "FIN001").status == ValidationRuleStatus.FAIL


def test_line_item_arithmetic_failure_requires_review() -> None:
    report = validate_invoice(make_invoice(line_total="100.00"))

    assert result(report, "FIN001").status == ValidationRuleStatus.FAIL
    assert result(report, "FIN001").actual_value == "100.00"
    assert result(report, "FIN001").expected_value == "99.00"
    assert report.final_decision == FinalValidationDecision.REVIEW_REQUIRED


def test_subtotal_failure_is_not_repaired() -> None:
    data = make_invoice()
    data.subtotal = "101.00"
    before = data.model_dump()

    report = validate_invoice(data)

    assert result(report, "FIN002").status == ValidationRuleStatus.FAIL
    assert data.model_dump() == before
    assert report.final_decision == FinalValidationDecision.REVIEW_REQUIRED


def test_discount_total_failure_is_detected() -> None:
    data = make_invoice()
    data.discount_total = "11.00"

    report = validate_invoice(data)

    assert result(report, "FIN003").status == ValidationRuleStatus.FAIL


def test_tax_total_failure_is_detected_for_line_tax() -> None:
    data = make_invoice()
    data.tax_total = "10.00"

    report = validate_invoice(data)

    assert result(report, "FIN004").status == ValidationRuleStatus.FAIL


def test_grand_total_failure_is_detected() -> None:
    report = validate_invoice(make_invoice(total_amount="105.00"))

    assert result(report, "FIN005").status == ValidationRuleStatus.FAIL
    assert result(report, "FIN005").difference == "1.00"


def test_null_optional_components_are_not_converted_to_zero() -> None:
    report = validate_invoice(
        make_invoice(
            discount_total=None,
            shipping_total=None,
            total_amount="109.00",
            line_total="109.00",
        )
    )

    assert result(report, "FIN003").status == ValidationRuleStatus.PASS
    assert result(report, "FIN005").status == ValidationRuleStatus.PASS
    assert result(report, "FIN005").metadata["discount_source"] == "ABSENT_NULL"
    assert result(report, "FIN005").metadata["shipping_source"] == "ABSENT_NULL"
    assert report.final_decision == FinalValidationDecision.PASS


def test_explicit_zero_is_distinct_from_null() -> None:
    data = make_invoice(
        discount_total="0.00", shipping_total="0.00", total_amount="109.00"
    )
    data.line_items[0].discount = "0.00"
    data.line_items[0].tax = "9.00"
    data.line_items[0].line_total = "109.00"

    report = validate_invoice(data)

    assert result(report, "FIN001").status == ValidationRuleStatus.PASS
    assert result(report, "FIN003").status == ValidationRuleStatus.PASS
    assert result(report, "FIN005").status == ValidationRuleStatus.PASS


def test_null_discount_with_non_null_line_discount_fails() -> None:
    data = make_invoice(
        discount_total=None, shipping_total="5.00", total_amount="104.00"
    )
    data.line_items[0].discount = "10.00"

    assert result(validate_invoice(data), "FIN003").status == ValidationRuleStatus.FAIL


def test_null_shipping_contributes_zero_without_mutation() -> None:
    data = make_invoice(shipping_total=None, total_amount="99.00")
    before = data.model_dump()

    report = validate_invoice(data)

    grand_total = result(report, "FIN005")
    assert grand_total.status == ValidationRuleStatus.PASS
    assert grand_total.metadata["shipping_contribution"] == "0.00"
    assert data.model_dump() == before


def test_wrong_grand_total_with_null_optional_components_fails() -> None:
    data = make_invoice(discount_total=None, shipping_total=None, total_amount="100.00")

    assert result(validate_invoice(data), "FIN005").status == ValidationRuleStatus.FAIL


def test_missing_line_evidence_is_not_evaluable() -> None:
    data = make_invoice()
    data.line_items[0].quantity = None
    data.line_items[0].unit_price = None

    report = validate_invoice(data)

    assert result(report, "FIN001").status == ValidationRuleStatus.NOT_EVALUABLE
    assert result(report, "FIN002").status == ValidationRuleStatus.NOT_EVALUABLE


def test_missing_due_date_is_not_a_failure() -> None:
    data = make_invoice()
    data.due_date = None

    report = validate_invoice(data)

    assert result(report, "VAL002").status == ValidationRuleStatus.NOT_EVALUABLE
    assert result(report, "VAL002").severity.value == "INFO"
    assert report.final_decision == FinalValidationDecision.PASS


def test_due_date_before_invoice_date_requires_review() -> None:
    data = make_invoice()
    data.due_date = "2025-12-31"

    report = validate_invoice(data)

    assert result(report, "VAL002").status == ValidationRuleStatus.FAIL
    assert report.final_decision == FinalValidationDecision.REVIEW_REQUIRED


def test_same_day_due_date_passes() -> None:
    data = make_invoice()
    data.due_date = data.invoice_date

    assert result(validate_invoice(data), "VAL002").status == ValidationRuleStatus.PASS


def test_supported_ambiguous_currency_is_not_missing() -> None:
    data = make_invoice(warnings=["ambiguous_currency"])
    data.currency = None

    assert result(validate_invoice(data), "VAL001").status == ValidationRuleStatus.PASS


def test_currency_missing_without_supported_warning_fails() -> None:
    data = make_invoice()
    data.currency = None

    assert result(validate_invoice(data), "VAL001").status == ValidationRuleStatus.FAIL


def test_negative_value_is_outside_supported_contract() -> None:
    data = make_invoice()
    data.total_amount = "-1.00"

    assert result(validate_invoice(data), "FIN006").status == ValidationRuleStatus.FAIL


def test_validation_is_deterministic_and_does_not_mutate() -> None:
    data = make_invoice()
    before = deepcopy(data.model_dump())

    first = validate_invoice(data).model_dump(mode="json")
    second = validate_invoice(data).model_dump(mode="json")

    assert first == second
    assert data.model_dump() == before


def test_registry_is_explicit_and_complete() -> None:
    assert [definition.rule_id for definition in RULE_REGISTRY] == [
        "FIN001",
        "FIN002",
        "FIN003",
        "FIN004",
        "FIN005",
        "FIN006",
        "VAL001",
        "VAL002",
    ]
