"""Read-only operational queries for human-review cases."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.orm import (
    Session,
    joinedload,
    selectinload,
)

from app.models.invoice import Invoice
from app.models.invoice_structured_extraction import (
    InvoiceStructuredExtraction,
    StructuredExtractionStatus,
)
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import (
    InvoiceWorkflow,
    ReviewCase,
    ReviewCaseStatus,
    ReviewResolution,
    WorkflowState,
)
from app.services.business_validation import RULESET_VERSION


class ReviewCaseReadError(LookupError):
    """Controlled error for read-only review-case resources."""

    def __init__(self, error_code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ReviewCaseRead:
    """Review case and its small operational context."""

    review_case: ReviewCase
    workflow: InvoiceWorkflow
    validation: InvoiceValidation
    invoice: Invoice
    is_current: bool
    is_actionable: bool


@dataclass(frozen=True)
class ReviewCasePage:
    """Stable, offset-based review queue page."""

    items: list[ReviewCaseRead]
    limit: int
    offset: int
    total: int


def list_review_cases(
    session: Session,
    *,
    status: ReviewCaseStatus | None = None,
    resolution: ReviewResolution | None = None,
    workflow_state: WorkflowState | None = None,
    invoice_id: UUID | None = None,
    include_historical: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> ReviewCasePage:
    """List review cases with current/actionable flags derived in the query."""

    current_expression = _current_expression()
    filters = _filters(
        current_expression=current_expression,
        status=status,
        resolution=resolution,
        workflow_state=workflow_state,
        invoice_id=invoice_id,
        include_historical=include_historical,
    )
    total = session.execute(
        select(func.count(ReviewCase.id))
        .select_from(ReviewCase)
        .join(InvoiceWorkflow, ReviewCase.workflow_id == InvoiceWorkflow.id)
        .join(InvoiceValidation, InvoiceWorkflow.validation_id == InvoiceValidation.id)
        .join(Invoice, InvoiceValidation.invoice_id == Invoice.id)
        .join(
            InvoiceStructuredExtraction,
            InvoiceValidation.structured_extraction_id
            == InvoiceStructuredExtraction.id,
        )
        .where(*filters)
    ).scalar_one()

    query = (
        select(ReviewCase)
        .join(InvoiceWorkflow, ReviewCase.workflow_id == InvoiceWorkflow.id)
        .join(InvoiceValidation, InvoiceWorkflow.validation_id == InvoiceValidation.id)
        .join(Invoice, InvoiceValidation.invoice_id == Invoice.id)
        .join(
            InvoiceStructuredExtraction,
            InvoiceValidation.structured_extraction_id
            == InvoiceStructuredExtraction.id,
        )
        .options(
            joinedload(ReviewCase.workflow)
            .joinedload(InvoiceWorkflow.validation)
            .joinedload(InvoiceValidation.invoice)
            .selectinload(Invoice.structured_extractions),
            joinedload(ReviewCase.workflow)
            .joinedload(InvoiceWorkflow.validation)
            .joinedload(InvoiceValidation.structured_extraction),
        )
        .where(*filters)
        .order_by(ReviewCase.created_at.desc(), ReviewCase.id.desc())
        .offset(offset)
        .limit(limit)
    )
    cases = session.execute(query).scalars().unique().all()
    items = [_to_read(case) for case in cases]
    return ReviewCasePage(items=items, limit=limit, offset=offset, total=total)


def get_review_case(session: Session, review_case_id: UUID) -> ReviewCaseRead:
    """Read one review case, including historical cases."""

    case = session.execute(
        select(ReviewCase)
        .where(ReviewCase.id == review_case_id)
        .options(
            joinedload(ReviewCase.workflow)
            .joinedload(InvoiceWorkflow.validation)
            .options(
                joinedload(InvoiceValidation.invoice),
                joinedload(InvoiceValidation.invoice).selectinload(
                    Invoice.structured_extractions
                ),
                joinedload(InvoiceValidation.structured_extraction),
                selectinload(InvoiceValidation.findings),
            )
        )
    ).scalar_one_or_none()
    if case is None:
        raise ReviewCaseReadError("review_case_not_found", "Review case not found", 404)
    return _to_read(case)


def _current_expression():
    canonical_extraction_id = (
        select(InvoiceStructuredExtraction.id)
        .where(
            InvoiceStructuredExtraction.invoice_id == Invoice.id,
            InvoiceStructuredExtraction.status
            == StructuredExtractionStatus.SUCCESS.value,
        )
        .order_by(
            InvoiceStructuredExtraction.created_at.desc(),
            InvoiceStructuredExtraction.id.desc(),
        )
        .limit(1)
        .correlate(Invoice)
        .scalar_subquery()
    )
    return and_(
        InvoiceValidation.structured_extraction_id == canonical_extraction_id,
        InvoiceValidation.ruleset_version == RULESET_VERSION,
    )


def _filters(
    *,
    current_expression,
    status: ReviewCaseStatus | None,
    resolution: ReviewResolution | None,
    workflow_state: WorkflowState | None,
    invoice_id: UUID | None,
    include_historical: bool,
):
    filters = []
    if not include_historical:
        filters.extend(
            (
                current_expression,
                ReviewCase.status == ReviewCaseStatus.OPEN.value,
                InvoiceWorkflow.state == WorkflowState.NEEDS_REVIEW.value,
            )
        )
    if status is not None:
        filters.append(ReviewCase.status == status.value)
    if resolution is not None:
        filters.append(ReviewCase.resolution == resolution.value)
    if workflow_state is not None:
        filters.append(InvoiceWorkflow.state == workflow_state.value)
    if invoice_id is not None:
        filters.append(Invoice.id == invoice_id)
    return filters


def _to_read(case: ReviewCase) -> ReviewCaseRead:
    workflow = case.workflow
    validation = workflow.validation
    invoice = validation.invoice
    canonical_id = _canonical_id_for_loaded_context(invoice)
    is_current = (
        validation.structured_extraction_id == canonical_id
        and validation.ruleset_version == RULESET_VERSION
    )
    is_actionable = (
        is_current
        and case.status == ReviewCaseStatus.OPEN.value
        and workflow.state == WorkflowState.NEEDS_REVIEW.value
    )
    return ReviewCaseRead(
        review_case=case,
        workflow=workflow,
        validation=validation,
        invoice=invoice,
        is_current=is_current,
        is_actionable=is_actionable,
    )


def _canonical_id_for_loaded_context(invoice: Invoice) -> UUID | None:
    successful = [
        extraction
        for extraction in invoice.structured_extractions
        if extraction.status == StructuredExtractionStatus.SUCCESS.value
    ]
    if not successful:
        return None
    canonical = max(
        successful,
        key=lambda extraction: (extraction.created_at, extraction.id),
    )
    return canonical.id
