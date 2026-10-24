"""Add persisted PDF text extraction outcomes.

Revision ID: c4e7f5a1b2d3
Revises: 9a3f1c4d7e02
Create Date: 2026-10-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision = "c4e7f5a1b2d3"
down_revision = "9a3f1c4d7e02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the one-to-one extraction table and extend invoice statuses."""

    op.drop_constraint("ck_invoices_status", "invoices", type_="check")
    op.create_check_constraint(
        "ck_invoices_status",
        "invoices",
        "status in ('received', 'text_extracted', 'needs_ocr', 'extraction_failed')",
    )
    op.create_table(
        "invoice_text_extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("extractor", sa.String(length=64), nullable=False),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("pages_with_text", sa.Integer(), nullable=False),
        sa.Column("character_count", sa.Integer(), nullable=False),
        sa.Column("non_whitespace_character_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("processing_ms", sa.Integer(), nullable=False),
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
            "status in ('text_extracted', 'needs_ocr', 'extraction_failed')",
            name="ck_invoice_text_extractions_status",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_text_extractions_invoice_id_invoices",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_text_extractions"),
        sa.UniqueConstraint(
            "invoice_id", name="uq_invoice_text_extractions_invoice_id"
        ),
    )


def downgrade() -> None:
    """Remove extraction outcomes and restore the original invoice status set."""

    op.drop_table("invoice_text_extractions")
    op.drop_constraint("ck_invoices_status", "invoices", type_="check")
    op.execute(
        "UPDATE invoices SET status = 'received' "
        "WHERE status IN ('text_extracted', 'needs_ocr', 'extraction_failed')"
    )
    op.create_check_constraint(
        "ck_invoices_status",
        "invoices",
        "status in ('received')",
    )
