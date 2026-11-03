from types import SimpleNamespace
from uuid import uuid4

from app.core.config import Settings
from app.scripts.live_e2e import _deliver_review_required


def test_reject_scenario_initial_route_uses_review_required_delivery(monkeypatch):
    captured = []

    def fake_delivery(result, invoice_id, filename, validation_id, settings):
        captured.append(
            (result.event_id, invoice_id, filename, validation_id, settings)
        )
        return SimpleNamespace(delivered=True, reason=None)

    monkeypatch.setattr("app.scripts.live_e2e.deliver_workflow_event", fake_delivery)
    result = SimpleNamespace(
        state="NEEDS_REVIEW",
        event_created=True,
        event_id=uuid4(),
        validation_id=uuid4(),
    )
    invoice_id = uuid4()
    settings = Settings(n8n_enabled=True)

    delivery = _deliver_review_required(result, invoice_id, "reject.pdf", settings)

    assert delivery.delivered is True
    assert captured == [
        (result.event_id, invoice_id, "reject.pdf", result.validation_id, settings)
    ]
