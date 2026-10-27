"""Typed models for deterministic business validation results."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

RULESET_VERSION = "invoice-validation-v1"


class ValidationRuleStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_EVALUABLE = "NOT_EVALUABLE"


class ValidationSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FinalValidationDecision(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    BLOCKED = "BLOCKED"


FINAL_DECISIONS = tuple(FinalValidationDecision)


class ValidationRuleResult(BaseModel):
    """Explainable result for one registered validation rule."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    rule_name: str
    status: ValidationRuleStatus
    severity: ValidationSeverity
    message: str
    actual_value: str | None = None
    expected_value: str | None = None
    difference: str | None = None
    affected_fields: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    """Complete audit-friendly validation report without raw invoice text."""

    model_config = ConfigDict(extra="forbid")

    ruleset_version: str = RULESET_VERSION
    evaluated_at: str | None = None
    rule_results: list[ValidationRuleResult]
    aggregate_counts: dict[str, int]
    final_decision: FinalValidationDecision
