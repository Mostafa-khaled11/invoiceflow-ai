"""Add local identity, role, and server-side session persistence.

Revision ID: e1b2c3d4e5f6
Revises: d8f9a0b1c2d3
Create Date: 2026-11-07
"""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision = "e1b2c3d4e5f6"
down_revision = "d8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_ROWS = [
    {
        "id": UUID("11111111-1111-4111-8111-111111111111"),
        "name": "Operator",
        "description": "Invoice intake and operational processing role.",
    },
    {
        "id": UUID("22222222-2222-4222-8222-222222222222"),
        "name": "Reviewer",
        "description": "Human review and exception-resolution role.",
    },
    {
        "id": UUID("33333333-3333-4333-8333-333333333333"),
        "name": "Approver",
        "description": "Reserved for the PR-02 approval workflow.",
    },
    {
        "id": UUID("44444444-4444-4444-8444-444444444444"),
        "name": "Admin",
        "description": "Account and role administration; not automatic approval.",
    },
]


def upgrade() -> None:
    """Create identity tables without changing existing operational data."""

    op.create_table(
        "user_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="PENDING"
        ),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status in ('PENDING', 'ACTIVE', 'DISABLED')",
            name="ck_user_accounts_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_accounts"),
    )

    op.create_table(
        "oidc_identities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("issuer", sa.String(length=2048), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("last_seen_email", sa.String(length=320), nullable=True),
        sa.Column(
            "email_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            name="fk_oidc_identities_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oidc_identities"),
        sa.UniqueConstraint(
            "issuer",
            "subject",
            name="uq_oidc_identities_issuer_subject",
        ),
    )
    op.create_index(
        "ix_oidc_identities_user_id",
        "oidc_identities",
        ["user_id"],
    )

    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )
    op.bulk_insert(
        sa.table(
            "roles",
            sa.column("id", sa.Uuid()),
            sa.column("name", sa.String(length=32)),
            sa.column("description", sa.Text()),
        ),
        _ROLE_ROWS,
    )

    op.create_table(
        "user_role_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column(
            "granted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_user_role_assignments_role_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            name="fk_user_role_assignments_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_role_assignments"),
    )
    op.create_index(
        "uq_user_role_assignments_active",
        "user_role_assignments",
        ["user_id", "role_id"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "ix_user_role_assignments_user_id",
        "user_role_assignments",
        ["user_id"],
    )

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("idle_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "length(token_hash) = 64",
            name="ck_user_sessions_token_hash_length",
        ),
        sa.CheckConstraint(
            "idle_expires_at > created_at",
            name="ck_user_sessions_idle_expiry_after_creation",
        ),
        sa.CheckConstraint(
            "absolute_expires_at > created_at",
            name="ck_user_sessions_absolute_expiry_after_creation",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_accounts.id"],
            name="fk_user_sessions_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])


def downgrade() -> None:
    """Remove only the identity tables created by this revision.

    Downgrade is safe only when identity/session data is intentionally
    disposable. It must not be run against a live deployment that relies on
    these records for authentication.
    """

    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index(
        "ix_user_role_assignments_user_id",
        table_name="user_role_assignments",
    )
    op.drop_index(
        "uq_user_role_assignments_active",
        table_name="user_role_assignments",
    )
    op.drop_table("user_role_assignments")
    op.drop_table("roles")
    op.drop_index("ix_oidc_identities_user_id", table_name="oidc_identities")
    op.drop_table("oidc_identities")
    op.drop_table("user_accounts")
