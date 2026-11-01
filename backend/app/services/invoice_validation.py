"""Business orchestration that persists deterministic validation runs.

Bridges the SQLAlchemy-free business validation engine to PostgreSQL: it
resolves the canonical successful structured extraction, executes the
deterministic ruleset, and atomically stores the run and its findings.

Guarantees:

- idempotency per (structured_extraction_id, ruleset_version) — provable at
  the database level by `uq_invoice_validations_extraction_ruleset` and
  recovered from insert races like every other child table;
- the validated `StructuredInvoiceData` payload is never modified; rules are
  read-only and findings are stored separately;
- no provider, prompt, or text extraction work happens here — validation
  only reads an already-successful structured extraction row.
"""

from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.invoice import Invoice
from app.models.invoice_structured_extraction import (
    InvoiceStructuredExtraction,
    StructuredExtractionStatus,
)
from app.models.invoice_validation import InvoiceValidation, InvoiceValidationFinding
from app.schemas.structured_extractions import StructuredInvoiceData
from app.services.business_validation import (
    RULESET_VERSION,
    ValidationRuleStatus,
    validate_invoice,
)

ERROR_INVOICE_NOT_FOUND = "invoice_not_found"
ERROR_STRUCTURED_EXTRACTION_REQUIRED = "structured_extraction_required"
ERROR_STRUCTURED_EXTRACTION_INVALID = "structured_extraction_invalid"
ERROR_VALIDATION_NOT_FOUND = "validation_not_found"
ERROR_VALIDATION_PERSISTENCE_FAILED = "validation_persistence_failed"


class InvoiceValidationError(RuntimeError):
    """Controlled domain error mapped to a safe HTTP response by the route."""

    def __init__(self, error_code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status_code = status_code


def resolve_canonical_structured_extraction(
    session: Session, invoice_id: UUID
) -> InvoiceStructuredExtraction:
    """Return the extraction validation must run against.

    The canonical selection is the latest successful structured extraction
    for the invoice (highest created_at; deterministic id tie-break), because
    the current schema version-by-version rows have no explicit canonical
    pointer. The selected row must belong to the invoice, read SUCCESS, and
    carry a structured payload.
    """

    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise InvoiceValidationError(ERROR_INVOICE_NOT_FOUND, "Invoice not found", 404)

    extraction = (
        session.execute(
            select(InvoiceStructuredExtraction)
            .where(
                InvoiceStructuredExtraction.invoice_id == invoice_id,
                InvoiceStructuredExtraction.status
                == StructuredExtractionStatus.SUCCESS.value,
            )
            .order_by(
                InvoiceStructuredExtraction.created_at.desc(),
                InvoiceStructuredExtraction.id.desc(),
            )
        )
        .scalars()
        .first()
    )
    if extraction is None:
        raise InvoiceValidationError(
            ERROR_STRUCTURED_EXTRACTION_REQUIRED,
            "A successful structured extraction is required before validation.",
            409,
        )
    return extraction


def validate_and_persist_invoice(
    session: Session, invoice_id: UUID
) -> tuple[InvoiceValidation, bool]:
    """Validate the canonical extraction and persist run plus findings once.

    Returns `(validation, created)`: `created` is False when the run already
    existed (idempotent replay or a concurrent request won the insert race).
    """

    extraction = resolve_canonical_structured_extraction(session, invoice_id)

    existing = _find_validation(session, extraction.id, RULESET_VERSION)
    if existing is not None:
        return existing, False

    try:
        data = StructuredInvoiceData.model_validate(extraction.structured_data)
    except ValidationError as exc:
        raise InvoiceValidationError(
            ERROR_STRUCTURED_EXTRACTION_INVALID,
            "The persisted structured extraction payload is unreadable.",
            500,
        ) from exc

    report = validate_invoice(data)
    run = InvoiceValidation(
        invoice_id=invoice_id,
        structured_extraction_id=extraction.id,
        ruleset_version=report.ruleset_version,
        final_decision=report.final_decision.value,
        pass_count=sum(
            1
            for result in report.rule_results
            if result.status == ValidationRuleStatus.PASS
        ),
        fail_count=sum(
            1
            for result in report.rule_results
            if result.status == ValidationRuleStatus.FAIL
        ),
        not_evaluable_count=sum(
            1
            for result in report.rule_results
            if result.status == ValidationRuleStatus.NOT_EVALUABLE
        ),
        findings=[
            InvoiceValidationFinding(
                sequence_number=sequence,
                rule_id=result.rule_id,
                rule_name=result.rule_name,
                status=result.status.value,
                severity=result.severity.value,
                message=result.message,
                actual_value=result.actual_value,
                expected_value=result.expected_value,
                difference=result.difference,
                affected_fields=list(result.affected_fields),
                meta=dict(result.metadata),
            )
            for sequence, result in enumerate(report.rule_results)
        ],
    )
    session.add(run)
    try:
        session.commit()
    except SQLAlchemyError as exc:
        session.rollback()
        recovered = _find_validation(session, extraction.id, RULESET_VERSION)
        if recovered is not None:
            return recovered, False
        raise InvoiceValidationError(
            ERROR_VALIDATION_PERSISTENCE_FAILED,
            "The validation result could not be stored.",
            500,
        ) from exc

    return run, True


def find_invoice_validation(session: Session, invoice_id: UUID) -> InvoiceValidation:
    """Return the persisted validation of the canonical extraction."""

    extraction = resolve_canonical_structured_extraction(session, invoice_id)
    run = find_validation_for_extraction(session, extraction.id)
    if run is None:
        raise InvoiceValidationError(
            ERROR_VALIDATION_NOT_FOUND,
            "The current structured extraction has not been validated yet.",
            404,
        )
    return run


def find_validation_for_extraction(
    session: Session,
    structured_extraction_id: UUID,
    ruleset_version: str = RULESET_VERSION,
) -> InvoiceValidation | None:
    """Return validation for one extraction under the active ruleset."""

    return _find_validation(session, structured_extraction_id, ruleset_version)


def _find_validation(
    session: Session, structured_extraction_id: UUID, ruleset_version: str
) -> InvoiceValidation | None:
    return session.execute(
        select(InvoiceValidation).filter_by(
            structured_extraction_id=structured_extraction_id,
            ruleset_version=ruleset_version,
        )
    ).scalar_one_or_none()
