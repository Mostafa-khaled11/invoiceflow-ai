"""Invoice intake endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import (
    get_app_settings,
    get_db,
    get_invoice_llm_provider,
    get_invoice_storage,
)
from app.core.config import Settings
from app.models.invoice import Invoice, InvoiceStatus
from app.models.invoice_text_extraction import (
    InvoiceTextExtraction,
    InvoiceTextExtractionStatus,
)
from app.schemas.invoices import (
    InvoiceDuplicateDetail,
    InvoiceResponse,
    InvoiceTextExtractionResponse,
)
from app.schemas.structured_extractions import StructuredExtractionResponse
from app.services.invoice_files import (
    UploadValidationError,
    validate_invoice_pdf,
)
from app.services.invoice_intake import (
    InvoiceDuplicateError,
    receive_invoice,
)
from app.services.invoice_llm import InvoiceLLMProvider
from app.services.invoice_storage import InvoiceStorage, InvoiceStorageError
from app.services.invoice_text_extraction import InvoiceTextExtractor
from app.services.structured_invoice_extraction import (
    StructuredExtractionServiceError,
    StructuredInvoiceExtractionService,
)

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


@router.post(
    "/{invoice_id}/extract-text",
    summary="Extract the stored invoice PDF digital text layer",
    response_model=InvoiceTextExtractionResponse,
)
def extract_invoice_text(
    invoice_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    storage: Annotated[InvoiceStorage, Depends(get_invoice_storage)],
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> InvoiceTextExtractionResponse:
    """Run one synchronous, idempotent digital-text extraction."""

    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    existing = session.execute(
        select(InvoiceTextExtraction).where(
            InvoiceTextExtraction.invoice_id == invoice_id
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _extraction_response(invoice_id, existing)

    result = InvoiceTextExtractor().extract(
        storage,
        invoice.storage_key,
        max_pages=settings.max_invoice_pages,
        min_extracted_text_chars=settings.min_extracted_text_chars,
    )
    extraction = InvoiceTextExtraction(
        invoice_id=invoice.id,
        extractor=result.extractor,
        extractor_version=result.extractor_version,
        text_content=result.text_content,
        page_count=result.page_count,
        pages_with_text=result.pages_with_text,
        character_count=result.character_count,
        non_whitespace_character_count=result.non_whitespace_character_count,
        status=result.status.value,
        error_code=result.error_code,
        processing_ms=result.processing_ms,
    )
    invoice.status = result.status.value
    session.add(extraction)
    try:
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        existing = session.execute(
            select(InvoiceTextExtraction).where(
                InvoiceTextExtraction.invoice_id == invoice_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _extraction_response(invoice_id, existing)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The invoice text extraction could not be stored.",
        ) from exc

    return _extraction_response(invoice_id, extraction)


def _extraction_response(
    invoice_id: UUID,
    extraction: InvoiceTextExtraction,
) -> InvoiceTextExtractionResponse:
    """Map persisted extraction metadata without returning raw text."""

    return InvoiceTextExtractionResponse(
        invoice_id=invoice_id,
        extraction_id=extraction.id,
        status=InvoiceTextExtractionStatus(extraction.status),
        extractor=extraction.extractor,
        extractor_version=extraction.extractor_version,
        page_count=extraction.page_count,
        pages_with_text=extraction.pages_with_text,
        character_count=extraction.character_count,
        non_whitespace_character_count=extraction.non_whitespace_character_count,
        error_code=extraction.error_code,
        processing_ms=extraction.processing_ms,
    )


@router.post(
    "/{invoice_id}/extract-structured",
    summary="Extract structured invoice data with the configured LLM",
    response_model=StructuredExtractionResponse,
)
def extract_invoice_structured(
    invoice_id: UUID,
    session: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    provider: Annotated[InvoiceLLMProvider, Depends(get_invoice_llm_provider)],
) -> StructuredExtractionResponse:
    """Run auditable structured extraction without returning source text."""

    try:
        return StructuredInvoiceExtractionService(session, provider, settings).extract(
            invoice_id
        )
    except StructuredExtractionServiceError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc
