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
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import (
    ReviewCaseStatus,
    ReviewResolution,
    WorkflowState,
)
from app.schemas.invoices import (
    InvoiceDuplicateDetail,
    InvoiceResponse,
    InvoiceTextExtractionResponse,
)
from app.schemas.structured_extractions import StructuredExtractionResponse
from app.schemas.validations import (
    InvoiceValidationResponse,
    ValidationFindingResponse,
)
from app.schemas.workflows import (
    InvoiceWorkflowResponse,
    InvoiceWorkflowRouteResponse,
    WorkflowReviewCaseResponse,
)
from app.services.business_validation import (
    FinalValidationDecision,
    ValidationRuleStatus,
    ValidationSeverity,
)
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
from app.services.invoice_validation import (
    InvoiceValidationError,
    find_invoice_validation,
    validate_and_persist_invoice,
)
from app.services.invoice_workflow import (
    WorkflowRoutingError,
    WorkflowRoutingResult,
    resolve_current_invoice_workflow,
    route_invoice_workflow,
)
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


@router.post(
    "/{invoice_id}/validate",
    summary="Validate the invoice against the deterministic business ruleset",
    response_model=InvoiceValidationResponse,
)
def run_invoice_validation(
    invoice_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> InvoiceValidationResponse:
    """Validate the canonical structured extraction and persist the report.

    Deterministic and provider-free: reads the latest successful structured
    extraction, returns the already persisted run for the same extraction
    and ruleset version, or stores a new run with all findings atomically.
    """

    try:
        run, _created = validate_and_persist_invoice(session, invoice_id)
    except InvoiceValidationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc

    return _validation_response(run)


@router.get(
    "/{invoice_id}/validation",
    summary="Read the persisted validation of the current structured extraction",
    response_model=InvoiceValidationResponse,
)
def get_invoice_validation(
    invoice_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> InvoiceValidationResponse:
    """Return the stored validation run including every finding."""

    try:
        run = find_invoice_validation(session, invoice_id)
    except InvoiceValidationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc

    return _validation_response(run)


@router.post(
    "/{invoice_id}/workflow/route",
    summary="Route the current validation into its initial workflow state",
    response_model=InvoiceWorkflowRouteResponse,
)
def route_invoice_workflow_endpoint(
    invoice_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> InvoiceWorkflowRouteResponse:
    """Create or retrieve the idempotent initial workflow for an invoice."""

    try:
        result = route_invoice_workflow(session, invoice_id)
    except WorkflowRoutingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc

    return _workflow_route_response(result)


@router.get(
    "/{invoice_id}/workflow",
    summary="Read the workflow for the current validation",
    response_model=InvoiceWorkflowResponse,
)
def get_invoice_workflow(
    invoice_id: UUID,
    session: Annotated[Session, Depends(get_db)],
) -> InvoiceWorkflowResponse:
    """Return workflow state only when it belongs to the current validation."""

    try:
        result = resolve_current_invoice_workflow(session, invoice_id)
    except WorkflowRoutingError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.error_code, "message": exc.message},
        ) from exc

    return _workflow_response(result)


def _validation_response(run: InvoiceValidation) -> InvoiceValidationResponse:
    return InvoiceValidationResponse(
        validation_id=run.id,
        invoice_id=run.invoice_id,
        structured_extraction_id=run.structured_extraction_id,
        ruleset_version=run.ruleset_version,
        final_decision=FinalValidationDecision(run.final_decision),
        pass_count=run.pass_count,
        fail_count=run.fail_count,
        not_evaluable_count=run.not_evaluable_count,
        created_at=run.created_at,
        findings=[
            ValidationFindingResponse(
                rule_id=finding.rule_id,
                rule_name=finding.rule_name,
                status=ValidationRuleStatus(finding.status),
                severity=ValidationSeverity(finding.severity),
                message=finding.message,
                actual_value=finding.actual_value,
                expected_value=finding.expected_value,
                difference=finding.difference,
                affected_fields=finding.affected_fields,
                metadata=finding.meta,
            )
            for finding in run.findings
        ],
    )


def _workflow_response(result: WorkflowRoutingResult) -> InvoiceWorkflowResponse:
    review_case = result.review_case
    return InvoiceWorkflowResponse(
        workflow_id=result.workflow_id,
        validation_id=result.validation_id,
        state=WorkflowState(result.state),
        state_version=result.state_version,
        created_at=result.created_at,
        updated_at=result.updated_at,
        validation_decision=FinalValidationDecision(result.validation_decision),
        ruleset_version=result.ruleset_version,
        review_case=(
            None
            if review_case is None
            else WorkflowReviewCaseResponse(
                review_case_id=review_case.review_case_id,
                status=ReviewCaseStatus(review_case.status),
                resolution=(
                    None
                    if review_case.resolution is None
                    else ReviewResolution(review_case.resolution)
                ),
            )
        ),
    )


def _workflow_route_response(
    result: WorkflowRoutingResult,
) -> InvoiceWorkflowRouteResponse:
    response = _workflow_response(result)
    return InvoiceWorkflowRouteResponse(
        **response.model_dump(),
        created=result.created,
    )
