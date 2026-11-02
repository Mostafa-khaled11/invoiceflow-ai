"""Operational human-review read endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.invoice_workflow import (
    ReviewCaseStatus,
    ReviewResolution,
    WorkflowState,
)
from app.schemas.review_cases import (
    ReviewCaseDetailResponse,
    ReviewCasePageResponse,
    ReviewCaseResponse,
    ReviewValidationSummary,
)
from app.services.business_validation import FinalValidationDecision
from app.services.review_cases import (
    ReviewCaseRead,
    ReviewCaseReadError,
    get_review_case,
    list_review_cases,
)

router = APIRouter(prefix="/review-cases", tags=["review-cases"])
_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100


@router.get("", response_model=ReviewCasePageResponse)
def list_review_cases_endpoint(
    session: Annotated[Session, Depends(get_db)],
    status_filter: Annotated[ReviewCaseStatus | None, Query(alias="status")] = None,
    resolution: ReviewResolution | None = None,
    workflow_state: WorkflowState | None = None,
    invoice_id: UUID | None = None,
    include_historical: bool = False,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = _DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReviewCasePageResponse:
    """Return current actionable cases by default, or explicit history."""

    page = list_review_cases(
        session,
        status=status_filter,
        resolution=resolution,
        workflow_state=workflow_state,
        invoice_id=invoice_id,
        include_historical=include_historical,
        limit=limit,
        offset=offset,
    )
    return ReviewCasePageResponse(
        items=[_case_response(item) for item in page.items],
        limit=page.limit,
        offset=page.offset,
        total=page.total,
    )


@router.get("/{review_case_id}", response_model=ReviewCaseDetailResponse)
def get_review_case_endpoint(
    review_case_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> ReviewCaseDetailResponse:
    """Return current or historical review-case operational context."""

    try:
        case = get_review_case(session, review_case_id)
    except ReviewCaseReadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc
    response = _case_response(case)
    findings = case.validation.findings
    failed = [finding for finding in findings if finding.status == "FAIL"]
    return ReviewCaseDetailResponse(
        **response.model_dump(),
        validation_summary=ReviewValidationSummary(
            decision=FinalValidationDecision(case.validation.final_decision),
            ruleset_version=case.validation.ruleset_version,
            finding_count=len(findings),
            failed_rule_codes=[finding.rule_id for finding in failed],
            failed_severities=[finding.severity for finding in failed],
        ),
    )


def _case_response(case: ReviewCaseRead) -> ReviewCaseResponse:
    review_case = case.review_case
    workflow = case.workflow
    validation = case.validation
    invoice = case.invoice
    return ReviewCaseResponse(
        review_case_id=review_case.id,
        invoice_id=invoice.id,
        workflow_id=workflow.id,
        validation_id=validation.id,
        status=ReviewCaseStatus(review_case.status),
        resolution=(
            None
            if review_case.resolution is None
            else ReviewResolution(review_case.resolution)
        ),
        workflow_state=WorkflowState(workflow.state),
        state_version=workflow.state_version,
        validation_decision=FinalValidationDecision(validation.final_decision),
        ruleset_version=validation.ruleset_version,
        created_at=review_case.created_at,
        resolved_at=review_case.resolved_at,
        reviewer_id=review_case.reviewer_id,
        reviewer_note=review_case.reviewer_note,
        resolution_request_id=review_case.resolution_request_id,
        is_current=case.is_current,
        is_actionable=case.is_actionable,
        invoice_filename=invoice.original_filename,
        invoice_created_at=invoice.created_at,
    )
