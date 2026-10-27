"""Offline tests for PDF text-fidelity diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.diagnose_pdf_text import (
    diagnose_dataset,
    diagnose_invoice,
    extract_production_text,
)

DATASET = Path(__file__).parents[2] / "evaluation"


def test_diagnostic_uses_production_extractor_output() -> None:
    pdf = DATASET / "generated" / "development" / "invoice_011" / "invoice.pdf"
    production = extract_production_text(pdf)
    diagnosis = diagnose_invoice(DATASET, "invoice_011")
    assert diagnosis["extracted_text"] == production.text_content
    assert diagnosis["page_count"] == production.page_count
    assert diagnosis["extractor"] == "pypdf"
    assert diagnosis["extractor_version"] == production.extractor_version


def test_diagnostic_reports_invoice_number_and_table_fidelity() -> None:
    diagnosis = diagnose_invoice(DATASET, "invoice_011")
    assert "Invoice Number: AC-8343" not in diagnosis["missing_canonical_lines"]
    assert any(
        "Invoice Number: AC-8343" in context
        for context in diagnosis["invoice_number_context"]["extracted"]
    )
    assert diagnosis["line_item_table_context"]["canonical"]
    assert diagnosis["line_item_table_context"]["extracted"]
    extracted_table = diagnosis["line_item_table_context"]["extracted"]
    assert all(value in extracted_table for value in ("$329.00", "$98.70", "$756.70"))


def test_invoice_036_page_boundary_is_reported() -> None:
    diagnosis = diagnose_invoice(DATASET, "invoice_036")
    assert diagnosis["page_count"] == 2
    assert diagnosis["page_boundaries"]["form_feed_count"] == 1
    assert diagnosis["page_boundaries"]["pages"][1]["first_lines"][0].startswith(
        "AC-8668"
    )
    assert diagnosis["line_item_table_context"]["extracted"].count("Description") == 2


def test_diagnostic_writes_machine_and_human_readable_artifacts(tmp_path: Path) -> None:
    report = diagnose_dataset(
        DATASET,
        ["invoice_011", "invoice_012", "invoice_036"],
        tmp_path / "pdf_text",
    )
    assert report == tmp_path / "pdf_text" / "report.md"
    assert report.is_file()
    for invoice_id in ("invoice_011", "invoice_012", "invoice_036"):
        directory = tmp_path / "pdf_text" / invoice_id
        assert (directory / "canonical_invoice.txt").is_file()
        assert (directory / "pypdf_extracted.txt").is_file()
        assert (directory / "unified.diff").is_file()
        diagnostics = json.loads(
            (directory / "diagnostics.json").read_text(encoding="utf-8")
        )
        assert diagnostics["invoice_id"] == invoice_id
        assert "canonical_text" not in diagnostics
        assert "extracted_text" not in diagnostics
