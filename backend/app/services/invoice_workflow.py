"""Deterministic initial workflow routing for current invoice validation."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

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
class WorkflowRoutingResult:
    """Internal result returned by one routing attempt."""

    workflow_id: UUID
    validation_id: UUID
    state: str
    state_version: int
    review_case_id: UUID | None
    created: bool


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
            review_case_id = _review_case_id(session, existing.id)
            result = _routing_result(existing, validation.id, review_case_id, False)
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

        review_case: ReviewCase | None = None
        if validation.final_decision == FinalValidationDecision.REVIEW_REQUIRED.value:
            review_case = ReviewCase(
                workflow_id=workflow.id,
                status=ReviewCaseStatus.OPEN.value,
            )
            session.add(review_case)

        session.commit()
        return _routing_result(
            workflow,
            validation.id,
            review_case.id if review_case is not None else None,
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
        review_case_id = _review_case_id(session, winner.id)
        result = _routing_result(winner, validation.id, review_case_id, False)
        session.commit()
        return result
    except SQLAlchemyError as exc:
        session.rollback()
        raise WorkflowRoutingError(
            "workflow_persistence_failed",
            "The workflow could not be stored.",
            500,
        ) from exc


def _review_case_id(session: Session, workflow_id: UUID) -> UUID | None:
    return session.execute(
        select(ReviewCase.id).where(ReviewCase.workflow_id == workflow_id)
    ).scalar_one_or_none()


def _routing_result(
    workflow: InvoiceWorkflow,
    validation_id: UUID,
    review_case_id: UUID | None,
    created: bool,
) -> WorkflowRoutingResult:
    return WorkflowRoutingResult(
        workflow_id=workflow.id,
        validation_id=validation_id,
        state=workflow.state,
        state_version=workflow.state_version,
        review_case_id=review_case_id,
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
