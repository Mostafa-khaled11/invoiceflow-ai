"""Offline regression tests for sanitized security-audit persistence."""

from uuid import uuid4

import pytest
from app.models.audit import SecurityAuditEvent
from app.services.security_audit import record_security_audit_event
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_writer_allowlists_metadata_and_persists_context(engine) -> None:
    request_id = uuid4()
    correlation_id = uuid4()
    with Session(engine) as session:
        record_security_audit_event(
            session,
            event_type="auth.login_failed",
            actor_type="system",
            actor_id="anonymous",
            target_type="auth",
            target_id="login",
            metadata={
                "failure_category": "state_mismatch",
                "reason": "token=secret-value",
                "role": "reviewer",
                "access_token": "must-not-persist",
                "raw_payload": "must-not-persist",
            },
            request_id=request_id,
            correlation_id=correlation_id,
        )
        session.commit()
        event = session.execute(select(SecurityAuditEvent)).scalar_one()

    assert event.request_id == request_id
    assert event.correlation_id == correlation_id
    assert event.meta == {
        "failure_category": "state_mismatch",
        "role": "reviewer",
    }
    assert "must-not-persist" not in str(event.meta)


def test_writer_does_not_commit_or_leave_rolled_back_events(engine) -> None:
    with Session(engine) as session:
        record_security_audit_event(
            session,
            event_type="session.revoked",
            actor_type="system",
            actor_id="authentication",
        )
        session.rollback()

    with Session(engine) as session:
        assert session.execute(select(SecurityAuditEvent)).first() is None


def test_writer_drops_personal_information_from_legacy_reason_field(engine) -> None:
    with Session(engine) as session:
        record_security_audit_event(
            session,
            event_type="role.revoked",
            actor_type="system",
            actor_id="identity-persistence",
            metadata={"reason": "email=alice@example.test; phone=555-0100"},
        )
        session.commit()
        event = session.execute(select(SecurityAuditEvent)).scalar_one()

    assert event.meta == {}
    assert "alice@example.test" not in str(event.meta)
    assert "555-0100" not in str(event.meta)


def test_writer_rejects_unapproved_event_types(engine) -> None:
    with Session(engine) as session:
        with pytest.raises(ValueError, match="Unsupported security audit"):
            record_security_audit_event(
                session,
                event_type="arbitrary.event",
                actor_type="system",
                actor_id="test",
            )
