"""Focused persistence operations for identity and session records.

This module deliberately does not authenticate requests, issue browser cookies,
or enforce application permissions. Browser authentication uses the separate
authentication service and this module remains the persistence boundary.
"""

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import compare_digest
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.identity import (
    ApplicationRole,
    OIDCIdentity,
    OIDCLoginTransaction,
    Role,
    UserAccount,
    UserAccountStatus,
    UserRoleAssignment,
    UserSession,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """Normalize SQLite's naive timestamp round-trip to the application clock."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _account_status(value: UserAccountStatus | str) -> str:
    try:
        return UserAccountStatus(value).value
    except ValueError as exc:
        raise ValueError(f"Unsupported user account status: {value}") from exc


def _role_name(value: ApplicationRole | str) -> str:
    try:
        return ApplicationRole(value).value
    except ValueError as exc:
        raise ValueError(f"Unsupported application role: {value}") from exc


def normalize_issuer(issuer: str) -> str:
    """Normalize only surrounding whitespace; issuer identity remains exact."""

    normalized = issuer.strip()
    if not normalized:
        raise ValueError("OIDC issuer cannot be empty")
    return normalized


def create_user_account(
    session: Session,
    *,
    email: str | None = None,
    display_name: str | None = None,
    status: UserAccountStatus | str = UserAccountStatus.PENDING,
) -> UserAccount:
    """Create a local account without assigning a role or credential."""

    account = UserAccount(
        email=email,
        display_name=display_name,
        status=_account_status(status),
    )
    session.add(account)
    session.flush()
    return account


def create_oidc_identity(
    session: Session,
    *,
    user_id: UUID,
    issuer: str,
    subject: str,
    last_seen_email: str | None = None,
    email_verified: bool = False,
) -> OIDCIdentity:
    """Attach a verified `(issuer, subject)` identity to an existing account."""

    if not subject.strip():
        raise ValueError("OIDC subject cannot be empty")
    if session.get(UserAccount, user_id) is None:
        raise ValueError("Cannot attach an identity to an unknown user account")
    identity = OIDCIdentity(
        user_id=user_id,
        issuer=normalize_issuer(issuer),
        subject=subject,
        last_seen_email=last_seen_email,
        email_verified=email_verified,
    )
    session.add(identity)
    session.flush()
    return identity


def find_oidc_identity(
    session: Session,
    *,
    issuer: str,
    subject: str,
) -> OIDCIdentity | None:
    """Resolve a local identity by the verified external identity pair."""

    return session.execute(
        select(OIDCIdentity).where(
            OIDCIdentity.issuer == normalize_issuer(issuer),
            OIDCIdentity.subject == subject,
        )
    ).scalar_one_or_none()


def assign_role(
    session: Session,
    *,
    user_id: UUID,
    role: ApplicationRole | str,
) -> UserRoleAssignment:
    """Grant a catalog role idempotently when no active grant exists."""

    if session.get(UserAccount, user_id) is None:
        raise ValueError("Cannot assign a role to an unknown user account")
    role_name = _role_name(role)
    role_record = session.execute(
        select(Role).where(Role.name == role_name)
    ).scalar_one_or_none()
    if role_record is None:
        raise ValueError(f"Role catalog entry does not exist: {role_name}")
    active = session.execute(
        select(UserRoleAssignment).where(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.role_id == role_record.id,
            UserRoleAssignment.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if active is not None:
        return active
    assignment = UserRoleAssignment(user_id=user_id, role_id=role_record.id)
    session.add(assignment)
    session.flush()
    return assignment


def revoke_role(
    session: Session,
    *,
    user_id: UUID,
    role: ApplicationRole | str,
    reason: str | None = None,
    revoked_at: datetime | None = None,
) -> UserRoleAssignment | None:
    """Revoke the current grant while retaining its persistence history."""

    role_name = _role_name(role)
    assignment = session.execute(
        select(UserRoleAssignment)
        .join(UserRoleAssignment.role)
        .where(
            UserRoleAssignment.user_id == user_id,
            Role.name == role_name,
            UserRoleAssignment.revoked_at.is_(None),
        )
    ).scalar_one_or_none()
    if assignment is None:
        return None
    assignment.revoked_at = revoked_at or _now()
    assignment.revocation_reason = reason
    session.flush()
    return assignment


def hash_session_token(token: str) -> str:
    """Return the non-reversible verifier stored for an opaque session token."""

    if not token:
        raise ValueError("Session token cannot be empty")
    return sha256(token.encode("utf-8")).hexdigest()


def hash_opaque_value(value: str) -> str:
    """Hash a short-lived opaque protocol value without retaining the value."""

    if not value:
        raise ValueError("Opaque value cannot be empty")
    return sha256(value.encode("utf-8")).hexdigest()


def _validate_token_hash(token_hash: str) -> str:
    if len(token_hash) != 64:
        raise ValueError("Session token hash must be a SHA-256 hexadecimal digest")
    try:
        int(token_hash, 16)
    except ValueError as exc:
        raise ValueError("Session token hash must be hexadecimal") from exc
    return token_hash.lower()


def create_session(
    session: Session,
    *,
    user_id: UUID,
    token_hash: str,
    idle_expires_at: datetime,
    absolute_expires_at: datetime,
    created_at: datetime | None = None,
    last_activity_at: datetime | None = None,
) -> UserSession:
    """Persist session metadata for an active account, never a raw token."""

    account = session.get(UserAccount, user_id)
    if account is None:
        raise ValueError("Cannot create a session for an unknown user account")
    if account.status != UserAccountStatus.ACTIVE.value:
        raise ValueError("Only active user accounts may receive sessions")
    normalized_hash = _validate_token_hash(token_hash)
    created = created_at or _now()
    activity = last_activity_at or created
    if idle_expires_at <= created or absolute_expires_at <= created:
        raise ValueError("Session expiry must be after session creation")
    session_record = UserSession(
        user_id=user_id,
        token_hash=normalized_hash,
        created_at=created,
        last_activity_at=activity,
        idle_expires_at=idle_expires_at,
        absolute_expires_at=absolute_expires_at,
    )
    session.add(session_record)
    session.flush()
    return session_record


def find_session_by_hash(session: Session, token_hash: str) -> UserSession | None:
    """Find a session by its stored verifier hash."""

    normalized_hash = _validate_token_hash(token_hash)
    return session.execute(
        select(UserSession).where(UserSession.token_hash == normalized_hash)
    ).scalar_one_or_none()


def is_session_active(
    session_record: UserSession,
    *,
    now: datetime | None = None,
) -> bool:
    """Evaluate revocation and both expiry boundaries without mutating data."""

    current = now or _now()
    if session_record.revoked_at is not None:
        return False
    return current < _as_utc(session_record.idle_expires_at) and current < _as_utc(
        session_record.absolute_expires_at
    )


def revoke_session(
    session: Session,
    *,
    session_id: UUID,
    reason: str | None = None,
    revoked_at: datetime | None = None,
) -> UserSession | None:
    """Mark a session revoked while retaining its lifecycle record."""

    session_record = session.get(UserSession, session_id)
    if session_record is None:
        return None
    session_record.revoked_at = revoked_at or _now()
    session_record.revocation_reason = reason
    session.flush()
    return session_record


def touch_session(
    session: Session,
    session_record: UserSession,
    *,
    now: datetime,
    idle_timeout: timedelta,
    update_interval: timedelta,
) -> bool:
    """Advance activity and idle expiry only after the configured interval."""

    if now - _as_utc(session_record.last_activity_at) < update_interval:
        return False
    session_record.last_activity_at = now
    session_record.idle_expires_at = min(
        now + idle_timeout,
        _as_utc(session_record.absolute_expires_at),
    )
    session.flush()
    return True


def revoke_user_sessions(
    session: Session,
    *,
    user_id: UUID,
    reason: str,
    revoked_at: datetime | None = None,
) -> int:
    """Revoke every session for an account during lifecycle invalidation."""

    now = revoked_at or _now()
    records = session.execute(
        select(UserSession).where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
    ).scalars()
    count = 0
    for record in records:
        record.revoked_at = now
        record.revocation_reason = reason
        count += 1
    session.flush()
    return count


def create_oidc_login_transaction(
    session: Session,
    *,
    state: str,
    nonce: str,
    redirect_uri: str,
    expires_at: datetime,
    created_at: datetime | None = None,
) -> OIDCLoginTransaction:
    """Persist only hashes for a single-use OIDC authorization transaction."""

    created = created_at or _now()
    if expires_at <= created:
        raise ValueError("OIDC transaction expiry must be after creation")
    transaction = OIDCLoginTransaction(
        state_hash=hash_opaque_value(state),
        nonce_hash=hash_opaque_value(nonce),
        redirect_uri=redirect_uri,
        created_at=created,
        expires_at=expires_at,
    )
    session.add(transaction)
    session.flush()
    return transaction


def consume_oidc_login_transaction(
    session: Session,
    *,
    state: str,
    nonce: str,
    now: datetime | None = None,
) -> OIDCLoginTransaction | None:
    """Atomically consume a valid state/nonce transaction exactly once."""

    current = now or _now()
    transaction = session.execute(
        select(OIDCLoginTransaction)
        .where(OIDCLoginTransaction.state_hash == hash_opaque_value(state))
        .with_for_update()
    ).scalar_one_or_none()
    if (
        transaction is None
        or transaction.consumed_at is not None
        or _as_utc(transaction.expires_at) <= current
        or not compare_digest(transaction.nonce_hash, hash_opaque_value(nonce))
    ):
        return None
    transaction.consumed_at = current
    session.flush()
    return transaction
