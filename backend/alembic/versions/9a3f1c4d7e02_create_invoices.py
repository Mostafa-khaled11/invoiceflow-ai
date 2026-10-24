"""Create the invoices table.

Revision ID: 9a3f1c4d7e02
Revises:
Create Date: 2026-10-24

The table holds the Postgres-side uniqueness guarantees required by the
intake flow: one row per content SHA-256 and one row per storage key. The
status column is a constrained string kept in sync with the application
`InvoiceStatus` enum (see `app/models/invoice.py`).
"""

import sqlalchemy as sa
from alembic import op

revision = "9a3f1c4d7e02"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Create the invoices table with its constraints."""

    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
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
        sa.CheckConstraint("status in ('received')", name="ck_invoices_status"),
        sa.PrimaryKeyConstraint("id", name="pk_invoices"),
        sa.UniqueConstraint("sha256", name="uq_invoices_sha256"),
        sa.UniqueConstraint("storage_key", name="uq_invoices_storage_key"),
    )


def downgrade() -> None:
    """Drop the invoices table created by this revision."""

    op.drop_table("invoices")
