"""Pydantic models for the invoice business validation API."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.services.business_validation.models import (
    FinalValidationDecision,
    ValidationRuleStatus,
    ValidationSeverity,
)


class ValidationFindingResponse(BaseModel):
    """Explainable result of one rule inside a persisted validation run."""

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    rule_name: str
    status: ValidationRuleStatus
    severity: ValidationSeverity
    message: str
    actual_value: str | None = None
    expected_value: str | None = None
    difference: str | None = None
    affected_fields: list[str]
    metadata: dict[str, Any]


class InvoiceValidationResponse(BaseModel):
    """Persisted validation run for one structured extraction."""

    model_config = ConfigDict(extra="forbid")

    validation_id: UUID
    invoice_id: UUID
    structured_extraction_id: UUID
    ruleset_version: str
    final_decision: FinalValidationDecision
    pass_count: int = Field(ge=0)
    fail_count: int = Field(ge=0)
    not_evaluable_count: int = Field(ge=0)
    created_at: datetime
    findings: list[ValidationFindingResponse]
