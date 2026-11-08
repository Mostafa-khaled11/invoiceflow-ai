"""Canonical identity and fingerprint helpers for review commands."""

import hashlib
import json
from uuid import UUID

from app.models.invoice_workflow import ReviewResolution

FINGERPRINT_VERSION = "review-resolution-v1"
SYSTEM_ACTOR_SCOPE_PREFIX = "system:"


def actor_scope_key(actor_type: str, actor_id: str | None) -> str:
    """Return the canonical actor-bound idempotency scope."""

    if actor_type in {"human", "service"} and actor_id:
        return f"{actor_type}:{actor_id}"
    if actor_type == "system" and actor_id:
        return f"{SYSTEM_ACTOR_SCOPE_PREFIX}{actor_id}"
    raise ValueError("A valid trusted actor is required for command idempotency")


def review_resolution_fingerprint(
    *,
    invoice_id: UUID,
    workflow_id: UUID,
    review_case_id: UUID,
    decision: ReviewResolution,
    note: str | None,
) -> str:
    """Hash the versioned, exact review-resolution command payload."""

    payload = {
        "operation": "review.resolve",
        "invoice_id": str(invoice_id),
        "workflow_id": str(workflow_id),
        "review_case_id": str(review_case_id),
        "decision": decision.value,
        "note": note,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
