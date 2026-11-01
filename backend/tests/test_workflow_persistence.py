"""Persistence tests for Task 5.3B2 workflow models."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID

import pytest
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
from app.services.invoice_validation import validate_and_persist_invoice
from sqlalchemy import Engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.test_business_validation import make_invoice
from tests.test_invoice_validation_persistence import _seed_extraction


def _seed_validation(engine: Engine) -> UUID:
    invoice_id, _ = _seed_extraction(engine, make_invoice())
    with Session(engine) as session:
        validation, created = validate_and_persist_invoice(session, invoice_id)
        assert created is True
        return validation.id


def _seed_workflow(engine: Engine) -> tuple[UUID, UUID]:
    validation_id = _seed_validation(engine)
    with Session(engine) as session:
        workflow = InvoiceWorkflow(
            validation_id=validation_id,
            state=WorkflowState.READY.value,
            state_version=1,
        )
        session.add(workflow)
        session.commit()
        return workflow.id, validation_id


def _assert_rejected(session: Session, object_to_add: object) -> None:
    session.add(object_to_add)
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_workflow_model_persists_with_validation_relationship(engine: Engine) -> None:
    workflow_id, validation_id = _seed_workflow(engine)

    with Session(engine) as session:
        workflow = session.get(InvoiceWorkflow, workflow_id)
        assert workflow is not None
        assert workflow.validation_id == validation_id
        assert workflow.state == WorkflowState.READY.value
        assert workflow.state_version == 1
        assert workflow.validation.id == validation_id


def test_one_workflow_per_validation_is_database_enforced(engine: Engine) -> None:
    _workflow_id, validation_id = _seed_workflow(engine)

    with Session(engine) as session:
        _assert_rejected(
            session,
            InvoiceWorkflow(
                validation_id=validation_id,
                state=WorkflowState.READY.value,
                state_version=1,
            ),
        )


def test_invalid_workflow_state_is_rejected(engine: Engine) -> None:
    validation_id = _seed_validation(engine)
    with Session(engine) as session:
        _assert_rejected(
            session,
            InvoiceWorkflow(
                validation_id=validation_id,
                state="PENDING",
                state_version=1,
            ),
        )


def test_workflow_state_version_must_be_positive(engine: Engine) -> None:
    validation_id = _seed_validation(engine)
    with Session(engine) as session:
        _assert_rejected(
            session,
            InvoiceWorkflow(
                validation_id=validation_id,
                state=WorkflowState.READY.value,
                state_version=0,
            ),
        )


def test_workflow_event_sequence_is_unique_per_workflow(engine: Engine) -> None:
    workflow_id, _validation_id = _seed_workflow(engine)

    with Session(engine) as session:
        session.add(
            WorkflowEvent(
                workflow_id=workflow_id,
                sequence_number=1,
                event_type=WorkflowEventType.ROUTED.value,
                from_state=None,
                to_state=WorkflowState.READY.value,
            )
        )
        session.commit()
        _assert_rejected(
            session,
            WorkflowEvent(
                workflow_id=workflow_id,
                sequence_number=1,
                event_type=WorkflowEventType.ROUTED.value,
                from_state=None,
                to_state=WorkflowState.READY.value,
            ),
        )


def test_initial_routed_event_allows_null_from_state(engine: Engine) -> None:
    workflow_id, _validation_id = _seed_workflow(engine)

    with Session(engine) as session:
        event = WorkflowEvent(
            workflow_id=workflow_id,
            sequence_number=1,
            event_type=WorkflowEventType.ROUTED.value,
            from_state=None,
            to_state=WorkflowState.READY.value,
        )
        session.add(event)
        session.commit()
        stored = session.get(WorkflowEvent, event.id)
        assert stored is not None
        assert stored.from_state is None
        assert stored.to_state == WorkflowState.READY.value


def test_invalid_event_state_is_rejected(engine: Engine) -> None:
    workflow_id, _validation_id = _seed_workflow(engine)

    with Session(engine) as session:
        _assert_rejected(
            session,
            WorkflowEvent(
                workflow_id=workflow_id,
                sequence_number=1,
                event_type=WorkflowEventType.ROUTED.value,
                from_state="PENDING",
                to_state=WorkflowState.READY.value,
            ),
        )


def test_one_review_case_per_workflow_is_database_enforced(engine: Engine) -> None:
    workflow_id, _validation_id = _seed_workflow(engine)

    with Session(engine) as session:
        session.add(
            ReviewCase(
                workflow_id=workflow_id,
                status=ReviewCaseStatus.OPEN.value,
            )
        )
        session.commit()
        _assert_rejected(
            session,
            ReviewCase(
                workflow_id=workflow_id,
                status=ReviewCaseStatus.OPEN.value,
            ),
        )


def test_review_status_and_resolution_constraints(engine: Engine) -> None:
    workflow_id, _validation_id = _seed_workflow(engine)

    with Session(engine) as session:
        open_case = ReviewCase(
            workflow_id=workflow_id,
            status=ReviewCaseStatus.OPEN.value,
            resolution=None,
        )
        session.add(open_case)
        session.commit()
        assert open_case.resolution is None

    second_workflow_id, _ = _seed_workflow(engine)
    with Session(engine) as session:
        resolved_case = ReviewCase(
            workflow_id=second_workflow_id,
            status=ReviewCaseStatus.RESOLVED.value,
            resolution=ReviewResolution.ACCEPT_EXCEPTION.value,
            reviewer_id="reviewer-1",
            reviewer_note="Accepted with documented exception.",
            resolution_request_id="request-1",
            resolved_at=datetime.now(UTC),
        )
        session.add(resolved_case)
        session.commit()
        assert resolved_case.resolution == ReviewResolution.ACCEPT_EXCEPTION.value

    third_workflow_id, _ = _seed_workflow(engine)
    with Session(engine) as session:
        _assert_rejected(
            session,
            ReviewCase(
                workflow_id=third_workflow_id,
                status="PENDING",
                resolution=ReviewResolution.REJECT.value,
            ),
        )

    fourth_workflow_id, _ = _seed_workflow(engine)
    with Session(engine) as session:
        _assert_rejected(
            session,
            ReviewCase(
                workflow_id=fourth_workflow_id,
                status=ReviewCaseStatus.OPEN.value,
                resolution="APPROVE",
            ),
        )


def test_workflow_event_and_review_relationships_round_trip(engine: Engine) -> None:
    workflow_id, _validation_id = _seed_workflow(engine)

    with Session(engine) as session:
        workflow = session.get(InvoiceWorkflow, workflow_id)
        assert workflow is not None
        workflow.events.append(
            WorkflowEvent(
                sequence_number=1,
                event_type=WorkflowEventType.ROUTED.value,
                from_state=None,
                to_state=WorkflowState.READY.value,
            )
        )
        workflow.review_case = ReviewCase(status=ReviewCaseStatus.OPEN.value)
        session.commit()

    with Session(engine) as session:
        workflow = session.execute(
            select(InvoiceWorkflow).where(InvoiceWorkflow.id == workflow_id)
        ).scalar_one()
        assert len(workflow.events) == 1
        assert workflow.events[0].workflow_id == workflow.id
        assert workflow.review_case is not None
        assert workflow.review_case.workflow_id == workflow.id


def test_validation_delete_is_restricted_when_workflow_audit_exists(
    engine: Engine,
) -> None:
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("audit FK behavior requires PostgreSQL")

    workflow_id, validation_id = _seed_workflow(engine)
    with Session(engine) as session:
        session.add(
            WorkflowEvent(
                workflow_id=workflow_id,
                sequence_number=1,
                event_type=WorkflowEventType.ROUTED.value,
                from_state=None,
                to_state=WorkflowState.READY.value,
            )
        )
        session.commit()

    with Session(engine) as session:
        validation = session.get(InvoiceValidation, validation_id)
        assert validation is not None
        session.delete(validation)
        with pytest.raises(IntegrityError):
            session.commit()
