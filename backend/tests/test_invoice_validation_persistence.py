"""Service-layer tests for persisted deterministic validation runs (Task 5.2)."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import app.services.invoice_validation as invoice_validation_service
import pytest
from app.models.invoice import Invoice
from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_text_extraction import InvoiceTextExtraction
from app.models.invoice_validation import InvoiceValidation, InvoiceValidationFinding
from app.schemas.structured_extractions import StructuredInvoiceData
from app.services.business_validation import (
    RULESET_VERSION,
    ValidationRuleStatus,
    validate_invoice,
)
from app.services.business_validation.registry import RULE_REGISTRY
from app.services.invoice_validation import (
    InvoiceValidationError,
    find_invoice_validation,
    resolve_canonical_structured_extraction,
    validate_and_persist_invoice,
)
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from tests.test_business_validation import make_invoice


def _seed_invoice(engine: Engine) -> UUID:
    """Insert a bare invoice row and return its id."""

    with Session(engine) as session:
        invoice = Invoice(
            original_filename="seed.pdf",
            storage_key=f"{uuid4()}.pdf",
            content_type="application/pdf",
            size_bytes=64,
            sha256=uuid4().hex + uuid4().hex,
        )
        session.add(invoice)
        session.commit()
        return invoice.id


def _seed_extraction(
    engine: Engine,
    data: StructuredInvoiceData | dict[str, Any] | None,
    *,
    status: str = "success",
    created_at: datetime | None = None,
    prompt_version: str = "invoice-extraction-v1",
    invoice_id: UUID | None = None,
    extraction_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    """Insert (or extend) an invoice with text and structured extraction rows."""

    payload: dict[str, Any] | None
    if data is None:
        payload = None
    elif isinstance(data, dict):
        payload = data
    else:
        payload = data.model_dump(mode="json")

    with Session(engine) as session:
        if invoice_id is None:
            resolved_invoice_id = uuid4()
            text_id = uuid4()
            session.add(
                Invoice(
                    id=resolved_invoice_id,
                    original_filename="seed.pdf",
                    storage_key=f"{uuid4()}.pdf",
                    content_type="application/pdf",
                    size_bytes=64,
                    sha256=uuid4().hex + uuid4().hex,
                )
            )
            session.add(
                InvoiceTextExtraction(
                    id=text_id,
                    invoice_id=resolved_invoice_id,
                    extractor="pypdf",
                    extractor_version="test",
                    text_content="seed text",
                    page_count=1,
                    pages_with_text=1,
                    character_count=9,
                    non_whitespace_character_count=9,
                    status="text_extracted",
                    processing_ms=1,
                )
            )
        else:
            resolved_invoice_id = invoice_id
            text_id = session.execute(
                select(InvoiceTextExtraction.id).where(
                    InvoiceTextExtraction.invoice_id == invoice_id
                )
            ).scalar_one()

        extraction = InvoiceStructuredExtraction(
            id=extraction_id or uuid4(),
            invoice_id=resolved_invoice_id,
            text_extraction_id=text_id,
            provider="fake",
            model="fake-model",
            prompt_version=prompt_version,
            schema_version="invoice-schema-v1",
            status=status,
            structured_data=payload,
        )
        if created_at is not None:
            extraction.created_at = created_at
        session.add(extraction)
        session.commit()
        return resolved_invoice_id, extraction.id


def test_successful_run_persists_report_and_findings(engine: Engine) -> None:
    data = make_invoice()
    invoice_id, extraction_id = _seed_extraction(engine, data)

    with Session(engine) as session:
        run, created = validate_and_persist_invoice(session, invoice_id)
        assert created is True
        validation_id = run.id

    expected = validate_invoice(data)
    with Session(engine) as session:
        persisted = session.get(InvoiceValidation, validation_id)
        assert persisted is not None
        assert persisted.invoice_id == invoice_id
        assert persisted.structured_extraction_id == extraction_id
        assert persisted.ruleset_version == RULESET_VERSION
        assert persisted.ruleset_version == "invoice-validation-v1"
        assert persisted.final_decision == expected.final_decision.value
        assert persisted.pass_count == sum(
            1
            for result in expected.rule_results
            if result.status == ValidationRuleStatus.PASS
        )
        assert persisted.fail_count == sum(
            1
            for result in expected.rule_results
            if result.status == ValidationRuleStatus.FAIL
        )
        assert persisted.not_evaluable_count == sum(
            1
            for result in expected.rule_results
            if result.status == ValidationRuleStatus.NOT_EVALUABLE
        )
        findings = list(persisted.findings)
        assert [finding.rule_id for finding in findings] == [
            result.rule_id for result in expected.rule_results
        ]
        for finding, result in zip(findings, expected.rule_results, strict=True):
            assert finding.rule_name == result.rule_name
            assert finding.status == result.status.value
            assert finding.severity == result.severity.value
            assert finding.message == result.message
            assert finding.actual_value == result.actual_value
            assert finding.expected_value == result.expected_value
            assert finding.difference == result.difference
            assert finding.affected_fields == result.affected_fields
            assert finding.meta == result.metadata


def test_validation_is_idempotent(engine: Engine) -> None:
    invoice_id, _ = _seed_extraction(engine, make_invoice())

    with Session(engine) as session:
        first, created_first = validate_and_persist_invoice(session, invoice_id)
        first_id = first.id
    with Session(engine) as session:
        second, created_second = validate_and_persist_invoice(session, invoice_id)
        second_id = second.id

    assert created_first is True
    assert created_second is False
    assert second_id == first_id

    with Session(engine) as session:
        runs = session.execute(
            select(func.count()).select_from(InvoiceValidation)
        ).scalar_one()
        findings = session.execute(
            select(func.count()).select_from(InvoiceValidationFinding)
        ).scalar_one()
    assert runs == 1
    assert findings == len(RULE_REGISTRY)


def test_database_rejects_duplicate_extraction_ruleset(engine: Engine) -> None:
    invoice_id, extraction_id = _seed_extraction(engine, make_invoice())

    with Session(engine) as session:
        validate_and_persist_invoice(session, invoice_id)

    with Session(engine) as session:
        duplicate = InvoiceValidation(
            invoice_id=invoice_id,
            structured_extraction_id=extraction_id,
            ruleset_version=RULESET_VERSION,
            final_decision="PASS",
            pass_count=0,
            fail_count=0,
            not_evaluable_count=0,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.commit()


def test_insert_race_returns_existing_run(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice_id, extraction_id = _seed_extraction(engine, make_invoice())

    with Session(engine) as session:
        winner = InvoiceValidation(
            invoice_id=invoice_id,
            structured_extraction_id=extraction_id,
            ruleset_version=RULESET_VERSION,
            final_decision="PASS",
            pass_count=0,
            fail_count=0,
            not_evaluable_count=0,
        )
        session.add(winner)
        session.commit()
        winner_id = winner.id

    original = invoice_validation_service._find_validation
    calls = {"count": 0}

    def race_spy(
        session: Session, structured_extraction_id: UUID, ruleset_version: str
    ) -> InvoiceValidation | None:
        calls["count"] += 1
        if calls["count"] == 1:
            return None  # The losing request misses the pre-check.
        return original(session, structured_extraction_id, ruleset_version)

    monkeypatch.setattr(invoice_validation_service, "_find_validation", race_spy)

    with Session(engine) as session:
        run, created = validate_and_persist_invoice(session, invoice_id)
        run_id = run.id

    assert created is False
    assert run_id == winner_id
    assert calls["count"] == 2


def test_validation_commit_failure_rolls_back_run_and_findings(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed commit cannot leave a parent run or child findings behind."""

    invoice_id, _ = _seed_extraction(engine, make_invoice())

    def fail_commit(_session: Session) -> None:
        raise SQLAlchemyError("simulated finding persistence failure")

    monkeypatch.setattr(Session, "commit", fail_commit)

    with Session(engine) as session:
        with pytest.raises(InvoiceValidationError) as exc_info:
            validate_and_persist_invoice(session, invoice_id)

    assert exc_info.value.error_code == "validation_persistence_failed"
    with Session(engine) as session:
        assert (
            session.execute(
                select(func.count()).select_from(InvoiceValidation)
            ).scalar_one()
            == 0
        )
        assert (
            session.execute(
                select(func.count()).select_from(InvoiceValidationFinding)
            ).scalar_one()
            == 0
        )


