"""Unit and SQLite service tests for deterministic workflow routing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import (
    InvoiceWorkflow,
    ReviewCase,
    ReviewCaseStatus,
    WorkflowEvent,
    WorkflowEventType,
    WorkflowState,
)
from app.services.business_validation import RULESET_VERSION
from app.services.invoice_validation import validate_and_persist_invoice
from app.services.invoice_workflow import (
    UnsupportedValidationDecisionError,
    WorkflowRoutingError,
    route_invoice_workflow,
    route_validation_decision,
)
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from tests.test_business_validation import make_invoice
from tests.test_invoice_validation_persistence import _seed_extraction, _seed_invoice


def _seed_route(
    engine: Engine,
    *,
    data: Any | None = None,
    decision: str | None = None,
    created_at: datetime | None = None,
) -> tuple[UUID, UUID, UUID]:
    invoice_id, extraction_id = _seed_extraction(
        engine,
        data if data is not None else make_invoice(),
        created_at=created_at,
    )
    with Session(engine) as session:
        validation, created = validate_and_persist_invoice(session, invoice_id)
        assert created is True
        validation_id = validation.id
        if decision is not None:
            validation.final_decision = decision
            session.commit()
    return invoice_id, extraction_id, validation_id


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("PASS", WorkflowState.READY),
        ("WARNING", WorkflowState.READY),
        ("REVIEW_REQUIRED", WorkflowState.NEEDS_REVIEW),
        ("BLOCKED", WorkflowState.BLOCKED),
    ],
)
def test_routing_policy_maps_supported_decisions(
    decision: str, expected: WorkflowState
) -> None:
    assert route_validation_decision(decision) is expected


def test_routing_policy_rejects_unsupported_decision() -> None:
    with pytest.raises(UnsupportedValidationDecisionError):
        route_validation_decision("PENDING")


@pytest.mark.parametrize(
    ("data", "decision", "expected_state", "review_expected"),
    [
        (None, "PASS", WorkflowState.READY.value, False),
        (
            make_invoice(summary_tax=True, total_amount="104.00", line_total="90.00"),
            "WARNING",
            WorkflowState.READY.value,
            False,
        ),
        (
            make_invoice(total_amount="105.00"),
            "REVIEW_REQUIRED",
            WorkflowState.NEEDS_REVIEW.value,
            True,
        ),
        (None, "BLOCKED", WorkflowState.BLOCKED.value, False),
    ],
)
def test_routing_persists_workflow_event_and_optional_review_case(
    engine: Engine,
    data: Any | None,
    decision: str,
    expected_state: str,
    review_expected: bool,
) -> None:
    invoice_id, _extraction_id, validation_id = _seed_route(
        engine, data=data, decision=decision
    )

    with Session(engine) as session:
        result = route_invoice_workflow(session, invoice_id)
        assert result.created is True
        assert result.validation_id == validation_id
        assert result.state == expected_state
        assert result.state_version == 1
        assert (result.review_case_id is not None) is review_expected

    with Session(engine) as session:
        workflow = session.get(InvoiceWorkflow, result.workflow_id)
        assert workflow is not None
        assert workflow.validation_id == validation_id
        assert workflow.state == expected_state
        events = list(workflow.events)
        assert len(events) == 1
        assert events[0].event_type == WorkflowEventType.ROUTED.value
        assert events[0].sequence_number == 1
        assert events[0].from_state is None
        assert events[0].to_state == expected_state
        assert (workflow.review_case is not None) is review_expected
        if workflow.review_case is not None:
            assert workflow.review_case.status == ReviewCaseStatus.OPEN.value
            assert workflow.review_case.resolution is None
            assert workflow.review_case.reviewer_id is None
            assert workflow.review_case.resolved_at is None


def test_repeated_routing_reuses_workflow_without_resetting_state(
    engine: Engine,
) -> None:
    invoice_id, _extraction_id, _validation_id = _seed_route(engine)

    with Session(engine) as session:
        first = route_invoice_workflow(session, invoice_id)
        workflow = session.get(InvoiceWorkflow, first.workflow_id)
        assert workflow is not None
        workflow.state = WorkflowState.EXCEPTION_ACCEPTED.value
        workflow.state_version = 7
        session.commit()

    with Session(engine) as session:
        second = route_invoice_workflow(session, invoice_id)
        assert second.workflow_id == first.workflow_id
        assert second.created is False
        assert second.state == WorkflowState.EXCEPTION_ACCEPTED.value
        assert second.state_version == 7

        assert (
            session.execute(
                select(func.count()).select_from(WorkflowEvent)
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(select(func.count()).select_from(ReviewCase)).scalar_one()
            == 0
        )


def test_unknown_invoice_creates_no_records(engine: Engine) -> None:
    with Session(engine) as session:
        with pytest.raises(WorkflowRoutingError) as exc_info:
            route_invoice_workflow(session, uuid4())
    assert exc_info.value.error_code == "invoice_not_found"


def test_missing_successful_extraction_creates_no_records(engine: Engine) -> None:
    invoice_id = _seed_invoice(engine)

    with Session(engine) as session:
        with pytest.raises(WorkflowRoutingError) as exc_info:
            route_invoice_workflow(session, invoice_id)
    assert exc_info.value.error_code == "structured_extraction_required"


def test_current_extraction_without_validation_is_rejected(engine: Engine) -> None:
    invoice_id, _extraction_id = _seed_extraction(engine, make_invoice())

    with Session(engine) as session:
        with pytest.raises(WorkflowRoutingError) as exc_info:
            route_invoice_workflow(session, invoice_id)
    assert exc_info.value.error_code == "current_validation_required"


def test_newer_successful_extraction_invalidates_old_validation(engine: Engine) -> None:
    invoice_id, _old_extraction_id, old_validation_id = _seed_route(
        engine, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    with Session(engine) as session:
        old_workflow = route_invoice_workflow(session, invoice_id)

    _invoice_id, new_extraction_id = _seed_extraction(
        engine,
        make_invoice(),
        invoice_id=invoice_id,
        prompt_version="invoice-extraction-v2",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    with Session(engine) as session:
        with pytest.raises(WorkflowRoutingError) as exc_info:
            route_invoice_workflow(session, invoice_id)
    assert exc_info.value.error_code == "current_validation_required"

    with Session(engine) as session:
        workflows = session.execute(select(InvoiceWorkflow)).scalars().all()
        assert [workflow.id for workflow in workflows] == [old_workflow.workflow_id]
        assert (
            session.execute(
                select(InvoiceValidation.id).where(
                    InvoiceValidation.id == old_validation_id
                )
            ).scalar_one()
            == old_validation_id
        )
        session.add(
            InvoiceValidation(
                invoice_id=invoice_id,
                structured_extraction_id=new_extraction_id,
                ruleset_version=RULESET_VERSION,
                final_decision="PASS",
                pass_count=0,
                fail_count=0,
                not_evaluable_count=0,
            )
        )
        session.commit()

    with Session(engine) as session:
        current = session.execute(
            select(InvoiceValidation).where(
                InvoiceValidation.structured_extraction_id == new_extraction_id
            )
        ).scalar_one()
        routed = route_invoice_workflow(session, invoice_id)
        assert routed.validation_id == current.id
        assert routed.workflow_id != old_workflow.workflow_id


def test_newer_failed_extraction_does_not_invalidate_current_success(
    engine: Engine,
) -> None:
    invoice_id, _old_extraction_id, validation_id = _seed_route(
        engine, created_at=datetime(2026, 1, 1, tzinfo=UTC)
    )
    _seed_extraction(
        engine,
        None,
        invoice_id=invoice_id,
        status="failed",
        prompt_version="invoice-extraction-v2",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )

    with Session(engine) as session:
        result = route_invoice_workflow(session, invoice_id)
    assert result.validation_id == validation_id
    assert result.state == WorkflowState.READY.value


def test_inactive_ruleset_validation_is_not_accepted(engine: Engine) -> None:
    invoice_id, extraction_id = _seed_extraction(engine, make_invoice())
    with Session(engine) as session:
        session.add(
            InvoiceValidation(
                invoice_id=invoice_id,
                structured_extraction_id=extraction_id,
                ruleset_version="invoice-validation-old",
                final_decision="PASS",
                pass_count=0,
                fail_count=0,
                not_evaluable_count=0,
            )
        )
        session.commit()

    with Session(engine) as session:
        with pytest.raises(WorkflowRoutingError) as exc_info:
            route_invoice_workflow(session, invoice_id)
    assert exc_info.value.error_code == "current_validation_required"
    assert RULESET_VERSION != "invoice-validation-old"


def test_event_failure_rolls_back_workflow(engine: Engine, monkeypatch) -> None:
    invoice_id, _extraction_id, _validation_id = _seed_route(engine)
    original_add = Session.add

    def fail_event_add(self: Session, instance: object, _warn: bool = True) -> None:
        if isinstance(instance, WorkflowEvent):
            raise SQLAlchemyError("controlled event failure")
        original_add(self, instance, _warn=_warn)

    monkeypatch.setattr(Session, "add", fail_event_add)

    with Session(engine) as session:
        with pytest.raises(WorkflowRoutingError) as exc_info:
            route_invoice_workflow(session, invoice_id)
    assert exc_info.value.error_code == "workflow_persistence_failed"

    with Session(engine) as session:
        assert (
            session.execute(
                select(func.count()).select_from(InvoiceWorkflow)
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                select(func.count()).select_from(WorkflowEvent)
            ).scalar_one()
            == 0
        )


def test_review_case_failure_rolls_back_workflow_and_event(
    engine: Engine, monkeypatch
) -> None:
    invoice_id, _extraction_id, _validation_id = _seed_route(
        engine, data=make_invoice(total_amount="105.00"), decision="REVIEW_REQUIRED"
    )
    original_add = Session.add

    def fail_review_add(self: Session, instance: object, _warn: bool = True) -> None:
        if isinstance(instance, ReviewCase):
            raise SQLAlchemyError("controlled review case failure")
        original_add(self, instance, _warn=_warn)

    monkeypatch.setattr(Session, "add", fail_review_add)

    with Session(engine) as session:
        with pytest.raises(WorkflowRoutingError) as exc_info:
            route_invoice_workflow(session, invoice_id)
    assert exc_info.value.error_code == "workflow_persistence_failed"

    with Session(engine) as session:
        assert (
            session.execute(
                select(func.count()).select_from(InvoiceWorkflow)
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                select(func.count()).select_from(WorkflowEvent)
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(select(func.count()).select_from(ReviewCase)).scalar_one()
            == 0
        )


def test_routing_does_not_mutate_validation_or_extraction(engine: Engine) -> None:
    invoice_id, extraction_id, validation_id = _seed_route(engine)
    with Session(engine) as session:
        extraction = session.get(InvoiceStructuredExtraction, extraction_id)
        validation = session.get(InvoiceValidation, validation_id)
        assert extraction is not None and validation is not None
        before_data = extraction.structured_data.copy()
        before_updated = extraction.updated_at
        before_decision = validation.final_decision

    with Session(engine) as session:
        route_invoice_workflow(session, invoice_id)

    with Session(engine) as session:
        extraction = session.get(InvoiceStructuredExtraction, extraction_id)
        validation = session.get(InvoiceValidation, validation_id)
        assert extraction is not None and validation is not None
        assert extraction.structured_data == before_data
        assert extraction.updated_at == before_updated
        assert validation.final_decision == before_decision
