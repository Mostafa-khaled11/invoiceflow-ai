"""Deterministic aggregation of rule results."""

from __future__ import annotations

from collections import Counter

from .models import (
    FinalValidationDecision,
    ValidationRuleResult,
    ValidationRuleStatus,
    ValidationSeverity,
)


def aggregate_decision(
    results: list[ValidationRuleResult],
) -> tuple[FinalValidationDecision, dict[str, int]]:
    """Apply the v1 policy: financial mismatches require review, not blocking."""

    counts = Counter()
    for result in results:
        counts[f"{result.rule_id}.{result.status.value}"] += 1
        counts[f"status.{result.status.value}"] += 1
    if any(
        result.status == ValidationRuleStatus.FAIL
        and result.metadata.get("blocking") is True
        for result in results
    ):
        decision = FinalValidationDecision.BLOCKED
    elif any(result.status == ValidationRuleStatus.FAIL for result in results):
        decision = FinalValidationDecision.REVIEW_REQUIRED
    elif any(
        result.status == ValidationRuleStatus.NOT_EVALUABLE
        and result.severity in (ValidationSeverity.HIGH, ValidationSeverity.CRITICAL)
        for result in results
    ):
        decision = FinalValidationDecision.REVIEW_REQUIRED
    elif any(
        result.status == ValidationRuleStatus.NOT_EVALUABLE
        and result.severity in (ValidationSeverity.WARNING, ValidationSeverity.MEDIUM)
        for result in results
    ):
        decision = FinalValidationDecision.WARNING
    else:
        decision = FinalValidationDecision.PASS
    return decision, dict(sorted(counts.items()))
