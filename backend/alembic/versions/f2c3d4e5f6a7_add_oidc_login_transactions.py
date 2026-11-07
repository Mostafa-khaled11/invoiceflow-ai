"""Add short-lived OIDC authorization transaction persistence.

Revision ID: f2c3d4e5f6a7
Revises: e1b2c3d4e5f6
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "f2c3d4e5f6a7"
down_revision = "e1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the single-use OIDC state transaction table."""

    op.create_table(
        "oidc_login_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="ck_oidc_login_transactions_expiry_after_creation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_oidc_login_transactions"),
        sa.UniqueConstraint(
            "state_hash",
            name="uq_oidc_login_transactions_state_hash",
        ),
    )
    op.create_index(
        "ix_oidc_login_transactions_expires_at",
        "oidc_login_transactions",
        ["expires_at"],
    )


def downgrade() -> None:
    """Drop only the disposable authorization transaction table."""

    op.drop_index(
        "ix_oidc_login_transactions_expires_at",
        table_name="oidc_login_transactions",
    )
    op.drop_table("oidc_login_transactions")
