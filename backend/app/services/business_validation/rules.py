"""Independently testable rules for the supported financial contract."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from enum import StrEnum

from app.schemas.structured_extractions import StructuredInvoiceData

from .models import (
    ValidationRuleResult,
    ValidationRuleStatus,
    ValidationSeverity,
)
from .money import ZERO, decimal_text, difference, parse_money, quantize_money


class TaxPresentation(StrEnum):
    LINE_ITEM = "LINE_ITEM"
    SUMMARY = "SUMMARY"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


RuleFunction = Callable[[StructuredInvoiceData], ValidationRuleResult]


def derive_tax_presentation(data: StructuredInvoiceData) -> TaxPresentation:
    """Derive tax mode conservatively without changing the input schema."""

    if not data.line_items:
        return TaxPresentation.UNKNOWN
    taxes = [item.tax for item in data.line_items]
    if all(value is not None for value in taxes):
        return TaxPresentation.LINE_ITEM
    if all(value is None for value in taxes) and data.tax_total is not None:
        return TaxPresentation.SUMMARY
    return TaxPresentation.UNKNOWN


def _result(
    rule_id: str,
    name: str,
    status: ValidationRuleStatus,
    severity: ValidationSeverity,
    message: str,
    *,
    actual: Decimal | None = None,
    expected: Decimal | None = None,
    affected: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> ValidationRuleResult:
    return ValidationRuleResult(
        rule_id=rule_id,
        rule_name=name,
        status=status,
        severity=severity,
        message=message,
        actual_value=decimal_text(actual),
        expected_value=decimal_text(expected),
        difference=(
            decimal_text(difference(actual, expected))
            if actual is not None and expected is not None
            else None
        ),
        affected_fields=affected or [],
        metadata=metadata or {},
    )


def _missing(
    rule_id: str,
    name: str,
    reason: str,
    fields: list[str],
    *,
    severity: ValidationSeverity = ValidationSeverity.WARNING,
    metadata: dict[str, object] | None = None,
) -> ValidationRuleResult:
    return _result(
        rule_id,
        name,
        ValidationRuleStatus.NOT_EVALUABLE,
        severity,
        reason,
        affected=fields,
        metadata=metadata,
    )


def line_item_arithmetic(data: StructuredInvoiceData) -> ValidationRuleResult:
    """FIN001: validate each line using inferred tax presentation."""

    mode = derive_tax_presentation(data)
    discount_values = [item.discount for item in data.line_items]
    if all(value is not None for value in discount_values):
        discount_mode = "LINE_ITEM"
    elif all(value is None for value in discount_values):
        discount_mode = "ABSENT_NULL"
    else:
        discount_mode = "LINE_ITEM_WITH_ABSENT_NULL"

    diagnostics: list[dict[str, object]] = []
    failures: list[str] = []
    unevaluable: list[str] = []
    first_failure: tuple[Decimal, Decimal] | None = None
    for index, item in enumerate(data.line_items):
        prefix = f"line_items[{index}]"
        quantity = parse_money(item.quantity)
        unit_price = parse_money(item.unit_price)
        line_total = parse_money(item.line_total)
        discount = parse_money(item.discount) or ZERO
        tax = (
            parse_money(item.tax)
            if mode == TaxPresentation.LINE_ITEM
            else ZERO
            if mode == TaxPresentation.SUMMARY
            else None
        )
        missing_fields = [
            field
            for field, value in (
                ("quantity", quantity),
                ("unit_price", unit_price),
                ("line_total", line_total),
            )
            if value is None
        ]
        if tax is None:
            missing_fields.append("tax_presentation")
        if missing_fields:
            unevaluable.append(prefix)
            diagnostics.append(
                {
                    "line": index,
                    "status": "NOT_EVALUABLE",
                    "reason": ", ".join(missing_fields),
                    "tax_presentation": mode.value,
                    "discount_presentation": discount_mode,
                }
            )
            continue
        assert (
            quantity is not None and unit_price is not None and line_total is not None
        )
        gross = quantize_money(quantity * unit_price)
        expected = quantize_money(gross - discount + tax)
        if expected != line_total:
            failures.append(prefix)
            if first_failure is None:
                first_failure = (line_total, expected)
            diagnostics.append(
                {
                    "line": index,
                    "status": "FAIL",
                    "expected": decimal_text(expected),
                    "actual": decimal_text(line_total),
                    "difference": decimal_text(difference(line_total, expected)),
                    "tax_presentation": mode.value,
                    "discount_presentation": discount_mode,
                }
            )
        else:
            diagnostics.append(
                {
                    "line": index,
                    "status": "PASS",
                    "expected": decimal_text(expected),
                    "actual": decimal_text(line_total),
                    "tax_presentation": mode.value,
                    "discount_presentation": discount_mode,
                }
            )

    metadata = {
        "tax_presentation": mode.value,
        "discount_presentation": discount_mode,
        "line_items": diagnostics,
    }
    if failures:
        actual, expected = first_failure or (None, None)
        return _result(
            "FIN001",
            "LINE_ITEM_ARITHMETIC",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.HIGH,
            "One or more line totals do not match the supported financial contract.",
            actual=actual,
            expected=expected,
            affected=failures,
            metadata=metadata,
        )
    if unevaluable:
        return _missing(
            "FIN001",
            "LINE_ITEM_ARITHMETIC",
            "Required line-item arithmetic evidence is unavailable.",
            unevaluable,
            metadata=metadata,
        )
    return _result(
        "FIN001",
        "LINE_ITEM_ARITHMETIC",
        ValidationRuleStatus.PASS,
        ValidationSeverity.INFO,
        "All line items match the supported financial contract.",
        metadata=metadata,
    )


def subtotal_consistency(data: StructuredInvoiceData) -> ValidationRuleResult:
    """FIN002: compare subtotal with rounded gross line sum."""

    subtotal = parse_money(data.subtotal)
    if subtotal is None:
        return _missing(
            "FIN002", "SUBTOTAL_CONSISTENCY", "Missing subtotal.", ["subtotal"]
        )
    gross_values: list[Decimal] = []
    missing: list[str] = []
    for index, item in enumerate(data.line_items):
        quantity = parse_money(item.quantity)
        unit_price = parse_money(item.unit_price)
        if quantity is None:
            missing.append(f"line_items[{index}].quantity")
        if unit_price is None:
            missing.append(f"line_items[{index}].unit_price")
        if quantity is not None and unit_price is not None:
            gross_values.append(quantize_money(quantity * unit_price))
    if missing:
        return _missing(
            "FIN002",
            "SUBTOTAL_CONSISTENCY",
            "Quantity and unit price are incomplete for one or more line items.",
            missing,
        )
    expected = quantize_money(sum(gross_values, ZERO))
    if expected != subtotal:
        return _result(
            "FIN002",
            "SUBTOTAL_CONSISTENCY",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.HIGH,
            "Declared subtotal does not match the rounded gross line sum.",
            actual=subtotal,
            expected=expected,
            affected=["subtotal", "line_items"],
        )
    return _result(
        "FIN002",
        "SUBTOTAL_CONSISTENCY",
        ValidationRuleStatus.PASS,
        ValidationSeverity.INFO,
        "Subtotal matches the rounded gross line sum.",
        actual=subtotal,
        expected=expected,
    )


def discount_total_consistency(data: StructuredInvoiceData) -> ValidationRuleResult:
    """FIN003: validate absolute line discounts only when fully evidenced."""

    total = parse_money(data.discount_total)
    all_line_discounts_absent = all(item.discount is None for item in data.line_items)
    any_line_discount_present = any(
        item.discount is not None for item in data.line_items
    )
    if total is None and all_line_discounts_absent:
        return _result(
            "FIN003",
            "DISCOUNT_TOTAL_CONSISTENCY",
            ValidationRuleStatus.PASS,
            ValidationSeverity.INFO,
            "Discount is consistently absent from the invoice.",
            metadata={"effective_discount_total": "0.00", "presentation": "ABSENT"},
        )
    if total is None and any_line_discount_present:
        return _result(
            "FIN003",
            "DISCOUNT_TOTAL_CONSISTENCY",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.HIGH,
            "Line discount evidence contradicts an absent invoice discount total.",
            affected=["discount_total", "line_items.discount"],
        )
    if total is None:
        return _missing(
            "FIN003",
            "DISCOUNT_TOTAL_CONSISTENCY",
            "Discount evidence is incomplete.",
            ["discount_total"],
        )
    if any(item.discount is None for item in data.line_items):
        return _missing(
            "FIN003",
            "DISCOUNT_TOTAL_CONSISTENCY",
            "Invoice discount total is present but line discount evidence is "
            "incomplete.",
            ["line_items.discount"],
        )
    expected = quantize_money(
        sum((parse_money(item.discount) or ZERO for item in data.line_items), ZERO)
    )
    if expected != total:
        return _result(
            "FIN003",
            "DISCOUNT_TOTAL_CONSISTENCY",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.HIGH,
            "Declared discount total does not match the absolute line discounts.",
            actual=total,
            expected=expected,
            affected=["discount_total", "line_items.discount"],
        )
    return _result(
        "FIN003",
        "DISCOUNT_TOTAL_CONSISTENCY",
        ValidationRuleStatus.PASS,
        ValidationSeverity.INFO,
        "Discount total matches the absolute line discounts.",
        actual=total,
        expected=expected,
    )


def tax_total_consistency(data: StructuredInvoiceData) -> ValidationRuleResult:
    """FIN004: compare tax totals only for complete line-level tax evidence."""

    total = parse_money(data.tax_total)
    if total is None:
        return _missing(
            "FIN004", "TAX_TOTAL_CONSISTENCY", "Missing tax total.", ["tax_total"]
        )
    mode = derive_tax_presentation(data)
    if mode != TaxPresentation.LINE_ITEM:
        return _missing(
            "FIN004",
            "TAX_TOTAL_CONSISTENCY",
            "Tax is summary-presented or its presentation cannot be determined safely.",
            ["line_items.tax"],
            metadata={"tax_presentation": mode.value},
        )
    expected = quantize_money(
        sum((parse_money(item.tax) or ZERO for item in data.line_items), ZERO)
    )
    if expected != total:
        return _result(
            "FIN004",
            "TAX_TOTAL_CONSISTENCY",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.HIGH,
            "Declared tax total does not match the complete line-level tax sum.",
            actual=total,
            expected=expected,
            affected=["tax_total", "line_items.tax"],
            metadata={"tax_presentation": mode.value},
        )
    return _result(
        "FIN004",
        "TAX_TOTAL_CONSISTENCY",
        ValidationRuleStatus.PASS,
        ValidationSeverity.INFO,
        "Tax total matches the complete line-level tax sum.",
        actual=total,
        expected=expected,
        metadata={"tax_presentation": mode.value},
    )


def grand_total_consistency(data: StructuredInvoiceData) -> ValidationRuleResult:
    """FIN005: validate grand total only when optional presence is explicit."""

    values = {
        field: parse_money(getattr(data, field))
        for field in (
            "subtotal",
            "discount_total",
            "tax_total",
            "shipping_total",
            "total_amount",
        )
    }
    required = ("subtotal", "tax_total", "total_amount")
    missing = [field for field in required if values[field] is None]
    if missing:
        return _missing(
            "FIN005",
            "GRAND_TOTAL_CONSISTENCY",
            "A required grand-total operand is missing.",
            missing,
        )
    assert values["subtotal"] is not None
    assert values["tax_total"] is not None
    assert values["total_amount"] is not None
    discount = values["discount_total"]
    shipping = values["shipping_total"]
    effective_discount = discount if discount is not None else ZERO
    effective_shipping = shipping if shipping is not None else ZERO
    expected = quantize_money(
        values["subtotal"]
        - effective_discount
        + values["tax_total"]
        + effective_shipping
    )
    actual = values["total_amount"]
    metadata = {
        "discount_contribution": decimal_text(effective_discount),
        "discount_source": "PRESENT" if discount is not None else "ABSENT_NULL",
        "shipping_contribution": decimal_text(effective_shipping),
        "shipping_source": "PRESENT" if shipping is not None else "ABSENT_NULL",
    }
    if expected != actual:
        return _result(
            "FIN005",
            "GRAND_TOTAL_CONSISTENCY",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.HIGH,
            "Declared total does not match the supported financial contract.",
            actual=actual,
            expected=expected,
            affected=[
                "total_amount",
                "subtotal",
                "discount_total",
                "tax_total",
                "shipping_total",
            ],
            metadata=metadata,
        )
    return _result(
        "FIN005",
        "GRAND_TOTAL_CONSISTENCY",
        ValidationRuleStatus.PASS,
        ValidationSeverity.INFO,
        "Grand total matches the supported financial contract.",
        actual=actual,
        expected=expected,
        metadata=metadata,
    )


def financial_value_integrity(data: StructuredInvoiceData) -> ValidationRuleResult:
    """FIN006: enforce non-negative values supported by the benchmark contract."""

    fields: list[tuple[str, str | None]] = [
        (field, getattr(data, field))
        for field in (
            "subtotal",
            "discount_total",
            "tax_total",
            "shipping_total",
            "total_amount",
        )
    ]
    for index, item in enumerate(data.line_items):
        fields.extend(
            (f"line_items[{index}].{field}", getattr(item, field))
            for field in ("quantity", "unit_price", "discount", "tax", "line_total")
        )
    negative = [
        field
        for field, value in fields
        if (parsed := parse_money(value)) is not None and parsed < ZERO
    ]
    if negative:
        return _result(
            "FIN006",
            "FINANCIAL_VALUE_INTEGRITY",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.MEDIUM,
            "Negative financial values are outside the supported benchmark contract.",
            affected=negative,
        )
    return _result(
        "FIN006",
        "FINANCIAL_VALUE_INTEGRITY",
        ValidationRuleStatus.PASS,
        ValidationSeverity.INFO,
        "All present financial values are finite and non-negative.",
    )


def required_fields(data: StructuredInvoiceData) -> ValidationRuleResult:
    """VAL001: validate critical extraction fields and supported currency ambiguity."""

    missing = [
        field
        for field in ("vendor_name", "invoice_number", "invoice_date", "total_amount")
        if getattr(data, field) is None
    ]
    ambiguity_warning = any(
        str(warning) == "ambiguous_currency" for warning in data.warnings
    )
    supported_ambiguity = data.currency is None and ambiguity_warning
    if data.currency is None and not supported_ambiguity:
        missing.append("currency")
    if missing:
        return _result(
            "VAL001",
            "REQUIRED_FIELDS",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.HIGH,
            "Required extraction fields are missing and no supported ambiguity "
            "explains them.",
            affected=missing,
            metadata={"supported_currency_ambiguity": supported_ambiguity},
        )
    return _result(
        "VAL001",
        "REQUIRED_FIELDS",
        ValidationRuleStatus.PASS,
        ValidationSeverity.INFO,
        "Critical extraction fields are present or explicitly covered by "
        "supported ambiguity.",
        metadata={"supported_currency_ambiguity": supported_ambiguity},
    )


def date_logic(data: StructuredInvoiceData) -> ValidationRuleResult:
    """VAL002: require due date not to precede invoice date when both exist."""

    if data.invoice_date is None or data.due_date is None:
        return _missing(
            "VAL002",
            "DATE_LOGIC",
            "Invoice date or due date is missing; date ordering is not evaluable.",
            [
                field
                for field in ("invoice_date", "due_date")
                if getattr(data, field) is None
            ],
            severity=ValidationSeverity.INFO,
        )
    if data.due_date < data.invoice_date:
        return _result(
            "VAL002",
            "DATE_LOGIC",
            ValidationRuleStatus.FAIL,
            ValidationSeverity.MEDIUM,
            "Due date precedes invoice date.",
            affected=["invoice_date", "due_date"],
        )
    return _result(
        "VAL002",
        "DATE_LOGIC",
        ValidationRuleStatus.PASS,
        ValidationSeverity.INFO,
        "Due date is on or after invoice date.",
        metadata={"same_day_allowed": True},
    )
