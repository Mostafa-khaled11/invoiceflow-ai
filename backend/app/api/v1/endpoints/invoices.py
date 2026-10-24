"""Invoice intake endpoints."""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile

from app.schemas.invoices import InvoiceUploadResponse
from app.services.invoice_files import UploadValidationError, validate_invoice_pdf

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post("/upload", summary="Upload an invoice PDF for intake validation")
async def upload_invoice(file: UploadFile) -> InvoiceUploadResponse:
    """Validate one invoice PDF upload and return its intake metadata.

    The upload is validated by content and never written to permanent
    storage; the response metadata supports later persistence and duplicate
    detection tasks.
    """

    try:
        validated = await validate_invoice_pdf(file)
    except UploadValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    finally:
        await file.close()

    return InvoiceUploadResponse(
        upload_id=uuid4(),
        filename=validated.filename,
        content_type=validated.content_type,
        size_bytes=validated.size_bytes,
        sha256=validated.sha256,
        status="validated",
    )
