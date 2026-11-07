"""Focused persistence tests for PR-01D.2 Step 1 tables."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from app.models.audit import ReviewCommandIdempotency, SecurityAuditEvent
from app.models.invoice import Invoice
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import InvoiceWorkflow, ReviewCase, WorkflowState
from sqlalchemy import Engine, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.test_workflow_persistence import _seed_validation


@pytest.fixture
def postgres_engine(engine: Engine) -> Engine:
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL is not configured")
    return engine


def _seed_dependencies(engine: Engine) -> tuple[Invoice, InvoiceWorkflow, ReviewCase]:
    validation_id = _seed_validation(engine)
    with Session(engine) as session:
        validation = session.get(InvoiceValidation, validation_id)
        assert validation is not None
        invoice = session.get(Invoice, validation.invoice_id)
        assert invoice is not None
        workflow = InvoiceWorkflow(
            validation_id=validation.id,
            state=WorkflowState.NEEDS_REVIEW.value,
        )
        session.add(workflow)
        session.flush()
        review_case = ReviewCase(
            workflow_id=workflow.id,
            status="OPEN",
        )
        session.add(review_case)
        session.flush()
        session.expunge_all()
        return invoice, workflow, review_case


def test_audit_tables_and_nullable_event_context_are_registered(engine: Engine) -> None:
    database_inspector = inspect(engine)
    assert {
        "review_command_idempotency",
        "security_audit_events",
    } <= set(database_inspector.get_table_names())
    columns = {
        column["name"] for column in database_inspector.get_columns("workflow_events")
    }
    assert {"request_id", "correlation_id"} <= columns


def test_security_audit_event_persists_sanitized_metadata(engine: Engine) -> None:
    event_id = uuid4()
    with Session(engine) as session:
        session.add(
            SecurityAuditEvent(
                id=event_id,
                event_type="session.revoked",
                actor_type="human",
                actor_id="account-1",
                target_type="session",
                target_id="session-1",
                meta={"reason": "user logout"},
            )
        )
        session.commit()
        stored = session.execute(
            select(SecurityAuditEvent).where(SecurityAuditEvent.id == event_id)
        ).scalar_one()
        assert stored.meta == {"reason": "user logout"}


@pytest.mark.integration
def test_review_command_actor_scope_unique_and_distinct_scopes_allowed(
    postgres_engine: Engine,
) -> None:
    invoice, workflow, review_case = _seed_dependencies(postgres_engine)
    base = dict(
        request_key="00000000-0000-4000-8000-000000000001",
        fingerprint_version="review-resolution-v1",
        command_fingerprint="a" * 64,
        invoice_id=invoice.id,
        workflow_id=workflow.id,
        review_case_id=review_case.id,
        status="COMPLETED",
    )
    with Session(postgres_engine) as session:
        session.add(ReviewCommandIdempotency(actor_scope_key="human:a", **base))
        session.add(ReviewCommandIdempotency(actor_scope_key="human:b", **base))
        session.commit()

        session.add(ReviewCommandIdempotency(actor_scope_key="human:a", **base))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


@pytest.mark.integration
def test_review_command_required_constraints_are_database_enforced(
    postgres_engine: Engine,
) -> None:
    invoice, workflow, review_case = _seed_dependencies(postgres_engine)
    with Session(postgres_engine) as session:
        session.add(
            ReviewCommandIdempotency(
                actor_scope_key="human:a",
                request_key="request",
                fingerprint_version="wrong-version",
                command_fingerprint="a" * 64,
                invoice_id=invoice.id,
                workflow_id=workflow.id,
                review_case_id=review_case.id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
