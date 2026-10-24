"""Application service coordinating invoice intake persistence.

Chosen consistency strategy for the filesystem + PostgreSQL pair, which
cannot share a single transaction:

1. the upload is validated in memory first (Task 3.1) and rejected before
   anything touches the database or the disk;
2. the SHA-256 duplicate lookup runs next, so duplicates never stage files
   or open write transactions;
3. upload bytes are copied into a private staging directory (same volume as
   the final location so the swap is an atomic rename);
4. inside one database transaction the invoice row is flushed and the
   staged file is renamed to its final internal key;
5. the transaction commits last, only after the file sits in its final
   location;
6. any failure before commit rolls the transaction back and removes both
   the staged and the (possibly moved) final file, leaving no orphan copy.

Residual risk: a process crash between rename and commit can leave a file
without its row. This window is accepted for the MVP scope instead of
introducing two-phase commit machinery; a future maintenance sweep can
delete storage keys that no longer match a row.
"""

from typing import BinaryIO
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.invoice import Invoice, InvoiceStatus
from app.services.invoice_files import ValidatedUpload
from app.services.invoice_storage import InvoiceStorage, InvoiceStorageError


class InvoiceDuplicateError(Exception):
    """Raised when an upload matches an already-persisted invoice."""

    def __init__(self, existing_invoice_id: UUID | None = None) -> None:
        super().__init__("Invoice already exists for the submitted content.")
        self.existing_invoice_id = existing_invoice_id


def _find_existing_by_sha256(session: Session, sha256: str) -> Invoice | None:
    """Return the persisted invoice that owns a content digest, if any."""

    return session.execute(
        select(Invoice).where(Invoice.sha256 == sha256)
    ).scalar_one_or_none()


def receive_invoice(
    session: Session,
    storage: InvoiceStorage,
    validated: ValidatedUpload,
    source: BinaryIO,
) -> Invoice:
    """Persist a validated upload as one invoice record plus one stored file.

    `source` is the seekable spool that still holds the validated upload
    bytes; it is staged and atomically swapped into place within the same
    transaction window as the row insert. Raises `InvoiceDuplicateError`
    when the content already exists, whether it is detected up front or
    appears as a uniqueness race while inserting.
    """

    existing = _find_existing_by_sha256(session, validated.sha256)
    if existing is not None:
        raise InvoiceDuplicateError(existing.id)

    invoice_id = uuid4()
    storage_key = f"{invoice_id}.pdf"
    staged = storage.stage_upload(source)
    try:
        invoice = Invoice(
            id=invoice_id,
            original_filename=validated.filename,
            storage_key=storage_key,
            content_type=validated.content_type,
            size_bytes=validated.size_bytes,
            sha256=validated.sha256,
            status=InvoiceStatus.RECEIVED.value,
        )
        session.add(invoice)
        session.flush()
        storage.commit_staged(staged, storage_key)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        storage.abandon(staged, storage_key)
        winner = _find_existing_by_sha256(session, validated.sha256)
        if winner is not None:
            raise InvoiceDuplicateError(winner.id) from exc
        raise
    except (InvoiceStorageError, SQLAlchemyError):
        session.rollback()
        storage.abandon(staged, storage_key)
        raise

    return invoice
