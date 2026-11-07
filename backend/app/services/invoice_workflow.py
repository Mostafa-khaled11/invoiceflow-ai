"""Deterministic initial workflow routing for current invoice validation."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import (
    InvoiceWorkflow,
    ReviewCase,
    ReviewCaseStatus,
    ReviewResolution,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowState,
)
from app.services.authorization import AuthorizationContext, Permission
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
    resolved_at: datetime | None
    resolution_request_id: str | None


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
    event_id: UUID | None = None
    event_created: bool = False

    @property
    def review_case_id(self) -> UUID | None:
        """Compatibility accessor for callers that only need the identifier."""

        return self.review_case.review_case_id if self.review_case else None


class ReviewResolutionError(RuntimeError):
    """Controlled domain error for human-review resolution commands."""

    def __init__(self, error_code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


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
        locked_extraction = session.execute(
            select(InvoiceStructuredExtraction)
            .where(InvoiceStructuredExtraction.id == extraction.id)
            .with_for_update()
        ).scalar_one()
        validation = find_validation_for_extraction(session, locked_extraction.id)
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

        routing_event = WorkflowEvent(
            workflow_id=workflow.id,
            sequence_number=1,
            event_type=WorkflowEventType.ROUTED.value,
            from_state=None,
            to_state=state.value,
            actor_type="system",
            reason="Initial deterministic validation routing",
            meta={"validation_decision": validation.final_decision},
        )
        session.add(routing_event)

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
            routing_event.id,
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
        locked_extraction = session.execute(
            select(InvoiceStructuredExtraction)
            .where(InvoiceStructuredExtraction.id == extraction.id)
            .with_for_update()
        ).scalar_one()
        validation = find_validation_for_extraction(session, locked_extraction.id)
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


def resolve_invoice_review(
    session: Session,
    invoice_id: UUID,
    decision: ReviewResolution,
    resolution_request_id: str,
    note: str | None = None,
    principal: AuthorizationContext | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
) -> WorkflowRoutingResult:
    """Resolve the current open review case under the invoice coordination lock."""

    if principal is not None:
        if not principal.can(Permission.REVIEW_RESOLVE):
            raise ReviewResolutionError(
                "authorization_required",
                "The review actor lacks review resolution permission.",
                403,
            )
        actor_type = principal.principal_type
        actor_id = (
            str(principal.account_id)
            if principal.account_id is not None
            else principal.service_name
        )

    if actor_type not in {"human", "service", "system"}:
        raise ReviewResolutionError(
            "invalid_actor",
            "The review actor type is invalid.",
            500,
        )
    if actor_type in {"human", "service"} and not actor_id:
        raise ReviewResolutionError(
            "invalid_actor",
            "A human or service review actor must have an identifier.",
            500,
        )
    if actor_type == "system" and actor_id is not None:
        raise ReviewResolutionError(
            "invalid_actor",
            "System review events cannot have an actor identifier.",
            500,
        )

    locked_invoice = lock_invoice_for_update(session, invoice_id)
    if locked_invoice is None:
        raise ReviewResolutionError("invoice_not_found", "Invoice not found", 404)

    try:
        try:
            extraction = resolve_canonical_structured_extraction(session, invoice_id)
        except InvoiceValidationError as exc:
            raise ReviewResolutionError(
                exc.error_code,
                exc.message,
                exc.status_code,
            ) from exc

        locked_extraction = session.execute(
            select(InvoiceStructuredExtraction)
            .where(InvoiceStructuredExtraction.id == extraction.id)
            .with_for_update()
        ).scalar_one()
        validation = find_validation_for_extraction(session, locked_extraction.id)
        if validation is None:
            raise ReviewResolutionError(
                "current_validation_required",
                "The current structured extraction has not been validated yet.",
                409,
            )
        validation = session.execute(
            select(InvoiceValidation)
            .where(InvoiceValidation.id == validation.id)
            .with_for_update()
        ).scalar_one()

        workflow = session.execute(
            select(InvoiceWorkflow)
            .where(InvoiceWorkflow.validation_id == validation.id)
            .with_for_update()
        ).scalar_one_or_none()
        if workflow is None:
            raise ReviewResolutionError(
                "workflow_not_found",
                "The current validation has not been routed yet.",
                404,
            )

        review_case = session.execute(
            select(ReviewCase)
            .where(ReviewCase.workflow_id == workflow.id)
            .with_for_update()
        ).scalar_one_or_none()
        if review_case is None:
            raise ReviewResolutionError(
                "review_case_required",
                "The current workflow has no review case.",
                409,
            )

        if review_case.status == ReviewCaseStatus.RESOLVED.value:
            if review_case.resolution_request_id != resolution_request_id:
                raise ReviewResolutionError(
                    "review_already_resolved",
                    "The review case has already been resolved.",
                    409,
                )
            if review_case.resolution != decision.value:
                raise ReviewResolutionError(
                    "resolution_request_conflict",
                    "The resolution request ID was reused with a different decision.",
                    409,
                )
            if review_case.reviewer_id != actor_id:
                raise ReviewResolutionError(
                    "resolution_request_conflict",
                    "The resolution request ID was reused by a different actor.",
                    409,
                )
            if review_case.reviewer_note != note:
                raise ReviewResolutionError(
                    "resolution_request_conflict",
                    "The resolution request ID was reused with a different note.",
                    409,
                )
            return _routing_result(
                workflow,
                validation,
                _review_case_from_model(review_case),
                False,
            )

        if workflow.state != WorkflowState.NEEDS_REVIEW.value:
            raise ReviewResolutionError(
                "workflow_not_reviewable",
                "Only NEEDS_REVIEW workflows can be resolved by human review.",
                409,
            )
        if review_case.status != ReviewCaseStatus.OPEN.value:
            raise ReviewResolutionError(
                "review_case_not_open",
                "The review case is not open.",
                409,
            )

        next_state = (
            WorkflowState.EXCEPTION_ACCEPTED
            if decision is ReviewResolution.ACCEPT_EXCEPTION
            else WorkflowState.REJECTED
        )
        event_type = (
            WorkflowEventType.EXCEPTION_ACCEPTED
            if decision is ReviewResolution.ACCEPT_EXCEPTION
            else WorkflowEventType.REJECTED
        )
        next_sequence = (
            session.execute(
                select(func.max(WorkflowEvent.sequence_number)).where(
                    WorkflowEvent.workflow_id == workflow.id
                )
            ).scalar_one()
            or 0
        ) + 1
        now = datetime.now(UTC)
        previous_state = workflow.state
        workflow.state = next_state.value
        workflow.state_version += 1
        review_case.status = ReviewCaseStatus.RESOLVED.value
        review_case.resolution = decision.value
        review_case.resolved_at = now
        review_case.reviewer_id = actor_id
        review_case.reviewer_note = note
        review_case.resolution_request_id = resolution_request_id
        session.add(
            resolution_event := WorkflowEvent(
                workflow_id=workflow.id,
                sequence_number=next_sequence,
                event_type=event_type.value,
                from_state=previous_state,
                to_state=next_state.value,
                actor_type=actor_type,
                actor_id=actor_id,
                reason="Human review resolution",
                meta={"resolution_request_id": resolution_request_id},
            )
        )
        session.commit()
        return _routing_result(
            workflow,
            validation,
            _review_case_from_model(review_case),
            False,
            resolution_event.id,
            True,
        )
    except ReviewResolutionError:
        session.rollback()
        raise
    except SQLAlchemyError as exc:
        session.rollback()
        raise ReviewResolutionError(
            "review_persistence_failed",
            "The review resolution could not be stored.",
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
        resolved_at=review_case.resolved_at,
        resolution_request_id=review_case.resolution_request_id,
    )


def _routing_result(
    workflow: InvoiceWorkflow,
    validation: InvoiceValidation,
    review_case: WorkflowReviewCaseResult | None,
    created: bool,
    event_id: UUID | None = None,
    event_created: bool = False,
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
        event_id=event_id,
        event_created=event_created,
    )


def _is_workflow_identity_race(exc: IntegrityError) -> bool:
    """Recognize only the workflow identity conflict as a retryable race."""

    original = getattr(exc, "orig", None)
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None)
    if constraint_name == "uq_invoice_workflows_validation_id":
        return True
    return "uq_invoice_workflows_validation_id" in str(exc)
