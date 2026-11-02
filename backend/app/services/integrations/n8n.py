"""Best-effort, post-commit delivery to local n8n webhooks."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

from app.core.config import Settings
from app.services.invoice_workflow import WorkflowRoutingResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class N8nDeliveryResult:
    delivered: bool
    status_code: int | None = None
    reason: str | None = None


def deliver_workflow_event(
    result: WorkflowRoutingResult,
    invoice_id: UUID,
    filename: str,
    validation_id: UUID,
    settings: Settings,
) -> N8nDeliveryResult:
    """Deliver one newly-created event without affecting business state."""

    if not settings.n8n_enabled:
        return N8nDeliveryResult(False, reason="disabled")
    if not result.event_created or result.event_id is None:
        return N8nDeliveryResult(False, reason="duplicate_or_no_event")
    if not settings.n8n_base_url or not settings.n8n_webhook_secret:
        return N8nDeliveryResult(False, reason="not_configured")

    event_type = "workflow.review_required"
    path = "/webhook/invoiceflow/review-required"
    if result.state in ("EXCEPTION_ACCEPTED", "REJECTED"):
        event_type = "workflow.review_resolved"
        path = "/webhook/invoiceflow/review-resolved"
    review_case = result.review_case
    payload = {
        "event_id": str(result.event_id),
        "event_type": event_type,
        "event_version": "1",
        "occurred_at": datetime.now().astimezone().isoformat(),
        "invoice": {"id": str(invoice_id), "filename": filename},
        "workflow": {
            "id": str(result.workflow_id),
            "state": result.state,
            "state_version": result.state_version,
        },
        "validation": {
            "id": str(validation_id),
            "decision": result.validation_decision,
            "ruleset_version": result.ruleset_version,
        },
        "review_case": (
            None
            if review_case is None
            else {
                "id": str(review_case.review_case_id),
                "status": review_case.status,
                "resolution": review_case.resolution,
            }
        ),
    }
    request = Request(
        f"{settings.n8n_base_url.rstrip('/')}{path}",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "X-InvoiceFlow-Webhook-Secret": settings.n8n_webhook_secret,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.n8n_timeout_seconds) as response:
            status_code = response.status
        delivered = 200 <= status_code < 300
        _log(result, invoice_id, event_type, delivered, status_code)
        return N8nDeliveryResult(delivered, status_code)
    except HTTPError as exc:
        _log(result, invoice_id, event_type, False, exc.code)
        return N8nDeliveryResult(False, exc.code, "http_error")
    except (TimeoutError, URLError, OSError) as exc:
        logger.warning(
            "n8n delivery failed event_id=%s event_type=%s workflow_id=%s "
            "invoice_id=%s reason=%s",
            result.event_id,
            event_type,
            result.workflow_id,
            invoice_id,
            type(exc).__name__,
        )
        return N8nDeliveryResult(False, reason=type(exc).__name__)


def _log(
    result: WorkflowRoutingResult,
    invoice_id: UUID,
    event_type: str,
    delivered: bool,
    status_code: int,
) -> None:
    logger.info(
        "n8n delivery event_id=%s event_type=%s workflow_id=%s invoice_id=%s "
        "delivered=%s status_code=%s",
        result.event_id,
        event_type,
        result.workflow_id,
        invoice_id,
        delivered,
        status_code,
    )
