"""Invoice intake endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_invoice_storage
from app.models.invoice import Invoice, InvoiceStatus
from app.schemas.invoices import InvoiceDuplicateDetail, InvoiceResponse
from app.services.invoice_files import (
    UploadValidationError,
    validate_invoice_pdf,
)
from app.services.invoice_intake import (
    InvoiceDuplicateError,
    receive_invoice,
)
from app.services.invoice_storage import InvoiceStorage, InvoiceStorageError

router = APIRouter(prefix="/invoices", tags=["invoices"])

_CONFLICT_MESSAGE = "Invoice already exists for the submitted content."


@router.post(
    "/upload",
    summary="Upload an invoice PDF for intake validation and storage",
    response_model=InvoiceResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "model": InvoiceDuplicateDetail,
            "description": "The same PDF content is already stored.",
        },
    },
)
async def upload_invoice(
    file: UploadFile,
    session: Annotated[Session, Depends(get_db)],
    storage: Annotated[InvoiceStorage, Depends(get_invoice_storage)],
) -> InvoiceResponse:
    """Validate one invoice PDF upload, reject duplicates, and persist it.

    Validated bytes are staged privately and atomically swapped into place
    within the same transaction window as the invoice row insert; a failed
    persistence attempt removes every staged artifact before re-raising.
    The spool stays open for the post-validation staging pass and is closed
    exactly once when the handling finishes, on success or failure.
    """

    try:
        try:
            validated = await validate_invoice_pdf(file)
        except UploadValidationError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        try:
            invoice = receive_invoice(session, storage, validated, source=file.file)
        except InvoiceDuplicateError as exc:
            detail: dict[str, str] = {"message": _CONFLICT_MESSAGE}
            if exc.existing_invoice_id is not None:
                detail["existing_invoice_id"] = str(exc.existing_invoice_id)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=detail
            ) from exc
        except (InvoiceStorageError, SQLAlchemyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="The invoice could not be stored.",
            ) from exc

        return InvoiceResponse(
            invoice_id=invoice.id,
            filename=invoice.original_filename,
            content_type=invoice.content_type,
            size_bytes=invoice.size_bytes,
            sha256=invoice.sha256,
            status=InvoiceStatus(invoice.status),
            created_at=invoice.created_at,
        )
    finally:
        await file.close()


@router.get(
    "/{invoice_id}",
    summary="Fetch stored invoice metadata",
    response_model=InvoiceResponse,
)
def get_invoice(
    invoice_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> InvoiceResponse:
    """Return previously stored invoice metadata without storage paths."""

    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    return InvoiceResponse(
        invoice_id=invoice.id,
        filename=invoice.original_filename,
        content_type=invoice.content_type,
        size_bytes=invoice.size_bytes,
        sha256=invoice.sha256,
        status=InvoiceStatus(invoice.status),
        created_at=invoice.created_at,
    )
