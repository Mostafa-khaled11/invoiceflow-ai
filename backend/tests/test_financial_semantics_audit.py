"""Offline tests for the financial-semantics audit tooling."""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path

from evaluation.audit_financial_semantics import (
    audit_dataset,
    audit_invoice,
    money,
)

DATASET = Path(__file__).parents[2] / "evaluation"


def test_dataset_financial_audit_is_complete_and_clean() -> None:
    report = audit_dataset(DATASET)
    summary = report["summary"]

    assert summary["invoices_checked"] == 60
    assert summary["line_items_checked"] == 216
    assert summary["financial_inconsistencies_found"] == 0
    assert summary["rounding_mismatches_found"] == 0
    assert summary["null_zero_anomalies_found"] == 0
    assert summary["quantity_min"] == "1.00"
    assert summary["quantity_max"] == "3.00"


def test_audit_results_are_deterministic() -> None:
    assert audit_dataset(DATASET) == audit_dataset(DATASET)


def test_decimal_rounding_is_explicit_half_up() -> None:
    assert money(Decimal("1.005")) == Decimal("1.01")
    assert money(Decimal("1.004")) == Decimal("1.00")


def test_null_and_zero_semantics_are_observed() -> None:
    summary = audit_dataset(DATASET)["summary"]

    assert summary["null_counts"]["discount_total"] == 36
    assert summary["null_counts"]["shipping_total"] == 42
    assert summary["null_counts"]["line_items.tax"] == 58
    assert summary["explicit_zero_counts"] == {}


def test_audit_detects_corrupted_line_total(tmp_path: Path) -> None:
    source = DATASET / "generated" / "development" / "invoice_001"
    target = tmp_path / "invoice_001"
    target.mkdir()
    for filename in ("metadata.json", "ground_truth.json"):
        (target / filename).write_bytes((source / filename).read_bytes())
    truth = json.loads((target / "ground_truth.json").read_text(encoding="utf-8"))
    truth["line_items"][0]["line_total"] = "0.00"
    (target / "ground_truth.json").write_text(json.dumps(truth), encoding="utf-8")

    report = audit_invoice(target)

    assert any(
        issue["field"] == "line_items[1].line_total" for issue in report["issues"]
    )


def test_audit_has_no_provider_or_production_behavior() -> None:
    source = inspect.getsource(
        __import__("evaluation.audit_financial_semantics", fromlist=["audit_dataset"])
    )

    assert "groq" not in source.lower()
    assert "httpx" not in source.lower()
    assert "InvoiceTextExtractor" not in source
