"""PostgreSQL concurrency tests for human-review resolution."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event
from uuid import UUID

import app.services.invoice_workflow as workflow_service
import pytest
from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import (
    InvoiceWorkflow,
    ReviewCase,
    ReviewResolution,
    WorkflowEvent,
    WorkflowState,
)
from app.services.business_validation import RULESET_VERSION
from app.services.invoice_lock import lock_invoice_for_update
from app.services.invoice_validation import (
    resolve_canonical_structured_extraction,
    validate_and_persist_invoice,
)
from app.services.invoice_workflow import (
    ReviewResolutionError,
    resolve_invoice_review,
    route_invoice_workflow,
)
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tests.test_business_validation import make_invoice
from tests.test_invoice_validation_persistence import _seed_extraction

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_engine(engine: Engine) -> Engine:
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL is not configured")
    return engine


def _seed_review_workflow(engine: Engine) -> UUID:
    invoice_id, _ = _seed_extraction(engine, make_invoice(total_amount="105.00"))
    with Session(engine) as session:
        validation, created = validate_and_persist_invoice(session, invoice_id)
        assert created is True
        assert validation.final_decision == "REVIEW_REQUIRED"
        route_invoice_workflow(session, invoice_id)
    return invoice_id


def _resolve(
    engine: Engine,
    invoice_id: UUID,
    decision: ReviewResolution,
    request_id: str,
    barrier: Barrier,
) -> tuple[str, str | None]:
    barrier.wait(timeout=10)
    try:
        with Session(engine) as session:
            result = resolve_invoice_review(session, invoice_id, decision, request_id)
            return "success", result.state
    except ReviewResolutionError as exc:
        return "error", exc.error_code


def test_concurrent_same_resolution_is_idempotent(
    postgres_engine: Engine,
) -> None:
    invoice_id = _seed_review_workflow(postgres_engine)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _resolve,
                postgres_engine,
                invoice_id,
                ReviewResolution.ACCEPT_EXCEPTION,
                "same-request-id",
                barrier,
            )
            for _ in range(2)
        ]
        results = [future.result(timeout=10) for future in futures]

    assert results == [("success", "EXCEPTION_ACCEPTED")] * 2
    with Session(postgres_engine) as session:
        workflow = session.execute(select(InvoiceWorkflow)).scalar_one()
        review_case = session.execute(select(ReviewCase)).scalar_one()
        assert workflow.state == WorkflowState.EXCEPTION_ACCEPTED.value
        assert workflow.state_version == 2
        assert review_case.status == "RESOLVED"
        assert review_case.resolution == ReviewResolution.ACCEPT_EXCEPTION.value
        assert (
            session.execute(
                select(func.count()).select_from(WorkflowEvent)
            ).scalar_one()
            == 2
        )


def test_concurrent_competing_resolutions_have_one_winner(
    postgres_engine: Engine,
) -> None:
    invoice_id = _seed_review_workflow(postgres_engine)
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _resolve,
                postgres_engine,
                invoice_id,
                decision,
                request_id,
                barrier,
            )
            for decision, request_id in (
                (ReviewResolution.ACCEPT_EXCEPTION, "accept-request"),
                (ReviewResolution.REJECT, "reject-request"),
            )
        ]
        results = [future.result(timeout=10) for future in futures]

    assert sum(result[0] == "success" for result in results) == 1
    assert sum(result[0] == "error" for result in results) == 1
    assert results[0][1] in {
        "EXCEPTION_ACCEPTED",
        "REJECTED",
        "review_already_resolved",
    }
    assert results[1][1] in {
        "EXCEPTION_ACCEPTED",
        "REJECTED",
        "review_already_resolved",
    }
    with Session(postgres_engine) as session:
        workflow = session.execute(select(InvoiceWorkflow)).scalar_one()
        review_case = session.execute(select(ReviewCase)).scalar_one()
        assert workflow.state in {
            WorkflowState.EXCEPTION_ACCEPTED.value,
            WorkflowState.REJECTED.value,
        }
        assert workflow.state_version == 2
        assert review_case.resolution in {
            ReviewResolution.ACCEPT_EXCEPTION.value,
            ReviewResolution.REJECT.value,
        }
        assert (
            session.execute(
                select(func.count()).select_from(WorkflowEvent)
            ).scalar_one()
            == 2
        )


def test_same_actor_key_collision_across_invoices_recovers_from_unique_race(
    postgres_engine: Engine,
) -> None:
    invoice_ids = [
        _seed_review_workflow(postgres_engine),
        _seed_review_workflow(postgres_engine),
    ]
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                _resolve,
                postgres_engine,
                invoice_id,
                ReviewResolution.REJECT,
                "cross-invoice-shared-key",
                barrier,
            )
            for invoice_id in invoice_ids
        ]
        results = [future.result(timeout=10) for future in futures]

    assert sum(result[0] == "success" for result in results) == 1
    assert (
        sum(result == ("error", "resolution_request_conflict") for result in results)
        == 1
    )
    with Session(postgres_engine) as session:
        assert (
            session.execute(
                select(func.count()).select_from(WorkflowEvent)
            ).scalar_one()
            == 3
        )


def test_stale_review_action_rechecks_currentness_after_invoice_lock(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice_id = _seed_review_workflow(postgres_engine)
    owner = Session(postgres_engine)
    assert lock_invoice_for_update(owner, invoice_id) is not None
    lock_attempted = Event()
    original_lock = workflow_service.lock_invoice_for_update

    def observed_lock(session: Session, target_invoice_id: UUID):
        lock_attempted.set()
        return original_lock(session, target_invoice_id)

    monkeypatch.setattr(workflow_service, "lock_invoice_for_update", observed_lock)
    barrier = Barrier(1)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _resolve,
            postgres_engine,
            invoice_id,
            ReviewResolution.ACCEPT_EXCEPTION,
            "stale-request",
            barrier,
        )
        try:
            assert lock_attempted.wait(timeout=10)

            old = owner.execute(
                select(InvoiceStructuredExtraction).where(
                    InvoiceStructuredExtraction.invoice_id == invoice_id
                )
            ).scalar_one()
            old_validation = owner.execute(
                select(InvoiceValidation).where(
                    InvoiceValidation.structured_extraction_id == old.id
                )
            ).scalar_one()
            old_workflow = owner.execute(
                select(InvoiceWorkflow).where(
                    InvoiceWorkflow.validation_id == old_validation.id
                )
            ).scalar_one()
            old_validation_id = old_validation.id
            old_workflow_id = old_workflow.id
            newer = InvoiceStructuredExtraction(
                invoice_id=invoice_id,
                text_extraction_id=old.text_extraction_id,
                provider="fake",
                model="fake-model",
                prompt_version="invoice-extraction-v2",
                schema_version="invoice-schema-v1",
                status="success",
                structured_data=make_invoice().model_dump(mode="json"),
                created_at=old.created_at + timedelta(seconds=1),
            )
            owner.add(newer)
            owner.flush()
            newer_validation = InvoiceValidation(
                invoice_id=invoice_id,
                structured_extraction_id=newer.id,
                ruleset_version=RULESET_VERSION,
                final_decision="PASS",
                pass_count=0,
                fail_count=0,
                not_evaluable_count=0,
            )
            owner.add(newer_validation)
            owner.flush()
            newer_extraction_id = newer.id
            newer_validation_id = newer_validation.id
            owner.commit()
        finally:
            owner.close()
        result = future.result(timeout=10)

    assert result == ("error", "workflow_not_found")
    with Session(postgres_engine) as session:
        canonical = resolve_canonical_structured_extraction(session, invoice_id)
        workflow = session.get(InvoiceWorkflow, old_workflow_id)
        review_case = session.execute(
            select(ReviewCase).where(ReviewCase.workflow_id == old_workflow_id)
        ).scalar_one()
        assert canonical.id == newer_extraction_id
        assert newer_validation_id != old_validation_id
        assert workflow is not None
        assert workflow.validation_id == old_validation_id
        assert workflow.state == WorkflowState.NEEDS_REVIEW.value
        assert workflow.state_version == 1
        assert review_case.status == "OPEN"
        assert review_case.resolution is None
        assert (
            session.execute(
                select(func.count()).where(WorkflowEvent.workflow_id == old_workflow_id)
            ).scalar_one()
            == 1
        )