def test_missing_invoice_maps_to_not_found(engine: Engine) -> None:
    with Session(engine) as session:
        with pytest.raises(InvoiceValidationError) as exc_info:
            validate_and_persist_invoice(session, uuid4())

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_code == "invoice_not_found"


def test_invoice_without_structured_extraction_maps_to_conflict(engine: Engine) -> None:
    invoice_id = _seed_invoice(engine)

    with Session(engine) as session:
        with pytest.raises(InvoiceValidationError) as exc_info:
            validate_and_persist_invoice(session, invoice_id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "structured_extraction_required"


def test_failed_structured_extraction_does_not_validate(engine: Engine) -> None:
    invoice_id, _ = _seed_extraction(engine, None, status="failed")

    with Session(engine) as session:
        with pytest.raises(InvoiceValidationError) as exc_info:
            validate_and_persist_invoice(session, invoice_id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "structured_extraction_required"


def test_unreadable_structured_payload_maps_to_server_error(engine: Engine) -> None:
    invoice_id, _ = _seed_extraction(engine, {"broken": "payload"})

    with Session(engine) as session:
        with pytest.raises(InvoiceValidationError) as exc_info:
            validate_and_persist_invoice(session, invoice_id)

    assert exc_info.value.status_code == 500
    assert exc_info.value.error_code == "structured_extraction_invalid"


def test_structured_extraction_is_not_mutated(engine: Engine) -> None:
    data = make_invoice()
    invoice_id, _ = _seed_extraction(engine, data)

    with Session(engine) as session:
        extraction = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == invoice_id
            )
        ).scalar_one()
        before_data = deepcopy(extraction.structured_data)
        before_status = extraction.status
        before_updated_at = extraction.updated_at

    with Session(engine) as session:
        validate_and_persist_invoice(session, invoice_id)

    with Session(engine) as session:
        extraction = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == invoice_id
            )
        ).scalar_one()
    assert extraction.structured_data == before_data
    assert extraction.structured_data == data.model_dump(mode="json")
    assert extraction.status == before_status == "success"
    assert extraction.updated_at == before_updated_at


