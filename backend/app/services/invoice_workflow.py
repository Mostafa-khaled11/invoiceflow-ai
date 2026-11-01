"""Deterministic initial workflow routing for current invoice validation."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import (
    InvoiceWorkflow,
    ReviewCase,
    ReviewCaseStatus,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowState,
)
from app.services.business_validation import FinalValidationDecision
from app.services.invoice_lock import lock_invoice_for_update
from app.services.invoice_validation import (
    InvoiceValidationError,
    find_validation_for_extraction,
    resolve_canonical_structured_extraction,
)


class WorkflowRoutingError(RuntimeError):
    """Typed service error for deterministic routing preconditions/persistence."""

    def __init__(self, error_code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


class UnsupportedValidationDecisionError(ValueError):
    """Raised when routing receives a decision outside the approved contract."""


@dataclass(frozen=True)
class WorkflowReviewCaseResult:
    """Compact review-case summary exposed to API adapters."""

    review_case_id: UUID
    status: str
    resolution: str | None


@dataclass(frozen=True)
class WorkflowRoutingResult:
    """Internal result returned by routing and current-workflow reads."""

    workflow_id: UUID
    validation_id: UUID
    state: str
    state_version: int
    validation_decision: str
    ruleset_version: str
    review_case: WorkflowReviewCaseResult | None
    created_at: datetime
    updated_at: datetime
    created: bool

    @property
    def review_case_id(self) -> UUID | None:
        """Compatibility accessor for callers that only need the identifier."""

        return self.review_case.review_case_id if self.review_case else None


def route_validation_decision(decision: str) -> WorkflowState:
    """Map one supported validation decision to its initial workflow state."""

    try:
        normalized = FinalValidationDecision(decision)
    except ValueError as exc:
        raise UnsupportedValidationDecisionError(
            f"Unsupported validation decision: {decision}"
        ) from exc

    if normalized in (
        FinalValidationDecision.PASS,
        FinalValidationDecision.WARNING,
    ):
        return WorkflowState.READY
    if normalized is FinalValidationDecision.REVIEW_REQUIRED:
        return WorkflowState.NEEDS_REVIEW
    if normalized is FinalValidationDecision.BLOCKED:
        return WorkflowState.BLOCKED
    raise UnsupportedValidationDecisionError(
        f"Unsupported validation decision: {decision}"
    )


def route_invoice_workflow(session: Session, invoice_id: UUID) -> WorkflowRoutingResult:
    """Create or retrieve the initial workflow for the invoice's current validation.

    The invoice lock is acquired before current extraction/validation lookup and
    remains held through workflow, event, and optional review-case persistence.
    """

    locked_invoice = lock_invoice_for_update(session, invoice_id)
    if locked_invoice is None:
        raise WorkflowRoutingError("invoice_not_found", "Invoice not found", 404)

    validation: InvoiceValidation | None = None
    try:
        try:
            extraction = resolve_canonical_structured_extraction(session, invoice_id)
        except InvoiceValidationError as exc:
            raise WorkflowRoutingError(
                exc.error_code,
                exc.message,
                exc.status_code,
            ) from exc
        validation = find_validation_for_extraction(session, extraction.id)
        if validation is None:
            raise WorkflowRoutingError(
                "current_validation_required",
                "The current structured extraction has not been validated yet.",
                409,
            )

        existing = session.execute(
            select(InvoiceWorkflow).where(
                InvoiceWorkflow.validation_id == validation.id
            )
        ).scalar_one_or_none()
        if existing is not None:
            review_case = _review_case(session, existing.id)
            result = _routing_result(existing, validation, review_case, False)
            session.commit()
            return result

        state = route_validation_decision(validation.final_decision)
        workflow = InvoiceWorkflow(
            validation_id=validation.id,
            state=state.value,
            state_version=1,
        )
        session.add(workflow)
        session.flush()

        session.add(
            WorkflowEvent(
                workflow_id=workflow.id,
                sequence_number=1,
                event_type=WorkflowEventType.ROUTED.value,
                from_state=None,
                to_state=state.value,
                actor_type="system",
                reason="Initial deterministic validation routing",
                meta={"validation_decision": validation.final_decision},
            )
        )

        review_case_model: ReviewCase | None = None
        if validation.final_decision == FinalValidationDecision.REVIEW_REQUIRED.value:
            review_case_model = ReviewCase(
                workflow_id=workflow.id,
                status=ReviewCaseStatus.OPEN.value,
            )
            session.add(review_case_model)

        session.commit()
        return _routing_result(
            workflow,
            validation,
            _review_case_from_model(review_case_model),
            True,
        )
    except (WorkflowRoutingError, UnsupportedValidationDecisionError):
        session.rollback()
        raise
    except IntegrityError as exc:
        session.rollback()
        if not _is_workflow_identity_race(exc):
            raise WorkflowRoutingError(
                "workflow_persistence_failed",
                "The workflow could not be stored.",
                500,
            ) from exc
        if validation is None:
            raise WorkflowRoutingError(
                "workflow_persistence_failed",
                "The workflow could not be stored.",
                500,
            ) from exc
        winner = session.execute(
            select(InvoiceWorkflow).where(
                InvoiceWorkflow.validation_id == validation.id
            )
        ).scalar_one_or_none()
        if winner is None:
            raise WorkflowRoutingError(
                "workflow_persistence_failed",
                "The workflow could not be stored.",
                500,
            ) from exc
        review_case = _review_case(session, winner.id)
        result = _routing_result(winner, validation, review_case, False)
        session.commit()
        return result
    except SQLAlchemyError as exc:
        session.rollback()
        raise WorkflowRoutingError(
            "workflow_persistence_failed",
            "The workflow could not be stored.",
            500,
        ) from exc


def resolve_current_invoice_workflow(
    session: Session, invoice_id: UUID
) -> WorkflowRoutingResult:
    """Read the workflow for the invoice's current validation context."""

    try:
        try:
            extraction = resolve_canonical_structured_extraction(session, invoice_id)
        except InvoiceValidationError as exc:
            raise WorkflowRoutingError(
                exc.error_code,
                exc.message,
                exc.status_code,
            ) from exc
        validation = find_validation_for_extraction(session, extraction.id)
        if validation is None:
            raise WorkflowRoutingError(
                "current_validation_required",
                "The current structured extraction has not been validated yet.",
                409,
            )
        workflow = session.execute(
            select(InvoiceWorkflow).where(
                InvoiceWorkflow.validation_id == validation.id
            )
        ).scalar_one_or_none()
        if workflow is None:
            raise WorkflowRoutingError(
                "workflow_not_found",
                "The current validation has not been routed yet.",
                404,
            )
        return _routing_result(
            workflow,
            validation,
            _review_case(session, workflow.id),
            False,
        )
    except WorkflowRoutingError:
        session.rollback()
        raise
    except SQLAlchemyError as exc:
        session.rollback()
        raise WorkflowRoutingError(
            "workflow_read_failed",
            "The current workflow could not be read.",
            500,
        ) from exc


