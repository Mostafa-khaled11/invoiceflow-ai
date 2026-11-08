"""Add durable review idempotency and security audit persistence.

Revision ID: a7b8c9d0e1f2
Revises: f2c3d4e5f6a7
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a7b8c9d0e1f2"
down_revision = "f2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.JSON:
    return sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Add only new columns and tables; preserve all existing rows."""

    op.add_column(
        "workflow_events",
        sa.Column("request_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "workflow_events",
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_workflow_events_request_id",
        "workflow_events",
        ["request_id"],
    )
    op.create_index(
        "ix_workflow_events_correlation_id",
        "workflow_events",
        ["correlation_id"],
    )

    op.create_table(
        "review_command_idempotency",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_scope_key", sa.String(length=160), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("fingerprint_version", sa.String(length=64), nullable=False),
        sa.Column("command_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("review_case_id", sa.Uuid(), nullable=False),
        sa.Column("result_event_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'PENDING'"),
        ),
        sa.Column("result_state", sa.String(length=32), nullable=True),
        sa.Column("result_resolution", sa.String(length=32), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
            "length(actor_scope_key) between 1 and 160",
            name="ck_review_command_actor_scope_key_length",
        ),
        sa.CheckConstraint(
            "length(request_key) between 1 and 128",
            name="ck_review_command_request_key_length",
        ),
        sa.CheckConstraint(
            "fingerprint_version = 'review-resolution-v1'",
            name="ck_review_command_fingerprint_version",
        ),
        sa.CheckConstraint(
            "length(command_fingerprint) = 64",
            name="ck_review_command_fingerprint_length",
        ),
        sa.CheckConstraint(
            "status in ('PENDING', 'COMPLETED')",
            name="ck_review_command_status",
        ),
        sa.ForeignKeyConstraint(
            ["invoice_id"],
            ["invoices.id"],
            name="fk_review_command_invoice_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["invoice_workflows.id"],
            name="fk_review_command_workflow_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["review_case_id"],
            ["review_cases.id"],
            name="fk_review_command_review_case_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["result_event_id"],
            ["workflow_events.id"],
            name="fk_review_command_result_event_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_command_idempotency"),
        sa.UniqueConstraint(
            "actor_scope_key",
            "request_key",
            name="uq_review_command_actor_request",
        ),
    )
    op.create_index(
        "ix_review_command_invoice_id",
        "review_command_idempotency",
        ["invoice_id"],
    )
    op.create_index(
        "ix_review_command_created_at",
        "review_command_idempotency",
        ["created_at"],
    )

    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("metadata", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type <> ''",
            name="ck_security_audit_event_type_nonempty",
        ),
        sa.CheckConstraint(
            "actor_type in ('human', 'service', 'system')",
            name="ck_security_audit_actor_type",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_security_audit_events"),
    )
    op.create_index(
        "ix_security_audit_events_created_at",
        "security_audit_events",
        ["created_at"],
    )
    op.create_index(
        "ix_security_audit_events_target",
        "security_audit_events",
        ["target_type", "target_id"],
    )


def downgrade() -> None:
    """Remove only objects introduced by this revision.

    This downgrade drops Step 1 command/security-audit rows and the new
    correlation columns. It is suitable only for an isolated disposable
    database or a planned maintenance rollback; production rollback should
    prefer reverting application code while retaining this additive schema.
    """

    op.drop_index("ix_security_audit_events_target", table_name="security_audit_events")
    op.drop_index(
        "ix_security_audit_events_created_at", table_name="security_audit_events"
    )
    op.drop_table("security_audit_events")
    op.drop_index(
        "ix_review_command_created_at",
        table_name="review_command_idempotency",
    )
    op.drop_index(
        "ix_review_command_invoice_id",
        table_name="review_command_idempotency",
    )
    op.drop_table("review_command_idempotency")
    op.drop_index("ix_workflow_events_correlation_id", table_name="workflow_events")
    op.drop_index("ix_workflow_events_request_id", table_name="workflow_events")
    op.drop_column("workflow_events", "correlation_id")
    op.drop_column("workflow_events", "request_id")