def test_corrupted_total_persists_fail_and_review(engine: Engine) -> None:
    data = make_invoice(total_amount="105.00")
    invoice_id, _ = _seed_extraction(engine, data)

    with Session(engine) as session:
        run, created = validate_and_persist_invoice(session, invoice_id)
        assert created is True
        run_id = run.id

    with Session(engine) as session:
        persisted = session.get(InvoiceValidation, run_id)
        assert persisted is not None
        assert persisted.final_decision == "REVIEW_REQUIRED"
        assert persisted.fail_count == 1
        finding = session.execute(
            select(InvoiceValidationFinding).where(
                InvoiceValidationFinding.validation_id == run_id,
                InvoiceValidationFinding.rule_id == "FIN005",
            )
        ).scalar_one()
        assert finding.status == "FAIL"
        assert finding.actual_value == "105.00"
        assert finding.expected_value == "104.00"
        assert finding.difference == "1.00"


def test_summary_tax_warning_persists_without_review(engine: Engine) -> None:
    data = make_invoice(summary_tax=True, total_amount="104.00", line_total="90.00")
    invoice_id, _ = _seed_extraction(engine, data)
    expected = validate_invoice(data)

    with Session(engine) as session:
        run, created = validate_and_persist_invoice(session, invoice_id)
        assert created is True
        run_id = run.id

    with Session(engine) as session:
        persisted = session.get(InvoiceValidation, run_id)
        assert persisted is not None
        assert persisted.final_decision == "WARNING"
        assert persisted.final_decision != "REVIEW_REQUIRED"
        assert persisted.final_decision == expected.final_decision.value
        assert persisted.fail_count == 0
        assert persisted.not_evaluable_count == sum(
            1
            for result in expected.rule_results
            if result.status == ValidationRuleStatus.NOT_EVALUABLE
        )
        fin004 = session.execute(
            select(InvoiceValidationFinding).where(
                InvoiceValidationFinding.validation_id == run_id,
                InvoiceValidationFinding.rule_id == "FIN004",
            )
        ).scalar_one()
        assert fin004.status == "NOT_EVALUABLE"


