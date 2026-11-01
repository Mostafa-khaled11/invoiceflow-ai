"""Add workflow runs, audit events, and review cases.

Revision ID: c7e8f9a0b1c2
Revises: b6d2f8a0e4c7
Create Date: 2026-11-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c7e8f9a0b1c2"
down_revision = "b6d2f8a0e4c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WORKFLOW_STATES = (
    "'READY', 'NEEDS_REVIEW', 'BLOCKED', 'EXCEPTION_ACCEPTED', 'REJECTED'"
)


def upgrade() -> None:
    """Create workflow persistence tables and audit-retaining foreign keys."""

    op.create_table(
        "invoice_workflows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("validation_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "state_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
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
        sa.CheckConstraint(
            f"state in ({_WORKFLOW_STATES})",
            name="ck_invoice_workflows_state",
        ),
        sa.CheckConstraint(
            "state_version >= 1",
            name="ck_invoice_workflows_state_version",
        ),
        sa.ForeignKeyConstraint(
            ["validation_id"],
            ["invoice_validations.id"],
            name="fk_invoice_workflows_validation_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invoice_workflows"),
        sa.UniqueConstraint(
            "validation_id",
            name="uq_invoice_workflows_validation_id",
        ),
    )

    op.create_table(
        "workflow_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_state", sa.String(length=32), nullable=True),
        sa.Column("to_state", sa.String(length=32), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "event_type in ('ROUTED')",
            name="ck_workflow_events_event_type",
        ),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name="ck_workflow_events_sequence_number",
        ),
        sa.CheckConstraint(
            f"from_state is null or from_state in ({_WORKFLOW_STATES})",
            name="ck_workflow_events_from_state",
        ),
        sa.CheckConstraint(
            f"to_state in ({_WORKFLOW_STATES})",
            name="ck_workflow_events_to_state",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["invoice_workflows.id"],
            name="fk_workflow_events_workflow_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workflow_events"),
        sa.UniqueConstraint(
            "workflow_id",
            "sequence_number",
            name="uq_workflow_events_workflow_sequence",
        ),
    )
    op.create_index(
        "ix_workflow_events_workflow_id",
        "workflow_events",
        ["workflow_id"],
    )

    op.create_table(
        "review_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resolution", sa.String(length=32), nullable=True),
        sa.Column("reviewer_id", sa.String(length=128), nullable=True),
        sa.Column("reviewer_note", sa.Text(), nullable=True),
        sa.Column("resolution_request_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status in ('OPEN', 'RESOLVED')",
            name="ck_review_cases_status",
        ),
        sa.CheckConstraint(
            "resolution is null or resolution in ('ACCEPT_EXCEPTION', 'REJECT')",
            name="ck_review_cases_resolution",
        ),
        sa.ForeignKeyConstraint(
            ["workflow_id"],
            ["invoice_workflows.id"],
            name="fk_review_cases_workflow_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_cases"),
        sa.UniqueConstraint("workflow_id", name="uq_review_cases_workflow_id"),
    )


def downgrade() -> None:
    """Drop workflow children first, then the workflow table."""

    op.drop_table("review_cases")
    op.drop_index("ix_workflow_events_workflow_id", table_name="workflow_events")
    op.drop_table("workflow_events")
    op.drop_table("invoice_workflows")
