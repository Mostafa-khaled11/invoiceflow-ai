"""Persistence models for provider-neutral identity and server-side sessions."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserAccountStatus(StrEnum):
    """Lifecycle states for local accounts mapped from verified identities."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class ApplicationRole(StrEnum):
    """Application roles available for future authorization enforcement."""

    OPERATOR = "Operator"
    REVIEWER = "Reviewer"
    APPROVER = "Approver"
    ADMIN = "Admin"


_ACCOUNT_STATUSES = "'PENDING', 'ACTIVE', 'DISABLED'"


class UserAccount(Base):
    """Stable local principal record; it contains no authentication secret."""

    __tablename__ = "user_accounts"
    __table_args__ = (
        CheckConstraint(
            f"status in ({_ACCOUNT_STATUSES})",
            name="ck_user_accounts_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=UserAccountStatus.PENDING.value,
        server_default=UserAccountStatus.PENDING.value,
    )
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    identities: Mapped[list["OIDCIdentity"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    role_assignments: Mapped[list["UserRoleAssignment"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class OIDCIdentity(Base):
    """Verified external identity keyed by exact issuer and subject."""

    __tablename__ = "oidc_identities"
    __table_args__ = (
        UniqueConstraint(
            "issuer",
            "subject",
            name="uq_oidc_identities_issuer_subject",
        ),
        Index("ix_oidc_identities_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    issuer: Mapped[str] = mapped_column(String(2048), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    last_seen_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    email_verified: Mapped[bool] = mapped_column(
        Boolean(), nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[UserAccount] = relationship(back_populates="identities")


class Role(Base):
    """Static role catalog used by role assignments."""

    __tablename__ = "roles"

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    assignments: Mapped[list["UserRoleAssignment"]] = relationship(
        back_populates="role",
        passive_deletes=True,
    )


class UserRoleAssignment(Base):
    """A grantable and revocable role assignment for one local account."""

    __tablename__ = "user_role_assignments"
    __table_args__ = (
        Index(
            "uq_user_role_assignments_active",
            "user_id",
            "role_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
        Index("ix_user_role_assignments_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("roles.id", ondelete="RESTRICT"),
        nullable=False,
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    user: Mapped[UserAccount] = relationship(back_populates="role_assignments")
    role: Mapped[Role] = relationship(back_populates="assignments")


class UserSession(Base):
    """Opaque server-side session metadata; raw credentials are never stored."""

    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint(
            "length(token_hash) = 64",
            name="ck_user_sessions_token_hash_length",
        ),
        CheckConstraint(
            "idle_expires_at > created_at",
            name="ck_user_sessions_idle_expiry_after_creation",
        ),
        CheckConstraint(
            "absolute_expires_at > created_at",
            name="ck_user_sessions_absolute_expiry_after_creation",
        ),
        UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
        Index("ix_user_sessions_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("user_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    idle_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    absolute_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revocation_reason: Mapped[str | None] = mapped_column(Text(), nullable=True)

    user: Mapped[UserAccount] = relationship(back_populates="sessions")
