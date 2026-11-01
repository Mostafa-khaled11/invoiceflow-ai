"""Add persistent business validation runs and findings.

Revision ID: b6d2f8a0e4c7
Revises: f1a2b3c4d5e6
Create Date: 2026-10-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b6d2f8a0e4c7"
down_revision = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create validation runs and findings with idempotency + audit indexes."""

    op.create_table(
        "invoice_validations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("structured_extraction_id", sa.Uuid(), nullable=False),
        sa.Column("ruleset_version", sa.String(length=64), nullable=False),
        sa.Column("final_decision", sa.String(length=32), nullable=False),
        sa.Column("pass_count", sa.Integer(), nullable=False),
        sa.Column("fail_count", sa.Integer(), nullable=False),
        sa.Column("not_evaluable_count", sa.Integer(), nullable=False),
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
            "final_decision in ('PASS', 'WARNING', 'REVIEW_REQUIRED', 'BLOCKED')",
            name="ck_invoice_validations_final_decision",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_invoice_validations_invoice_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["structured_extraction_id"],
            ["invoice_structured_extractions.id"],
            name="fk_invoice_validations_structured_extraction_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_validations"),
        sa.UniqueConstraint(
            "structured_extraction_id",
            "ruleset_version",
            name="uq_invoice_validations_extraction_ruleset",
        ),
    )
    op.create_index(
        "ix_invoice_validations_invoice_id",
        "invoice_validations",
        ["invoice_id"],
    )
    op.create_table(
        "invoice_validation_findings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("validation_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=32), nullable=False),
        sa.Column("rule_name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("actual_value", sa.String(length=64), nullable=True),
        sa.Column("expected_value", sa.String(length=64), nullable=True),
        sa.Column("difference", sa.String(length=64), nullable=True),
        sa.Column("affected_fields", postgresql.JSONB(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status in ('PASS', 'FAIL', 'NOT_EVALUABLE')",
            name="ck_invoice_validation_findings_status",
        ),
        sa.CheckConstraint(
            "severity in ('INFO', 'WARNING', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name="ck_invoice_validation_findings_severity",
        ),
        sa.ForeignKeyConstraint(
            ["validation_id"],
            ["invoice_validations.id"],
            name="fk_invoice_validation_findings_validation_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_validation_findings"),
        sa.UniqueConstraint(
            "validation_id",
            "rule_id",
            name="uq_invoice_validation_findings_rule",
        ),
    )
    op.create_index(
        "ix_invoice_validation_findings_validation_id",
        "invoice_validation_findings",
        ["validation_id"],
    )


def downgrade() -> None:
    """Remove validation persistence (findings first, then runs)."""

    op.drop_index(
        "ix_invoice_validation_findings_validation_id",
        table_name="invoice_validation_findings",
    )
    op.drop_table("invoice_validation_findings")
    op.drop_index("ix_invoice_validations_invoice_id", table_name="invoice_validations")
    op.drop_table("invoice_validations")
