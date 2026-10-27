"""Offline tests for PDF extraction strategy comparison."""

from __future__ import annotations

from pathlib import Path

from evaluation.compare_pdf_extraction import (
    _context_score,
    _line_item_scores,
    _multi_page_score,
    _table_header_score,
    compare_invoice,
    extract_strategy_a,
    extract_strategy_b,
    extract_strategy_c,
)
from evaluation.diagnose_pdf_text import extract_production_text

DATASET = Path(__file__).parents[2] / "evaluation"


def test_strategy_a_remains_exactly_production_extraction() -> None:
    pdf = DATASET / "generated" / "development" / "invoice_011" / "invoice.pdf"
    strategy = extract_strategy_a(pdf)
    production = extract_production_text(pdf)
    assert strategy.text == production.text_content
    assert strategy.page_count == production.page_count


def test_all_strategies_use_the_same_source_pdf() -> None:
    pdf = DATASET / "generated" / "development" / "invoice_012" / "invoice.pdf"
    outputs = [
        extract_strategy_a(pdf),
        extract_strategy_b(pdf),
        extract_strategy_c(pdf),
    ]
    assert {output.page_count for output in outputs} == {1}
    assert {output.name for output in outputs} == {
        "A-production-default",
        "B-pypdf-layout",
        "C-coordinate-aware",
    }


def test_invoice_number_context_scoring() -> None:
    assert (
        _context_score(["Invoice Number: AC-123"], "Invoice Number", "AC-123")["score"]
        == 1.0
    )
    assert _context_score(["AC-123"], "Invoice Number", "AC-123")["score"] == 1 / 3


def test_date_label_context_scoring() -> None:
    score = _context_score(["Invoice Date: 14/03/2026"], "Invoice Date", "14/03/2026")
    assert score["label_value_associated"] is True
    assert score["score"] == 1.0


def test_table_row_and_column_scoring() -> None:
    lines = [
        "Description | Quantity | Unit Price | Discount | Line Total",
        "Cloud Hosting | 2.00 | $100.00 | $5.00 | $195.00",
    ]
    assert _table_header_score(lines) == 1.0
    row_score, financial_score = _line_item_scores(
        lines,
        [
            {
                "description": "Cloud Hosting",
                "quantity": "2.00",
                "unit_price": "100.00",
                "discount": "5.00",
                "tax": None,
                "line_total": "195.00",
            }
        ],
    )
    assert row_score == 1.0
    assert financial_score == 1.0


def test_multi_page_scoring() -> None:
    assert (
        _multi_page_score(
            "page one\fcontinued\nDescription\nDescription", {"multi_page": True}
        )
        == 1.0
    )
    assert _multi_page_score("single page", {"multi_page": False}) == 1.0


def test_comparison_contains_visible_component_metrics_without_groq_or_ocr() -> None:
    comparison = compare_invoice(DATASET, "invoice_036")
    assert len(comparison["strategies"]) == 3
    required = {
        "invoice_number_context_score",
        "date_context_score",
        "table_header_score",
        "line_item_row_score",
        "financial_column_association_score",
        "multi_page_structure_score",
        "text_completeness_score",
    }
    for strategy in comparison["strategies"]:
        assert required <= strategy["metrics"].keys()
        assert "groq" not in strategy["description"].lower()
        assert "ocr" not in strategy["description"].lower()
