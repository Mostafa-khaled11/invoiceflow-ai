"""Offline API tests for the initial invoice workflow endpoints."""

from datetime import timedelta
from uuid import UUID, uuid4

from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_validation import InvoiceValidation
from app.models.invoice_workflow import (
    InvoiceWorkflow,
    WorkflowEvent,
    WorkflowState,
)
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


def _seed_routed_workflow(
    client: TestClient,
    suffix: str,
    data: dict[str, object],
) -> UUID:
    _use_provider(
        client,
        FakeProvider(data),
    )
    invoice_id = _upload(client, _pdf_with_text([TEXT_LAYER + suffix]))
    assert client.post(f"/api/v1/invoices/{invoice_id}/extract-text").status_code == 200
    structured = client.post(f"/api/v1/invoices/{invoice_id}/extract-structured")
    assert structured.status_code == 200
    validation = client.post(f"/api/v1/invoices/{invoice_id}/validate")
    assert validation.status_code == 200
    routed = client.post(ROUTE_URL.format(invoice_id))
    assert routed.status_code == 200
    return invoice_id


def _seed_review_workflow(client: TestClient, suffix: str = "") -> UUID:
    invoice_id = _seed_routed_workflow(
        client,
        suffix,
        make_invoice(total_amount="105.00").model_dump(mode="json"),
    )
    assert client.get(READ_URL.format(invoice_id)).json()["state"] == "NEEDS_REVIEW"
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
        resolution_event = (
            session.execute(
                select(WorkflowEvent).order_by(WorkflowEvent.sequence_number.desc())
            )
            .scalars()
            .first()
        )
        assert resolution_event is not None
        assert resolution_event.actor_type == "human"
        assert resolution_event.actor_id is not None
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


def test_review_api_same_key_with_different_note_conflicts(client: TestClient) -> None:
    invoice_id = _seed_review_workflow(client)
    request_id = str(uuid4())
    url = f"/api/v1/invoices/{invoice_id}/workflow/review"
    first = client.post(
        url,
        json={
            "decision": "REJECT",
            "resolution_request_id": request_id,
            "note": "Original decision note",
        },
    )
    replay = client.post(
        url,
        json={
            "decision": "REJECT",
            "resolution_request_id": request_id,
            "note": "Substituted decision note",
        },
    )

    assert first.status_code == 200
    assert replay.status_code == 409
    assert replay.json()["detail"]["code"] == "resolution_request_conflict"


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


def test_review_queue_defaults_to_current_actionable_cases(
    client: TestClient, engine: Engine
) -> None:
    current_id = _seed_review_workflow(client, "queue-current")
    ready_id = _seed_routed_workflow(client, "queue-ready", _valid_data())
    blocked_id = _seed_review_workflow(client, "queue-blocked")
    accepted_id = _seed_review_workflow(client, "queue-accepted")
    rejected_id = _seed_review_workflow(client, "queue-rejected")
    stale_id = _seed_review_workflow(client, "queue-stale")
    failed_only_id = _seed_review_workflow(client, "queue-failed-only")

    client.post(
        f"/api/v1/invoices/{accepted_id}/workflow/review",
        json={"decision": "ACCEPT_EXCEPTION", "resolution_request_id": str(uuid4())},
    )
    client.post(
        f"/api/v1/invoices/{rejected_id}/workflow/review",
        json={"decision": "REJECT", "resolution_request_id": str(uuid4())},
    )
    with Session(engine) as session:
        blocked_workflow = session.execute(
            select(InvoiceWorkflow).where(
                InvoiceWorkflow.validation_id
                == session.execute(
                    select(InvoiceValidation.id).where(
                        InvoiceValidation.invoice_id == blocked_id
                    )
                ).scalar_one()
            )
        ).scalar_one()
        blocked_workflow.state = WorkflowState.BLOCKED.value
        stale_extraction = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == stale_id
            )
        ).scalar_one()
        session.add(
            InvoiceStructuredExtraction(
                invoice_id=stale_id,
                text_extraction_id=stale_extraction.text_extraction_id,
                provider="fake",
                model="fake-model",
                prompt_version="invoice-extraction-v2",
                schema_version="invoice-schema-v1",
                status="success",
                structured_data=_valid_data(),
                created_at=stale_extraction.created_at + timedelta(seconds=1),
            )
        )
        failed_extraction = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == failed_only_id
            )
        ).scalar_one()
        session.add(
            InvoiceStructuredExtraction(
                invoice_id=failed_only_id,
                text_extraction_id=failed_extraction.text_extraction_id,
                provider="fake",
                model="fake-model",
                prompt_version="invoice-extraction-v3",
                schema_version="invoice-schema-v1",
                status="failed",
                structured_data=None,
                created_at=failed_extraction.created_at + timedelta(seconds=1),
            )
        )
        session.commit()

    response = client.get("/api/v1/review-cases")

    assert response.status_code == 200
    payload = response.json()
    returned_ids = {item["invoice_id"] for item in payload["items"]}
    assert returned_ids == {str(current_id), str(failed_only_id)}
    assert ready_id not in returned_ids
    assert str(blocked_id) not in returned_ids
    assert str(accepted_id) not in returned_ids
    assert str(rejected_id) not in returned_ids
    assert str(stale_id) not in returned_ids
    assert all(
        item["is_current"] and item["is_actionable"] for item in payload["items"]
    )


