"""Business orchestration for provider-backed structured invoice extraction."""

from time import perf_counter
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.invoice import Invoice, InvoiceStatus
from app.models.invoice_structured_extraction import (
    InvoiceStructuredExtraction,
    StructuredExtractionStatus,
)
from app.models.invoice_text_extraction import (
    InvoiceTextExtraction,
    InvoiceTextExtractionStatus,
)
from app.schemas.structured_extractions import (
    StructuredExtractionResponse,
    StructuredInvoiceData,
    validate_structured_invoice_semantics,
)
from app.services.invoice_llm import (
    InvoiceLLMProvider,
    InvoiceLLMProviderError,
)

SCHEMA_VERSION = "invoice-schema-v1"
ERROR_INPUT_TOO_LARGE = "llm_input_too_large"
ERROR_SEMANTIC_VALIDATION = "semantic_validation_failed"


class StructuredExtractionServiceError(RuntimeError):
    """Controlled domain error mapped to a safe HTTP response by the route."""

    def __init__(self, error_code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


class StructuredInvoiceExtractionService:
    """Reusable orchestration service suitable for a future worker."""

    def __init__(
        self,
        session: Session,
        provider: InvoiceLLMProvider,
        settings: Settings,
    ) -> None:
        self._session = session
        self._provider = provider
        self._settings = settings

    def extract(self, invoice_id: UUID) -> StructuredExtractionResponse:
        invoice = self._session.get(Invoice, invoice_id)
        if invoice is None:
            raise StructuredExtractionServiceError(
                "invoice_not_found", "Invoice not found", 404
            )

        text_extraction = self._session.execute(
            select(InvoiceTextExtraction).where(
                InvoiceTextExtraction.invoice_id == invoice_id
            )
        ).scalar_one_or_none()
        if invoice.status == InvoiceStatus.NEEDS_OCR.value:
            raise StructuredExtractionServiceError(
                "ocr_required",
                "Structured extraction requires successful digital text or OCR.",
                409,
            )
        if text_extraction is None:
            raise StructuredExtractionServiceError(
                "text_extraction_required",
                "A successful text extraction is required before structured "
                "extraction.",
                409,
            )
        if text_extraction.status != InvoiceTextExtractionStatus.TEXT_EXTRACTED.value:
            raise StructuredExtractionServiceError(
                "text_extraction_failed",
                "The persisted text extraction was not successful.",
                409,
            )

        identity = {
            "text_extraction_id": text_extraction.id,
            "provider": self._provider.provider_name,
            "model": self._provider.model_name,
            "prompt_version": self._settings.invoice_extraction_prompt_version,
            "schema_version": self._settings.invoice_extraction_schema_version,
        }
        existing = self._find_by_identity(identity)
        if (
            existing is not None
            and existing.status == StructuredExtractionStatus.SUCCESS
        ):
            return _to_response(existing)

        previous_attempts = existing.attempt_count if existing is not None else 0
        text = text_extraction.text_content
        if len(text) > self._settings.max_llm_input_chars:
            self._persist_failure(
                invoice,
                text_extraction,
                identity,
                existing=existing,
                error_code=ERROR_INPUT_TOO_LARGE,
                attempt_count=previous_attempts + 1,
                latency_ms=0,
            )
            raise StructuredExtractionServiceError(
                ERROR_INPUT_TOO_LARGE,
                "Persisted invoice text exceeds the configured LLM input limit.",
                413,
            )

        started = perf_counter()
        try:
            provider_response = self._provider.extract(
                text,
                schema=StructuredInvoiceData.model_json_schema(),
            )
            data = StructuredInvoiceData.model_validate(provider_response.data)
            validate_structured_invoice_semantics(data)
        except InvoiceLLMProviderError as exc:
            latency_ms = _elapsed_ms(started)
            self._persist_failure(
                invoice,
                text_extraction,
                identity,
                existing=existing,
                error_code=exc.error_code,
                attempt_count=previous_attempts + max(exc.attempt_count, 1),
                latency_ms=latency_ms,
            )
            raise StructuredExtractionServiceError(
                exc.error_code,
                "The structured invoice extraction provider failed.",
                502,
            ) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            latency_ms = _elapsed_ms(started)
            raw_output = (
                provider_response.raw_model_output
                if "provider_response" in locals()
                else None
            )
            self._persist_failure(
                invoice,
                text_extraction,
                identity,
                existing=existing,
                error_code=ERROR_SEMANTIC_VALIDATION,
                attempt_count=previous_attempts
                + max(
                    getattr(locals().get("provider_response"), "attempt_count", 1), 1
                ),
                latency_ms=latency_ms,
                raw_model_output=raw_output,
            )
            raise StructuredExtractionServiceError(
                ERROR_SEMANTIC_VALIDATION,
                "The provider output failed application validation.",
                422,
            ) from exc

        record = existing or InvoiceStructuredExtraction(**identity)
        record.invoice_id = invoice.id
        record.status = StructuredExtractionStatus.SUCCESS.value
        record.structured_data = data.model_dump(mode="json")
        record.raw_model_output = provider_response.raw_model_output
        record.input_tokens = provider_response.input_tokens
        record.output_tokens = provider_response.output_tokens
        record.latency_ms = _elapsed_ms(started)
        record.error_code = None
        record.attempt_count = previous_attempts + max(
            provider_response.attempt_count, 1
        )
        invoice.status = InvoiceStatus.STRUCTURED_EXTRACTED.value
        self._session.add(record)
        try:
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            concurrent = self._find_by_identity(identity)
            if (
                concurrent is not None
                and concurrent.status == StructuredExtractionStatus.SUCCESS
            ):
                return _to_response(concurrent)
            raise StructuredExtractionServiceError(
                "structured_extraction_persistence_failed",
                "The structured extraction could not be stored.",
                500,
            ) from exc
        return _to_response(record)

    def _find_by_identity(
        self, identity: dict[str, Any]
    ) -> InvoiceStructuredExtraction | None:
        return self._session.execute(
            select(InvoiceStructuredExtraction).filter_by(**identity)
        ).scalar_one_or_none()

    def _persist_failure(
        self,
        invoice: Invoice,
        text_extraction: InvoiceTextExtraction,
        identity: dict[str, Any],
        *,
        existing: InvoiceStructuredExtraction | None,
        error_code: str,
        attempt_count: int,
        latency_ms: int,
        raw_model_output: dict[str, Any] | None = None,
    ) -> InvoiceStructuredExtraction:
        record = existing or InvoiceStructuredExtraction(
            invoice_id=invoice.id,
            **identity,
        )
        record.status = StructuredExtractionStatus.FAILED.value
        record.structured_data = None
        record.raw_model_output = raw_model_output
        record.input_tokens = None
        record.output_tokens = None
        record.latency_ms = latency_ms
        record.error_code = error_code
        record.attempt_count = attempt_count
        self._session.add(record)
        try:
            self._session.commit()
        except SQLAlchemyError as exc:
            self._session.rollback()
            raise StructuredExtractionServiceError(
                "structured_extraction_persistence_failed",
                "The structured extraction failure could not be stored.",
                500,
            ) from exc
        return record


def _to_response(record: InvoiceStructuredExtraction) -> StructuredExtractionResponse:
    if (
        record.status != StructuredExtractionStatus.SUCCESS.value
        or not record.structured_data
    ):
        raise StructuredExtractionServiceError(
            record.error_code or "structured_extraction_failed",
            "The structured invoice extraction did not succeed.",
            502,
        )
    return StructuredExtractionResponse(
        invoice_id=record.invoice_id,
        structured_extraction_id=record.id,
        status=record.status,
        provider=record.provider,
        model=record.model,
        prompt_version=record.prompt_version,
        schema_version=record.schema_version,
        data=StructuredInvoiceData.model_validate(record.structured_data),
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        latency_ms=record.latency_ms,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
