"""API response models for the initial invoice workflow lifecycle."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.invoice_workflow import (
    ReviewCaseStatus,
    ReviewResolution,
    WorkflowState,
)
from app.services.business_validation.models import FinalValidationDecision


class WorkflowReviewCaseResponse(BaseModel):
    """Review-case summary attached to a needs-review workflow."""

    review_case_id: UUID
    status: ReviewCaseStatus
    resolution: ReviewResolution | None
    resolved_at: datetime | None = None
    resolution_request_id: str | None = None


class InvoiceReviewResolutionRequest(BaseModel):
    """Human-review command payload."""

    decision: ReviewResolution
    resolution_request_id: UUID
    note: str | None = None


class InvoiceWorkflowResponse(BaseModel):
    """Current workflow state and the validation context that produced it."""

    workflow_id: UUID
    validation_id: UUID
    state: WorkflowState
    state_version: int
    created_at: datetime
    updated_at: datetime
    validation_decision: FinalValidationDecision
    ruleset_version: str
    review_case: WorkflowReviewCaseResponse | None = None


class InvoiceWorkflowRouteResponse(InvoiceWorkflowResponse):
    """Workflow response including whether this request created the workflow."""

    created: bool
