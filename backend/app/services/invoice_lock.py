"""Shared invoice-row locking primitives for coordinated write transactions."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.invoice import Invoice


def lock_invoice_for_update(session: Session, invoice_id: UUID) -> Invoice | None:
    """Load an invoice while acquiring its PostgreSQL row-level write lock.

    The caller owns the surrounding transaction and must commit or roll it
    back. SQLite ignores ``FOR UPDATE`` but remains compatible with the same
    service and unit-test code path.
    """

    return session.execute(
        select(Invoice).where(Invoice.id == invoice_id).with_for_update()
    ).scalar_one_or_none()
