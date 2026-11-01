"""Real-PostgreSQL concurrency tests for deterministic workflow routing."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from uuid import UUID

import app.services.invoice_workflow as workflow_service
import pytest
from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import (
    InvoiceWorkflow,
    ReviewCase,
    WorkflowEvent,
    WorkflowState,
)
from app.services.business_validation import RULESET_VERSION
from app.services.invoice_lock import lock_invoice_for_update
from app.services.invoice_validation import validate_and_persist_invoice
from app.services.invoice_workflow import route_invoice_workflow
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


def _seed_current_validation(engine: Engine, *, review_required: bool = False) -> UUID:
    data = make_invoice(total_amount="105.00") if review_required else make_invoice()
    invoice_id, _ = _seed_extraction(engine, data)
    with Session(engine) as session:
        validation, created = validate_and_persist_invoice(session, invoice_id)
        assert created is True
        return invoice_id


def _route(engine: Engine, invoice_id: UUID) -> UUID:
    with Session(engine) as session:
        return route_invoice_workflow(session, invoice_id).workflow_id


def test_concurrent_pass_routing_creates_one_workflow(
    postgres_engine: Engine,
) -> None:
    invoice_id = _seed_current_validation(postgres_engine)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_route, postgres_engine, invoice_id) for _ in range(2)
        ]
        workflow_ids = [future.result(timeout=10) for future in futures]

    assert workflow_ids[0] == workflow_ids[1]
    with Session(postgres_engine) as session:
        assert (
            session.execute(
                select(func.count()).select_from(InvoiceWorkflow)
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                select(func.count()).where(InvoiceWorkflow.id == workflow_ids[0])
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(select(func.count()).select_from(ReviewCase)).scalar_one()
            == 0
        )


def test_concurrent_review_routing_creates_one_workflow_and_case(
    postgres_engine: Engine,
) -> None:
    invoice_id = _seed_current_validation(postgres_engine, review_required=True)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_route, postgres_engine, invoice_id) for _ in range(2)
        ]
        workflow_ids = [future.result(timeout=10) for future in futures]

    assert workflow_ids[0] == workflow_ids[1]
    with Session(postgres_engine) as session:
        workflow = session.get(InvoiceWorkflow, workflow_ids[0])
        assert workflow is not None
        assert workflow.state == WorkflowState.NEEDS_REVIEW.value
        assert (
            session.execute(
                select(func.count()).select_from(WorkflowEvent)
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(select(func.count()).select_from(ReviewCase)).scalar_one()
            == 1
        )


def test_routing_waits_for_lock_then_resolves_newer_current_validation(
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoice_id = _seed_current_validation(postgres_engine)
    with Session(postgres_engine) as session:
        old_extraction = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == invoice_id
            )
        ).scalar_one()
        old_created_at = old_extraction.created_at
        text_extraction_id = old_extraction.text_extraction_id

    owner = Session(postgres_engine)
    assert lock_invoice_for_update(owner, invoice_id) is not None
    lock_attempted = Event()
    original_lock = workflow_service.lock_invoice_for_update

    def observed_lock(session: Session, target_invoice_id: UUID):
        lock_attempted.set()
        return original_lock(session, target_invoice_id)

    monkeypatch.setattr(workflow_service, "lock_invoice_for_update", observed_lock)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_route, postgres_engine, invoice_id)
        assert lock_attempted.wait(timeout=2)

        newer_extraction = InvoiceStructuredExtraction(
            invoice_id=invoice_id,
            text_extraction_id=text_extraction_id,
            provider="fake",
            model="fake-model",
            prompt_version="invoice-extraction-v2",
            schema_version="invoice-schema-v1",
            status="success",
            structured_data=make_invoice().model_dump(mode="json"),
            created_at=old_created_at + timedelta(seconds=1),
        )
        owner.add(newer_extraction)
        owner.flush()
        newer_validation = InvoiceValidation(
            invoice_id=invoice_id,
            structured_extraction_id=newer_extraction.id,
            ruleset_version=RULESET_VERSION,
            final_decision="PASS",
            pass_count=0,
            fail_count=0,
            not_evaluable_count=0,
        )
        owner.add(newer_validation)
        owner.flush()
        newer_validation_id = newer_validation.id
        owner.commit()
        owner.close()

        workflow_id = future.result(timeout=10)

    with Session(postgres_engine) as session:
        workflow = session.get(InvoiceWorkflow, workflow_id)
        assert workflow is not None
        assert workflow.validation_id == newer_validation_id
