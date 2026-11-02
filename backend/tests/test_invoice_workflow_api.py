"""Offline API tests for the initial invoice workflow endpoints."""

from datetime import timedelta
from uuid import UUID, uuid4

from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import InvoiceWorkflow, WorkflowEvent
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tests.test_business_validation import make_invoice
from tests.test_invoice_text_extraction import _pdf_with_text, _upload
from tests.test_invoice_validation_api import _seed_invoice_with_extraction
from tests.test_structured_invoice_extraction import (
    TEXT_LAYER,
    FakeProvider,
    _use_provider,
    _valid_data,
)

ROUTE_URL = "/api/v1/invoices/{}/workflow/route"
READ_URL = "/api/v1/invoices/{}/workflow"


def test_route_creates_ready_workflow_and_is_idempotent(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_invoice_with_extraction(client)
    validation = client.post(f"/api/v1/invoices/{invoice_id}/validate")
    assert validation.status_code == 200

    first = client.post(ROUTE_URL.format(invoice_id))
    second = client.post(ROUTE_URL.format(invoice_id))

    assert first.status_code == second.status_code == 200
    assert first.json()["state"] == "READY"
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["workflow_id"] == first.json()["workflow_id"]
    with Session(engine) as session:
        assert (
            session.execute(
                select(func.count()).select_from(InvoiceWorkflow)
            ).scalar_one()
            == 1
        )
        assert (
            session.execute(
                select(func.count()).select_from(WorkflowEvent)
            ).scalar_one()
            == 1
        )


def test_route_review_required_creates_open_review_case(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(
        client, make_invoice(total_amount="105.00").model_dump(mode="json")
    )
    validation = client.post(f"/api/v1/invoices/{invoice_id}/validate")
    assert validation.json()["final_decision"] == "REVIEW_REQUIRED"

    response = client.post(ROUTE_URL.format(invoice_id))

    assert response.status_code == 200
    assert response.json()["state"] == "NEEDS_REVIEW"
    assert response.json()["review_case"]["status"] == "OPEN"
    assert response.json()["review_case"]["resolution"] is None


def test_route_warning_maps_to_ready_without_review_case(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(
        client,
        make_invoice(
            summary_tax=True, total_amount="104.00", line_total="90.00"
        ).model_dump(mode="json"),
    )
    validation = client.post(f"/api/v1/invoices/{invoice_id}/validate")
    assert validation.json()["final_decision"] == "WARNING"

    response = client.post(ROUTE_URL.format(invoice_id))

    assert response.status_code == 200
    assert response.json()["state"] == "READY"
    assert response.json()["review_case"] is None


def test_route_blocked_maps_to_blocked_without_review_case(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_invoice_with_extraction(
        client, make_invoice(total_amount="105.00").model_dump(mode="json")
    )
    validation = client.post(f"/api/v1/invoices/{invoice_id}/validate")
    with Session(engine) as session:
        run = session.get(InvoiceValidation, UUID(validation.json()["validation_id"]))
        assert run is not None
        run.final_decision = "BLOCKED"
        session.commit()

    response = client.post(ROUTE_URL.format(invoice_id))

    assert response.status_code == 200
    assert response.json()["state"] == "BLOCKED"
    assert response.json()["review_case"] is None


def test_get_returns_current_workflow_and_review_summary(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(
        client, make_invoice(total_amount="105.00").model_dump(mode="json")
    )
    assert client.post(f"/api/v1/invoices/{invoice_id}/validate").status_code == 200
    created = client.post(ROUTE_URL.format(invoice_id))
    read = client.get(READ_URL.format(invoice_id))

    assert read.status_code == 200
    assert read.json()["workflow_id"] == created.json()["workflow_id"]
    assert read.json()["review_case"]["status"] == "OPEN"
    assert read.json()["validation_decision"] == "REVIEW_REQUIRED"
    assert read.json()["ruleset_version"] == "invoice-validation-v1"


def test_unknown_invoice_maps_to_not_found(client: TestClient) -> None:
    invoice_id = uuid4()
    for response in (
        client.post(ROUTE_URL.format(invoice_id)),
        client.get(READ_URL.format(invoice_id)),
    ):
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "invoice_not_found"


def test_route_requires_structured_extraction(client: TestClient) -> None:
    _use_provider(client, FakeProvider(_valid_data()))
    invoice_id = _upload(client, _pdf_with_text([TEXT_LAYER]))
    assert client.post(f"/api/v1/invoices/{invoice_id}/extract-text").status_code == 200
    response = client.post(ROUTE_URL.format(invoice_id))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "structured_extraction_required"


def test_route_without_validation_maps_to_conflict(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(client)
    response = client.post(ROUTE_URL.format(invoice_id))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "current_validation_required"


def test_get_without_validation_maps_to_conflict(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(client)
    response = client.get(READ_URL.format(invoice_id))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "current_validation_required"


def test_current_validation_without_workflow_maps_to_not_found(
    client: TestClient,
) -> None:
    invoice_id = _seed_invoice_with_extraction(client)
    assert client.post(f"/api/v1/invoices/{invoice_id}/validate").status_code == 200
    response = client.get(READ_URL.format(invoice_id))
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workflow_not_found"


def test_stale_workflow_is_not_returned_for_newer_extraction(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_invoice_with_extraction(client, _valid_data())
    assert client.post(f"/api/v1/invoices/{invoice_id}/validate").status_code == 200
    first = client.post(ROUTE_URL.format(invoice_id))
    assert first.status_code == 200

    with Session(engine) as session:
        old = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == invoice_id
            )
        ).scalar_one()
        session.add(
            InvoiceStructuredExtraction(
                invoice_id=invoice_id,
                text_extraction_id=old.text_extraction_id,
                provider="fake",
                model="fake-model",
                prompt_version="invoice-extraction-v2",
                schema_version="invoice-schema-v1",
                status="success",
                structured_data=_valid_data(),
                created_at=old.created_at + timedelta(seconds=1),
            )
        )
        session.commit()

    read = client.get(READ_URL.format(invoice_id))
    route = client.post(ROUTE_URL.format(invoice_id))
    assert read.status_code == route.status_code == 409
    assert (
        read.json()["detail"]["code"]
        == route.json()["detail"]["code"]
        == "current_validation_required"
    )
    assert route.json()["detail"]["code"] != "workflow_not_found"


def _seed_review_workflow(client: TestClient) -> UUID:
    invoice_id = _seed_invoice_with_extraction(
        client, make_invoice(total_amount="105.00").model_dump(mode="json")
    )
    validation = client.post(f"/api/v1/invoices/{invoice_id}/validate")
    assert validation.status_code == 200
    assert validation.json()["final_decision"] == "REVIEW_REQUIRED"
    routed = client.post(ROUTE_URL.format(invoice_id))
    assert routed.status_code == 200
    assert routed.json()["state"] == "NEEDS_REVIEW"
    return invoice_id


def test_review_api_accept_exception_completes_lifecycle(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_review_workflow(client)
    request_id = uuid4()

    response = client.post(
        f"/api/v1/invoices/{invoice_id}/workflow/review",
        json={
            "decision": "ACCEPT_EXCEPTION",
            "resolution_request_id": str(request_id),
            "note": "Approved after review.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "EXCEPTION_ACCEPTED"
    assert payload["state_version"] == 2
    assert payload["validation_decision"] == "REVIEW_REQUIRED"
    assert payload["review_case"]["status"] == "RESOLVED"
    assert payload["review_case"]["resolution"] == "ACCEPT_EXCEPTION"
    assert payload["review_case"]["resolution_request_id"] == str(request_id)
    assert payload["review_case"]["resolved_at"] is not None
    with Session(engine) as session:
        validation = session.execute(select(InvoiceValidation)).scalar_one()
        assert validation.final_decision == "REVIEW_REQUIRED"
    assert client.get(READ_URL.format(invoice_id)).json()["state"] == (
        "EXCEPTION_ACCEPTED"
    )


def test_review_api_reject_completes_lifecycle(client: TestClient) -> None:
    invoice_id = _seed_review_workflow(client)
    response = client.post(
        f"/api/v1/invoices/{invoice_id}/workflow/review",
        json={
            "decision": "REJECT",
            "resolution_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 200
    assert response.json()["state"] == "REJECTED"
    assert response.json()["review_case"]["resolution"] == "REJECT"


def test_review_api_invalid_decision_is_unprocessable(client: TestClient) -> None:
    invoice_id = _seed_review_workflow(client)
    response = client.post(
        f"/api/v1/invoices/{invoice_id}/workflow/review",
        json={"decision": "PASS", "resolution_request_id": str(uuid4())},
    )
    assert response.status_code == 422


def test_review_api_idempotency_and_conflict(client: TestClient) -> None:
    invoice_id = _seed_review_workflow(client)
    request_id = str(uuid4())
    url = f"/api/v1/invoices/{invoice_id}/workflow/review"
    body = {
        "decision": "ACCEPT_EXCEPTION",
        "resolution_request_id": request_id,
    }

    first = client.post(url, json=body)
    retry = client.post(url, json=body)
    conflicting_request = client.post(
        url,
        json={"decision": "REJECT", "resolution_request_id": request_id},
    )
    different_request = client.post(
        url,
        json={"decision": "REJECT", "resolution_request_id": str(uuid4())},
    )

    assert first.status_code == retry.status_code == 200
    assert retry.json() == first.json()
    assert conflicting_request.status_code == 409
    assert conflicting_request.json()["detail"]["code"] == "resolution_request_conflict"
    assert different_request.status_code == 409
    assert different_request.json()["detail"]["code"] == "review_already_resolved"


def test_review_api_missing_workflow_maps_to_not_found(client: TestClient) -> None:
    invoice_id = _seed_invoice_with_extraction(
        client, make_invoice(total_amount="105.00").model_dump(mode="json")
    )
    assert client.post(f"/api/v1/invoices/{invoice_id}/validate").status_code == 200
    response = client.post(
        f"/api/v1/invoices/{invoice_id}/workflow/review",
        json={
            "decision": "ACCEPT_EXCEPTION",
            "resolution_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workflow_not_found"


def test_review_api_non_reviewable_workflow_maps_to_conflict(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_invoice_with_extraction(client)
    validation = client.post(f"/api/v1/invoices/{invoice_id}/validate")
    assert validation.status_code == 200
    assert client.post(ROUTE_URL.format(invoice_id)).status_code == 200
    response = client.post(
        f"/api/v1/invoices/{invoice_id}/workflow/review",
        json={
            "decision": "ACCEPT_EXCEPTION",
            "resolution_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 409


def test_review_api_stale_workflow_maps_to_conflict(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_review_workflow(client)
    with Session(engine) as session:
        old = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == invoice_id
            )
        ).scalar_one()
        session.add(
            InvoiceStructuredExtraction(
                invoice_id=invoice_id,
                text_extraction_id=old.text_extraction_id,
                provider="fake",
                model="fake-model",
                prompt_version="invoice-extraction-v2",
                schema_version="invoice-schema-v1",
                status="success",
                structured_data=_valid_data(),
                created_at=old.created_at + timedelta(seconds=1),
            )
        )
        session.commit()

    response = client.post(
        f"/api/v1/invoices/{invoice_id}/workflow/review",
        json={
            "decision": "ACCEPT_EXCEPTION",
            "resolution_request_id": str(uuid4()),
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "current_validation_required"