def test_round_trip_preserves_finding_values(engine: Engine) -> None:
    data = make_invoice(total_amount="105.00")
    invoice_id, _ = _seed_extraction(engine, data)
    expected = validate_invoice(data)
    expected_fin005 = next(
        result for result in expected.rule_results if result.rule_id == "FIN005"
    )

    with Session(engine) as session:
        run, _created = validate_and_persist_invoice(session, invoice_id)
        run_id = run.id

    with Session(engine) as session:
        finding = session.execute(
            select(InvoiceValidationFinding).where(
                InvoiceValidationFinding.validation_id == run_id,
                InvoiceValidationFinding.rule_id == "FIN005",
            )
        ).scalar_one()

    assert isinstance(finding.actual_value, str)
    assert finding.actual_value == expected_fin005.actual_value == "105.00"
    assert finding.expected_value == expected_fin005.expected_value == "104.00"
    assert finding.difference == expected_fin005.difference == "1.00"
    assert finding.affected_fields == expected_fin005.affected_fields
    assert finding.affected_fields
    assert finding.meta == expected_fin005.metadata
    assert finding.meta
    assert finding.status == expected_fin005.status.value == "FAIL"
    assert finding.severity == expected_fin005.severity.value


def test_find_before_first_validation_maps_to_not_found(engine: Engine) -> None:
    invoice_id, _ = _seed_extraction(engine, make_invoice())

    with Session(engine) as session:
        with pytest.raises(InvoiceValidationError) as exc_info:
            find_invoice_validation(session, invoice_id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.error_code == "validation_not_found"


def test_find_returns_persisted_run(engine: Engine) -> None:
    invoice_id, _ = _seed_extraction(engine, make_invoice())

    with Session(engine) as session:
        run, _created = validate_and_persist_invoice(session, invoice_id)
        direct_id = run.id

    with Session(engine) as session:
        found = find_invoice_validation(session, invoice_id)
        assert found.id == direct_id


def test_canonical_selection_prefers_latest_created_success(engine: Engine) -> None:
    invoice_id, _ = _seed_extraction(
        engine,
        make_invoice(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        prompt_version="invoice-extraction-v1",
    )
    _, newer_id = _seed_extraction(
        engine,
        make_invoice(),
        invoice_id=invoice_id,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        prompt_version="invoice-extraction-v2",
    )

    with Session(engine) as session:
        resolved = resolve_canonical_structured_extraction(session, invoice_id)
        assert resolved.id == newer_id

        run, created = validate_and_persist_invoice(session, invoice_id)
        assert created is True
        run_id = run.id

    with Session(engine) as session:
        persisted = session.get(InvoiceValidation, run_id)
        assert persisted is not None
        assert persisted.structured_extraction_id == newer_id


def test_failed_newer_extraction_does_not_replace_successful_older_one(
    engine: Engine,
) -> None:
    invoice_id, older_id = _seed_extraction(
        engine,
        make_invoice(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    _seed_extraction(
        engine,
        None,
        invoice_id=invoice_id,
        status="failed",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        prompt_version="invoice-extraction-v2",
    )

    with Session(engine) as session:
        resolved = resolve_canonical_structured_extraction(session, invoice_id)
        resolved_id = resolved.id
        run, created = validate_and_persist_invoice(session, invoice_id)
        run_extraction_id = run.structured_extraction_id

    assert resolved_id == older_id
    assert created is True
    assert run_extraction_id == older_id


def test_canonical_selection_uses_id_tiebreak_for_equal_timestamps(
    engine: Engine,
) -> None:
    invoice_id, lower_id = _seed_extraction(
        engine,
        make_invoice(),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        extraction_id=UUID("00000000-0000-0000-0000-000000000001"),
    )
    _, higher_id = _seed_extraction(
        engine,
        make_invoice(),
        invoice_id=invoice_id,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        prompt_version="invoice-extraction-v2",
        extraction_id=UUID("00000000-0000-0000-0000-000000000002"),
    )

    with Session(engine) as session:
        resolved = resolve_canonical_structured_extraction(session, invoice_id)
        resolved_id = resolved.id

    assert lower_id < higher_id
    assert resolved_id == higher_id
