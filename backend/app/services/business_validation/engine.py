"""Read-only deterministic validation engine."""

from __future__ import annotations

from app.schemas.structured_extractions import StructuredInvoiceData

from .decision import aggregate_decision
from .models import RULESET_VERSION, ValidationReport
from .registry import evaluate_registry


def validate_invoice(
    data: StructuredInvoiceData, *, evaluated_at: str | None = None
) -> ValidationReport:
    """Validate one structured invoice without mutating or repairing it."""

    results = evaluate_registry(data)
    decision, counts = aggregate_decision(results)
    return ValidationReport(
        ruleset_version=RULESET_VERSION,
        evaluated_at=evaluated_at,
        rule_results=results,
        aggregate_counts=counts,
        final_decision=decision,
    )
