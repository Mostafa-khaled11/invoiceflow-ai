"""Add LLM structured invoice extraction persistence."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f1a2b3c4d5e6"
down_revision = "c4e7f5a1b2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create structured extraction records and extend invoice statuses."""

    op.drop_constraint("ck_invoices_status", "invoices", type_="check")
    op.create_check_constraint(
        "ck_invoices_status",
        "invoices",
        "status in ("
        "'received', 'text_extracted', 'needs_ocr', 'extraction_failed', "
        "'structured_extracted'"
        ")",
    )
    op.create_table(
        "invoice_structured_extractions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("text_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("structured_data", postgresql.JSONB(), nullable=True),
        sa.Column("raw_model_output", postgresql.JSONB(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
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
            "status in ('success', 'failed')",
            name="ck_invoice_structured_extractions_status",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_structured_extractions_invoice_id_invoices",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["text_extraction_id"],
            ["invoice_text_extractions.id"],
            name="fk_invoice_structured_extractions_text_extraction_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_structured_extractions"),
        sa.UniqueConstraint(
            "text_extraction_id",
            "provider",
            "model",
            "prompt_version",
            "schema_version",
            name="uq_invoice_structured_extractions_identity",
        ),
    )


def downgrade() -> None:
    """Remove structured extraction persistence and restore prior statuses."""

    op.drop_table("invoice_structured_extractions")
    op.drop_constraint("ck_invoices_status", "invoices", type_="check")
    op.execute(
        "UPDATE invoices SET status = 'text_extracted' "
        "WHERE status = 'structured_extracted'"
    )
    op.create_check_constraint(
        "ck_invoices_status",
        "invoices",
        "status in ('received', 'text_extracted', 'needs_ocr', 'extraction_failed')",
    )
