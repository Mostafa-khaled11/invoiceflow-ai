"""Allow explicit human-review resolution audit events.

Revision ID: d8f9a0b1c2d3
Revises: c7e8f9a0b1c2
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision = "d8f9a0b1c2d3"
down_revision = "c7e8f9a0b1c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Expand the event type check without changing existing rows."""

    op.drop_constraint(
        "ck_workflow_events_event_type",
        "workflow_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workflow_events_event_type",
        "workflow_events",
        "event_type in ('ROUTED', 'EXCEPTION_ACCEPTED', 'REJECTED')",
    )


def downgrade() -> None:
    """Restore the routing-only event constraint after resolution events are removed."""

    op.drop_constraint(
        "ck_workflow_events_event_type",
        "workflow_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_workflow_events_event_type",
        "workflow_events",
        "event_type in ('ROUTED')",
    )
