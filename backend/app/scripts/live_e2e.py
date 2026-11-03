"""Safe local FastAPI-to-n8n verification harness.

Dry-run is the default. ``--execute`` creates synthetic records and never
deletes or modifies records outside the current test run.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_engine
from app.models.invoice import Invoice, InvoiceStatus
from app.models.invoice_structured_extraction import InvoiceStructuredExtraction
from app.models.invoice_text_extraction import InvoiceTextExtraction
from app.models.invoice_workflow import ReviewResolution, WorkflowEvent
from app.services.integrations.n8n import deliver_workflow_event
from app.services.invoice_validation import validate_and_persist_invoice
from app.services.invoice_workflow import (
    ReviewResolutionError,
    resolve_invoice_review,
    route_invoice_workflow,
)


def _data() -> dict[str, object]:
    """Return a valid extraction with one deterministic review finding."""

    return {
        "vendor_name": "InvoiceFlow E2E Fixture",
        "vendor_tax_id": None,
        "invoice_number": f"E2E-{uuid4().hex[:12]}",
        "invoice_date": "2026-01-01",
        "due_date": "2026-01-31",
        "purchase_order_number": None,
        "currency": "USD",
        "subtotal": "100.00",
        "discount_total": "10.00",
        "tax_total": "9.00",
        "shipping_total": "5.00",
        "total_amount": "104.00",
        "line_items": [
            {
                "description": "E2E service",
                "quantity": "2.00",
                "unit_price": "50.00",
                "discount": "10.00",
                "tax": "9.00",
                "line_total": "100.00",
            }
        ],
        "warnings": [],
    }


def _create_review_scenario(session: Session, run_id: str) -> UUID:
    invoice_id = uuid4()
    text_id = uuid4()
    invoice = Invoice(
        id=invoice_id,
        original_filename=f"invoiceflow-e2e-{run_id}.pdf",
        storage_key=f"e2e-{invoice_id}.pdf",
        content_type="application/pdf",
        size_bytes=1,
        sha256=uuid4().hex + uuid4().hex,
        status=InvoiceStatus.STRUCTURED_EXTRACTED.value,
    )
    session.add(invoice)
    session.add(
        InvoiceTextExtraction(
            id=text_id,
            invoice_id=invoice_id,
            extractor="e2e-fixture",
            extractor_version="1",
            text_content="InvoiceFlow deterministic E2E fixture",
            page_count=1,
            pages_with_text=1,
            character_count=39,
            non_whitespace_character_count=34,
            status="text_extracted",
            processing_ms=0,
        )
    )
    session.add(
        InvoiceStructuredExtraction(
            invoice_id=invoice_id,
            text_extraction_id=text_id,
            provider="deterministic-e2e-fixture",
            model="none",
            prompt_version="fixture-v1",
            schema_version="invoice-schema-v1",
            status="success",
            structured_data=_data(),
            attempt_count=0,
        )
    )
    session.commit()
    validation, _ = validate_and_persist_invoice(session, invoice_id)
    if validation.final_decision != "REVIEW_REQUIRED":
        raise RuntimeError("E2E fixture did not produce REVIEW_REQUIRED")
    return invoice_id


def _event_count(session: Session, workflow_id: UUID) -> int:
    return session.execute(
        select(func.count(WorkflowEvent.id)).where(
            WorkflowEvent.workflow_id == workflow_id
        )
    ).scalar_one()


def _deliver_review_required(result, invoice_id, filename, settings):
    """Deliver the initial NEEDS_REVIEW event for any live scenario."""

    if result.state != "NEEDS_REVIEW" or not result.event_created:
        raise RuntimeError("review-required delivery requires a new NEEDS_REVIEW event")
    return deliver_workflow_event(
        result, invoice_id, filename, result.validation_id, settings
    )


def _record(result, invoice_id: UUID, run_id: str, scenario: str) -> dict[str, object]:
    return {
        "scenario": scenario,
        "test_run_id": run_id,
        "invoice_id": str(invoice_id),
        "workflow_id": str(result.workflow_id),
        "review_case_id": (
            None
            if result.review_case is None
            else str(result.review_case.review_case_id)
        ),
        "event_id": None if result.event_id is None else str(result.event_id),
        "state": result.state,
        "state_version": result.state_version,
        "event_created": result.event_created,
        "webhook_status": "NOT_VERIFIED",
    }


def execute(settings: Settings, output: Path) -> int:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    engine = get_engine(settings)
    evidence: dict[str, object] = {"test_run_id": run_id, "scenarios": []}
    with Session(engine) as session:
        first_invoice = _create_review_scenario(session, run_id + "-accept")
        first = route_invoice_workflow(session, first_invoice)
        invoice = session.get(Invoice, first_invoice)
        if invoice is None:
            raise RuntimeError("fixture invoice disappeared")
        delivery = _deliver_review_required(
            first, first_invoice, invoice.original_filename, settings
        )
        first_record = _record(first, first_invoice, run_id, "review_required")
        first_record["webhook_status"] = (
            "WEBHOOK_ACCEPTED" if delivery.delivered else delivery.reason
        )
        cast = evidence["scenarios"]
        assert isinstance(cast, list)
        cast.append(first_record)

        request_id = uuid4()
        accepted = resolve_invoice_review(
            session, first_invoice, ReviewResolution.ACCEPT_EXCEPTION, str(request_id)
        )
        delivery = deliver_workflow_event(
            accepted,
            first_invoice,
            invoice.original_filename,
            accepted.validation_id,
            settings,
        )
        accepted_record = _record(accepted, first_invoice, run_id, "accept_exception")
        accepted_record["webhook_status"] = (
            "WEBHOOK_ACCEPTED" if delivery.delivered else delivery.reason
        )
        cast.append(accepted_record)

        before = _event_count(session, accepted.workflow_id)
        replay = resolve_invoice_review(
            session, first_invoice, ReviewResolution.ACCEPT_EXCEPTION, str(request_id)
        )
        after = _event_count(session, replay.workflow_id)
        cast.append(
            {
                "scenario": "idempotency",
                "test_run_id": run_id,
                "workflow_id": str(replay.workflow_id),
                "event_count_before": before,
                "event_count_after": after,
                "duplicate_event": before != after,
                "webhook_status": "N/A",
            }
        )

        second_invoice = _create_review_scenario(session, run_id + "-reject")
        routed_reject = route_invoice_workflow(session, second_invoice)
        second = session.get(Invoice, second_invoice)
        if second is None:
            raise RuntimeError("fixture invoice disappeared")
        delivery = _deliver_review_required(
            routed_reject, second_invoice, second.original_filename, settings
        )
        reject_route_record = _record(
            routed_reject, second_invoice, run_id, "review_required_reject"
        )
        reject_route_record["webhook_status"] = (
            "WEBHOOK_ACCEPTED" if delivery.delivered else delivery.reason
        )
        cast.append(reject_route_record)
        rejected = resolve_invoice_review(
            session, second_invoice, ReviewResolution.REJECT, str(uuid4())
        )
        delivery = deliver_workflow_event(
            rejected,
            second_invoice,
            second.original_filename,
            rejected.validation_id,
            settings,
        )
        rejected_record = _record(rejected, second_invoice, run_id, "reject")
        rejected_record["webhook_status"] = (
            "WEBHOOK_ACCEPTED" if delivery.delivered else delivery.reason
        )
        cast.append(rejected_record)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--output", type=Path, default=Path(".runtime/live-e2e/evidence.json")
    )
    args = parser.parse_args()
    if not args.execute:
        print("DRY RUN: no database writes or webhook calls. Re-run with --execute.")
        return 0
    try:
        return execute(Settings(), args.output)
    except ReviewResolutionError as exc:
        print(f"E2E FAILED: {exc.error_code}: {exc.message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