def test_review_queue_historical_filters_and_currentness(
    client: TestClient, engine: Engine
) -> None:
    stale_id = _seed_review_workflow(client, "history-stale")
    accepted_id = _seed_review_workflow(client, "history-accepted")
    mismatch_id = _seed_review_workflow(client, "history-mismatch")
    client.post(
        f"/api/v1/invoices/{accepted_id}/workflow/review",
        json={"decision": "ACCEPT_EXCEPTION", "resolution_request_id": str(uuid4())},
    )
    with Session(engine) as session:
        validation = session.execute(
            select(InvoiceValidation).where(InvoiceValidation.invoice_id == mismatch_id)
        ).scalar_one()
        validation.ruleset_version = "invoice-validation-old"
        stale_extraction = session.execute(
            select(InvoiceStructuredExtraction).where(
                InvoiceStructuredExtraction.invoice_id == stale_id
            )
        ).scalar_one()
        session.add(
            InvoiceStructuredExtraction(
                invoice_id=stale_id,
                text_extraction_id=stale_extraction.text_extraction_id,
                provider="fake",
                model="fake-model",
                prompt_version="invoice-extraction-v2",
                schema_version="invoice-schema-v1",
                status="success",
                structured_data=_valid_data(),
                created_at=stale_extraction.created_at + timedelta(seconds=1),
            )
        )
        session.commit()

    response = client.get(
        "/api/v1/review-cases",
        params={"include_historical": "true", "status": "RESOLVED"},
    )
    assert response.status_code == 200
    assert {item["invoice_id"] for item in response.json()["items"]} == {
        str(accepted_id)
    }
    resolution_filter = client.get(
        "/api/v1/review-cases",
        params={
            "include_historical": "true",
            "resolution": "ACCEPT_EXCEPTION",
        },
    )
    assert resolution_filter.status_code == 200
    assert {item["invoice_id"] for item in resolution_filter.json()["items"]} == {
        str(accepted_id)
    }
    state_filter = client.get(
        "/api/v1/review-cases",
        params={
            "include_historical": "true",
            "workflow_state": "NEEDS_REVIEW",
        },
    )
    assert state_filter.status_code == 200
    state_ids = {item["invoice_id"] for item in state_filter.json()["items"]}
    assert str(stale_id) in state_ids
    assert str(mismatch_id) in state_ids
    assert all(
        not item["is_current"] and not item["is_actionable"]
        for item in state_filter.json()["items"]
    )


def test_review_queue_pagination_ordering_and_total(client: TestClient) -> None:
    for index in range(3):
        _seed_review_workflow(client, f"pagination-{index}")

    all_cases = client.get("/api/v1/review-cases", params={"limit": 100})
    first_page = client.get("/api/v1/review-cases", params={"limit": 2, "offset": 0})
    second_page = client.get("/api/v1/review-cases", params={"limit": 2, "offset": 2})

    assert (
        all_cases.status_code
        == first_page.status_code
        == second_page.status_code
        == 200
    )
    all_ids = [item["review_case_id"] for item in all_cases.json()["items"]]
    assert all_cases.json()["total"] == 3
    assert first_page.json()["total"] == 3
    assert [item["review_case_id"] for item in first_page.json()["items"]] == all_ids[
        :2
    ]
    assert [item["review_case_id"] for item in second_page.json()["items"]] == all_ids[
        2:
    ]


def test_review_queue_invalid_filters_return_422(client: TestClient) -> None:
    response = client.get("/api/v1/review-cases", params={"limit": 101})
    assert response.status_code == 422


def test_review_case_detail_includes_context_and_reason_summary(
    client: TestClient,
) -> None:
    invoice_id = _seed_review_workflow(client, "detail-current")
    queue = client.get("/api/v1/review-cases", params={"invoice_id": invoice_id})
    case_id = queue.json()["items"][0]["review_case_id"]

    response = client.get(f"/api/v1/review-cases/{case_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["invoice_id"] == str(invoice_id)
    assert payload["is_current"] is True
    assert payload["is_actionable"] is True
    assert payload["invoice_filename"]
    assert payload["validation_summary"]["decision"] == "REVIEW_REQUIRED"
    assert payload["validation_summary"]["finding_count"] > 0
    assert "FIN005" in payload["validation_summary"]["failed_rule_codes"]


def test_review_case_detail_historical_case_is_readable(
    client: TestClient, engine: Engine
) -> None:
    invoice_id = _seed_review_workflow(client, "detail-historical")
    queue = client.get("/api/v1/review-cases", params={"invoice_id": invoice_id})
    case_id = queue.json()["items"][0]["review_case_id"]
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

    response = client.get(f"/api/v1/review-cases/{case_id}")

    assert response.status_code == 200
    assert response.json()["is_current"] is False
    assert response.json()["is_actionable"] is False


def test_unknown_review_case_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/review-cases/{uuid4()}")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "review_case_not_found"
