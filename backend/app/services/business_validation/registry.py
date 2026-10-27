"""Explicit registry for the v1 validation rules."""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.structured_extractions import StructuredInvoiceData

from .models import ValidationRuleResult
from .rules import (
    RuleFunction,
    date_logic,
    discount_total_consistency,
    financial_value_integrity,
    grand_total_consistency,
    line_item_arithmetic,
    required_fields,
    subtotal_consistency,
    tax_total_consistency,
)


@dataclass(frozen=True, slots=True)
class ValidationRuleDefinition:
    rule_id: str
    rule_name: str
    evaluate: RuleFunction


RULE_REGISTRY: tuple[ValidationRuleDefinition, ...] = (
    ValidationRuleDefinition("FIN001", "LINE_ITEM_ARITHMETIC", line_item_arithmetic),
    ValidationRuleDefinition("FIN002", "SUBTOTAL_CONSISTENCY", subtotal_consistency),
    ValidationRuleDefinition(
        "FIN003", "DISCOUNT_TOTAL_CONSISTENCY", discount_total_consistency
    ),
    ValidationRuleDefinition("FIN004", "TAX_TOTAL_CONSISTENCY", tax_total_consistency),
    ValidationRuleDefinition(
        "FIN005", "GRAND_TOTAL_CONSISTENCY", grand_total_consistency
    ),
    ValidationRuleDefinition(
        "FIN006", "FINANCIAL_VALUE_INTEGRITY", financial_value_integrity
    ),
    ValidationRuleDefinition("VAL001", "REQUIRED_FIELDS", required_fields),
    ValidationRuleDefinition("VAL002", "DATE_LOGIC", date_logic),
)


def evaluate_registry(data: StructuredInvoiceData) -> list[ValidationRuleResult]:
    return [definition.evaluate(data) for definition in RULE_REGISTRY]
