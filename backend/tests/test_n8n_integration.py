import json
from datetime import UTC, datetime
from uuid import uuid4

from app.core.config import Settings
from app.services.integrations import n8n
from app.services.invoice_workflow import (
    WorkflowReviewCaseResult,
    WorkflowRoutingResult,
)


def _result(
    *, state: str = "NEEDS_REVIEW", event_created: bool = True
) -> WorkflowRoutingResult:
    return WorkflowRoutingResult(
        workflow_id=uuid4(),
        validation_id=uuid4(),
        state=state,
        state_version=1,
        validation_decision="REVIEW_REQUIRED",
        ruleset_version="invoice-validation-v1",
        review_case=WorkflowReviewCaseResult(
            review_case_id=uuid4(),
            status="OPEN" if state == "NEEDS_REVIEW" else "RESOLVED",
            resolution=None if state == "NEEDS_REVIEW" else state,
            resolved_at=None,
            resolution_request_id=None,
        ),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        created=event_created,
        event_id=uuid4() if event_created else None,
        event_created=event_created,
    )


def test_disabled_delivery_does_not_call_network(monkeypatch):
    monkeypatch.setattr(
        n8n,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )
    result = n8n.deliver_workflow_event(
        _result(), uuid4(), "invoice.pdf", uuid4(), Settings(n8n_enabled=False)
    )
    assert result.reason == "disabled"


def test_review_required_payload_is_versioned_and_safe(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(n8n, "urlopen", fake_urlopen)
    invoice_id = uuid4()
    source = _result()
    result = n8n.deliver_workflow_event(
        source,
        invoice_id,
        "invoice.pdf",
        uuid4(),
        Settings(
            n8n_enabled=True,
            n8n_base_url="http://n8n:5678",
            n8n_webhook_secret="local-secret",
        ),
    )
    payload = json.loads(captured["request"].data)
    assert result.delivered is True
    assert payload["event_type"] == "workflow.review_required"
    assert payload["event_version"] == "1"
    assert payload["event_id"] == str(source.event_id)
    assert "raw_model_output" not in json.dumps(payload)
    assert (
        captured["request"].get_header("X-invoiceflow-webhook-secret") == "local-secret"
    )


def test_duplicate_result_is_not_delivered(monkeypatch):
    monkeypatch.setattr(
        n8n,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError()),
    )
    result = n8n.deliver_workflow_event(
        _result(event_created=False),
        uuid4(),
        "invoice.pdf",
        uuid4(),
        Settings(n8n_enabled=True),
    )
    assert result.reason == "duplicate_or_no_event"