def _review_case(
    session: Session, workflow_id: UUID
) -> WorkflowReviewCaseResult | None:
    review_case = session.execute(
        select(ReviewCase).where(ReviewCase.workflow_id == workflow_id)
    ).scalar_one_or_none()
    return _review_case_from_model(review_case)


def _review_case_from_model(
    review_case: ReviewCase | None,
) -> WorkflowReviewCaseResult | None:
    if review_case is None:
        return None
    return WorkflowReviewCaseResult(
        review_case_id=review_case.id,
        status=review_case.status,
        resolution=review_case.resolution,
    )


def _routing_result(
    workflow: InvoiceWorkflow,
    validation: InvoiceValidation,
    review_case: WorkflowReviewCaseResult | None,
    created: bool,
) -> WorkflowRoutingResult:
    validation_id = validation.id
    return WorkflowRoutingResult(
        workflow_id=workflow.id,
        validation_id=validation_id,
        state=workflow.state,
        state_version=workflow.state_version,
        validation_decision=validation.final_decision,
        ruleset_version=validation.ruleset_version,
        review_case=review_case,
        created_at=workflow.created_at,
        updated_at=workflow.updated_at,
        created=created,
    )


def _is_workflow_identity_race(exc: IntegrityError) -> bool:
    """Recognize only the workflow identity conflict as a retryable race."""

    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name == "uq_invoice_workflows_validation_id":
        return True
    return "uq_invoice_workflows_validation_id" in str(exc)
