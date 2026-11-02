"""API response models for operational human-review reads."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.invoice_workflow import (
    ReviewCaseStatus,
    ReviewResolution,
    WorkflowState,
)
from app.services.business_validation import FinalValidationDecision


class ReviewValidationSummary(BaseModel):
    """Compact explanation of why the validation requires attention."""

    decision: FinalValidationDecision
    ruleset_version: str
    finding_count: int
    failed_rule_codes: list[str]
    failed_severities: list[str]


class ReviewCaseResponse(BaseModel):
    """Operational review-case representation shared by queue and detail APIs."""

    review_case_id: UUID
    invoice_id: UUID
    workflow_id: UUID
    validation_id: UUID
    status: ReviewCaseStatus
    resolution: ReviewResolution | None
    workflow_state: WorkflowState
    state_version: int
    validation_decision: FinalValidationDecision
    ruleset_version: str
    created_at: datetime
    resolved_at: datetime | None
    reviewer_id: str | None
    reviewer_note: str | None
    resolution_request_id: str | None
    is_current: bool
    is_actionable: bool
    invoice_filename: str
    invoice_created_at: datetime


class ReviewCaseDetailResponse(ReviewCaseResponse):
    """Review-case response with a compact validation reason summary."""

    validation_summary: ReviewValidationSummary


class ReviewCasePageResponse(BaseModel):
    """Offset-paginated operational review queue."""

    items: list[ReviewCaseResponse]
    limit: int
    offset: int
    total: int
