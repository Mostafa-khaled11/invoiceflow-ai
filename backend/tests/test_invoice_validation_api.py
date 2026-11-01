"""API tests for the invoice validation endpoints (Task 5.2)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from app.api.deps import get_invoice_llm_provider
from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_validation import InvoiceValidation
from app.schemas.structured_extractions import StructuredInvoiceData
from app.services.business_validation import (
    ValidationRuleStatus,
    validate_invoice,
)
from app.services.invoice_llm import GroqInvoiceLLMProvider
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tests.test_business_validation import make_invoice
from tests.test_invoice_text_extraction import _pdf_with_text, _upload
from tests.test_structured_invoice_extraction import (
    STRUCTURED_URL,
    TEXT_LAYER,
    FakeProvider,
    _use_provider,
    _valid_data,
)

VALIDATE_URL = "/api/v1/invoices/{}/validate"
READ_URL = "/api/v1/invoices/{}/validation"

FINDING_FIELDS = {
    "rule_id",
    "rule_name",
    "status",
    "severity",
    "message",
    "actual_value",
    "expected_value",
    "difference",
    "affected_fields",
    "metadata",
}
VALIDATION_FIELDS = {
    "validation_id",
    "invoice_id",
    "structured_extraction_id",
    "ruleset_version",
    "final_decision",
    "pass_count",
    "fail_count",
    "not_evaluable_count",
    "created_at",
    "findings",
}


def _seed_invoice_with_extraction(
    client: TestClient,
    data: dict[str, object] | None = None,
) -> UUID:
    """Drive upload, text extraction, and structured extraction through the API."""

    provider = FakeProvider(data if data is not None else _valid_data())
    _use_provider(client, provider)
    invoice_id = _upload(client, _pdf_with_text([TEXT_LAYER]))
    assert client.post(f"/api/v1/invoices/{invoice_id}/extract-text").status_code == 200
    structured = client.post(STRUCTURED_URL.format(invoice_id))
    assert structured.status_code == 200
    return invoice_id


def test_validate_persists_report_with_findings(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_invoice_with_extraction(client)
    expected = validate_invoice(StructuredInvoiceData.model_validate(_valid_data()))

    response = client.post(VALIDATE_URL.format(invoice_id))

    assert response.status_code == 200
    payload = response.json()
    with Session(engine) as session:
        extraction_id = session.execute(
            select(InvoiceStructuredExtraction.id).where(
                InvoiceStructuredExtraction.invoice_id == invoice_id
            )
        ).scalar_one()
    assert payload["invoice_id"] == str(invoice_id)
    assert payload["structured_extraction_id"] == str(extraction_id)
    assert set(payload) == VALIDATION_FIELDS
    assert payload["ruleset_version"] == "invoice-validation-v1"
    assert payload["final_decision"] == expected.final_decision.value
    assert payload["pass_count"] == sum(
        1
        for result in expected.rule_results
        if result.status == ValidationRuleStatus.PASS
    )
    assert payload["fail_count"] == 0
    assert payload["not_evaluable_count"] == sum(
        1
        for result in expected.rule_results
        if result.status == ValidationRuleStatus.NOT_EVALUABLE
    )
    assert [finding["rule_id"] for finding in payload["findings"]] == [
        result.rule_id for result in expected.rule_results
    ]
    for finding, result in zip(payload["findings"], expected.rule_results, strict=True):
        assert set(finding) == FINDING_FIELDS
        assert finding["rule_name"] == result.rule_name
        assert finding["status"] == result.status.value
        assert finding["severity"] == result.severity.value
        assert finding["message"] == result.message
        assert finding["affected_fields"] == result.affected_fields
        assert isinstance(finding["metadata"], dict)
        assert finding["metadata"] == result.metadata


def test_repeat_validate_returns_existing_run(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_invoice_with_extraction(client)

    first = client.post(VALIDATE_URL.format(invoice_id))
    second = client.post(VALIDATE_URL.format(invoice_id))

    assert first.status_code == second.status_code == 200
    assert second.json()["validation_id"] == first.json()["validation_id"]
    with Session(engine) as session:
        runs = session.execute(
            select(func.count()).select_from(InvoiceValidation)
        ).scalar_one()
    assert runs == 1


def test_validate_unknown_invoice_maps_to_not_found(client: TestClient) -> None:
    response = client.post(VALIDATE_URL.format(uuid4()))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "invoice_not_found"


def test_validate_without_structured_extraction_maps_to_conflict(
    client: TestClient,
) -> None:
    _use_provider(client, FakeProvider())
    invoice_id = _upload(client, _pdf_with_text([TEXT_LAYER]))
    assert client.post(f"/api/v1/invoices/{invoice_id}/extract-text").status_code == 200

    response = client.post(VALIDATE_URL.format(invoice_id))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "structured_extraction_required"


def test_read_before_first_validate_maps_to_not_found(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(client)

    response = client.get(READ_URL.format(invoice_id))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "validation_not_found"


def test_read_returns_persisted_validation(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(client)

    created = client.post(VALIDATE_URL.format(invoice_id))
    read = client.get(READ_URL.format(invoice_id))

    assert created.status_code == 200
    assert read.status_code == 200
    assert read.json() == created.json()


def test_read_does_not_return_stale_validation_after_newer_extraction(
    client: TestClient, engine: Engine
) -> None:
    """GET resolves the current extraction, never an older validation row."""

    invoice_id = _seed_invoice_with_extraction(client)
    first = client.post(VALIDATE_URL.format(invoice_id))
    assert first.status_code == 200
    validation_a = first.json()

    with Session(engine) as session:
        extraction_a = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == invoice_id
            )
        ).scalar_one()
        extraction_b = InvoiceStructuredExtraction(
            invoice_id=invoice_id,
            text_extraction_id=extraction_a.text_extraction_id,
            provider="fake",
            model="fake-model",
            prompt_version="invoice-extraction-v2",
            schema_version="invoice-schema-v1",
            status="success",
            structured_data=_valid_data(),
            created_at=extraction_a.created_at + timedelta(seconds=1),
        )
        session.add(extraction_b)
        session.commit()
        extraction_b_id = extraction_b.id

    stale_read = client.get(READ_URL.format(invoice_id))
    assert stale_read.status_code == 404
    assert stale_read.json()["detail"]["code"] == "validation_not_found"

    second = client.post(VALIDATE_URL.format(invoice_id))
    assert second.status_code == 200
    validation_b = second.json()
    assert validation_b["validation_id"] != validation_a["validation_id"]
    assert validation_b["structured_extraction_id"] == str(extraction_b_id)
    assert client.get(READ_URL.format(invoice_id)).json() == validation_b

    with Session(engine) as session:
        historical = session.get(InvoiceValidation, UUID(validation_a["validation_id"]))
        assert historical is not None
        assert historical.structured_extraction_id != extraction_b_id


def test_validate_corrupted_extraction_requires_review(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(
        client, make_invoice(total_amount="105.00").model_dump(mode="json")
    )

    response = client.post(VALIDATE_URL.format(invoice_id))

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_decision"] == "REVIEW_REQUIRED"
    assert payload["fail_count"] == 1
    fin005 = next(
        finding for finding in payload["findings"] if finding["rule_id"] == "FIN005"
    )
    assert fin005["status"] == "FAIL"
    assert fin005["actual_value"] == "105.00"
    assert fin005["expected_value"] == "104.00"
    assert fin005["difference"] == "1.00"


def test_validate_summary_tax_stays_warning(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(
        client,
        make_invoice(
            summary_tax=True, total_amount="104.00", line_total="90.00"
        ).model_dump(mode="json"),
    )

    response = client.post(VALIDATE_URL.format(invoice_id))

    assert response.status_code == 200
    payload = response.json()
    assert payload["final_decision"] == "WARNING"
    assert payload["final_decision"] != "REVIEW_REQUIRED"
    assert payload["fail_count"] == 0
    fin004 = next(
        finding for finding in payload["findings"] if finding["rule_id"] == "FIN004"
    )
    assert fin004["status"] == "NOT_EVALUABLE"


def test_validation_endpoints_never_touch_the_llm_provider(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice_id = _seed_invoice_with_extraction(client)

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("validation must not invoke the LLM provider")

    monkeypatch.setattr(GroqInvoiceLLMProvider, "extract", _forbidden)
    cast(Any, client.app).dependency_overrides[get_invoice_llm_provider] = _forbidden

    validated = client.post(VALIDATE_URL.format(invoice_id))
    read = client.get(READ_URL.format(invoice_id))

    assert validated.status_code == 200
    assert read.status_code == 200
