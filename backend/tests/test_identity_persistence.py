"""PostgreSQL-backed persistence tests for PR-01B.1 identity records."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.models.identity import (
    ApplicationRole,
    OIDCIdentity,
    Role,
    UserAccount,
    UserAccountStatus,
    UserRoleAssignment,
    UserSession,
)
from app.models.invoice import Invoice, InvoiceStatus
from app.services.identity_persistence import (
    assign_role,
    create_oidc_identity,
    create_session,
    create_user_account,
    find_session_by_hash,
    hash_session_token,
    is_session_active,
    revoke_role,
    revoke_session,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def postgres_engine(engine):
    """Use only the configured disposable PostgreSQL test database."""

    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("TEST_DATABASE_URL is not configured")
    return engine


def _active_user(session: Session) -> UserAccount:
    return create_user_account(
        session,
        status=UserAccountStatus.ACTIVE,
        email="reviewer@example.test",
    )


def _role(session: Session, role: ApplicationRole) -> Role:
    record = Role(name=role.value)
    session.add(record)
    session.flush()
    return record


def test_identity_creation_and_email_change_preserve_local_owner(
    postgres_engine,
) -> None:
    with Session(postgres_engine) as session:
        user = create_user_account(
            session,
            email="old@example.test",
            display_name="Initial User",
        )
        identity = create_oidc_identity(
            session,
            user_id=user.id,
            issuer="https://issuer.example.test",
            subject="subject-001",
            last_seen_email="old@example.test",
            email_verified=True,
        )
        user_id = user.id
        session.commit()

        user.email = "new@example.test"
        session.commit()
        persisted = session.get(OIDCIdentity, identity.id)

    assert persisted is not None
    assert persisted.user_id == user_id
    assert persisted.issuer == "https://issuer.example.test"
    assert persisted.subject == "subject-001"


def test_duplicate_issuer_subject_is_rejected(postgres_engine) -> None:
    with Session(postgres_engine) as session:
        first = _active_user(session)
        create_oidc_identity(
            session,
            user_id=first.id,
            issuer="https://issuer.example.test",
            subject="same-subject",
        )
        first_id = first.id
        session.commit()

        second = _active_user(session)
        with pytest.raises(IntegrityError):
            create_oidc_identity(
                session,
                user_id=second.id,
                issuer="https://issuer.example.test",
                subject="same-subject",
            )
        session.rollback()

        identities = session.execute(select(OIDCIdentity)).scalars().all()

    assert len(identities) == 1
    assert identities[0].user_id == first_id


def test_identity_foreign_key_rejects_unknown_user(postgres_engine) -> None:
    with Session(postgres_engine) as session:
        session.add(
            OIDCIdentity(
                user_id=uuid4(),
                issuer="https://issuer.example.test",
                subject="orphan",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_account_lifecycle_states_are_explicit(postgres_engine) -> None:
    with Session(postgres_engine) as session:
        account = create_user_account(session)
        assert account.status == UserAccountStatus.PENDING.value
        account.status = UserAccountStatus.ACTIVE.value
        session.commit()
        account.status = UserAccountStatus.DISABLED.value
        session.commit()
        persisted = session.get(UserAccount, account.id)

    assert persisted is not None
    assert persisted.status == UserAccountStatus.DISABLED.value


def test_role_assignment_is_idempotent_and_regrantable_after_revocation(
    postgres_engine,
) -> None:
    with Session(postgres_engine) as session:
        user = _active_user(session)
        _role(session, ApplicationRole.REVIEWER)
        first = assign_role(
            session,
            user_id=user.id,
            role=ApplicationRole.REVIEWER,
        )
        same = assign_role(
            session,
            user_id=user.id,
            role=ApplicationRole.REVIEWER,
        )
        assert same.id == first.id
        revoked = revoke_role(
            session,
            user_id=user.id,
            role=ApplicationRole.REVIEWER,
            reason="role rotation",
        )
        assert revoked is not None
        replacement = assign_role(
            session,
            user_id=user.id,
            role=ApplicationRole.REVIEWER,
        )
        session.commit()

        assignments = (
            session.execute(
                select(UserRoleAssignment).order_by(UserRoleAssignment.granted_at)
            )
            .scalars()
            .all()
        )

    assert len(assignments) == 2
    assert assignments[0].revoked_at is not None
    assert assignments[0].revocation_reason == "role rotation"
    assert assignments[1].id == replacement.id
    assert assignments[1].revoked_at is None


def test_session_hash_expiry_and_revocation_fields_are_persisted(
    postgres_engine,
) -> None:
    now = datetime.now(UTC)
    raw_token = "opaque-session-secret"
    token_hash = hash_session_token(raw_token)
    with Session(postgres_engine) as session:
        user = _active_user(session)
        session_record = create_session(
            session,
            user_id=user.id,
            token_hash=token_hash,
            created_at=now,
            last_activity_at=now,
            idle_expires_at=now + timedelta(minutes=15),
            absolute_expires_at=now + timedelta(hours=8),
        )
        session.commit()
        persisted = find_session_by_hash(session, token_hash)

        assert persisted is not None
        assert raw_token not in persisted.token_hash
        assert is_session_active(persisted, now=now + timedelta(minutes=1))
        assert not is_session_active(
            persisted,
            now=now + timedelta(minutes=16),
        )
        revoked = revoke_session(
            session,
            session_id=session_record.id,
            reason="test logout",
            revoked_at=now + timedelta(minutes=2),
        )
        assert revoked is not None
        revoked_at = revoked.revoked_at
        revocation_reason = revoked.revocation_reason
        inactive_after_revoke = not is_session_active(
            revoked,
            now=now + timedelta(minutes=3),
        )
        session.commit()

    assert revoked is not None
    assert revoked_at is not None
    assert revocation_reason == "test logout"
    assert inactive_after_revoke


def test_duplicate_session_hash_is_rejected_and_rollback_preserves_first(
    postgres_engine,
) -> None:
    now = datetime.now(UTC)
    token_hash = hash_session_token("duplicate-session-secret")
    with Session(postgres_engine) as session:
        user = _active_user(session)
        create_session(
            session,
            user_id=user.id,
            token_hash=token_hash,
            created_at=now,
            idle_expires_at=now + timedelta(minutes=15),
            absolute_expires_at=now + timedelta(hours=8),
        )
        session.commit()

        with pytest.raises(IntegrityError):
            create_session(
                session,
                user_id=user.id,
                token_hash=token_hash,
                created_at=now,
                idle_expires_at=now + timedelta(minutes=15),
                absolute_expires_at=now + timedelta(hours=8),
            )
        session.rollback()
        persisted = session.execute(select(UserSession)).scalars().all()

    assert len(persisted) == 1


def test_session_foreign_key_rejects_unknown_user(postgres_engine) -> None:
    now = datetime.now(UTC)
    with Session(postgres_engine) as session:
        session.add(
            UserSession(
                user_id=uuid4(),
                token_hash=hash_session_token("orphan-session-secret"),
                created_at=now,
                last_activity_at=now,
                idle_expires_at=now + timedelta(minutes=15),
                absolute_expires_at=now + timedelta(hours=8),
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_identity_tables_do_not_modify_existing_invoice_records(
    postgres_engine,
) -> None:
    with Session(postgres_engine) as session:
        invoice = Invoice(
            original_filename="identity-fixture.pdf",
            storage_key="a" * 64,
            content_type="application/pdf",
            size_bytes=10,
            sha256="b" * 64,
            status=InvoiceStatus.RECEIVED.value,
        )
        session.add(invoice)
        user = _active_user(session)
        create_oidc_identity(
            session,
            user_id=user.id,
            issuer="https://issuer.example.test",
            subject="preservation-subject",
        )
        session.commit()
        persisted_invoice = session.get(Invoice, invoice.id)

    assert persisted_invoice is not None
    assert persisted_invoice.original_filename == "identity-fixture.pdf"
