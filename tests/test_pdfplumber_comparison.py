from __future__ import annotations

import inspect
import json
from pathlib import Path

from evaluation.compare_pdf_extraction import extract_strategy_a
from evaluation.diagnose_pdf_text import extract_production_text
from evaluation.compare_pdfplumber import (
    DEFAULT_OUTPUT_DIR,
    _contains_near,
    _extract_coordinate_aware,
    _extract_pdfplumber,
    compare_dataset,
    inspect_text_layer,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evaluation"
REPRESENTATIVE_IDS = (
    "invoice_001",
    "invoice_011",
    "invoice_012",
    "invoice_016",
    "invoice_026",
    "invoice_033",
    "invoice_036",
    "invoice_038",
)


def _pdf(invoice_id: str) -> Path:
    return DATASET / "generated" / "development" / invoice_id / "invoice.pdf"


def test_pdfplumber_uses_the_same_source_pdfs() -> None:
    assert all(_pdf(invoice_id).is_file() for invoice_id in REPRESENTATIVE_IDS)
    assert _extract_pdfplumber(_pdf("invoice_011")).page_count == 1
    assert _extract_coordinate_aware(_pdf("invoice_036"))[0].page_count == 2


def test_invoice_number_value_and_label_proximity_scoring() -> None:
    words = [
        {"text": "Invoice", "x0": 10, "x1": 50, "top": 10},
        {"text": "Number", "x0": 55, "x1": 95, "top": 10},
        {"text": "AC-1234", "x0": 110, "x1": 160, "top": 10},
    ]
    result = _contains_near(words, ("invoice", "number"), "AC-1234")
    assert result == {
        "label_present": True,
        "value_present": True,
        "associated": True,
        "minimum_word_distance": 2,
    }


def test_text_layer_inspection_exposes_dates_tables_and_repaired_number() -> None:
    invoice_id = "invoice_011"
    directory = DATASET / "generated" / "development" / invoice_id
    canonical = (directory / "invoice.txt").read_text(encoding="utf-8")
    ground_truth = json.loads((directory / "ground_truth.json").read_text())
    result = inspect_text_layer(
        directory / "invoice.pdf",
        canonical,
        ground_truth,
        extract_strategy_a(directory / "invoice.pdf").text,
    )
    assert result["date_context"]["Invoice Date"]["associated"]
    assert result["table_token_presence"]["description"]
    assert result["invoice_number"]["pdfplumber"]["label_present"]
    assert result["invoice_number"]["pdfplumber"]["value_present"]
    assert result["invoice_number"]["pdfplumber"]["associated"]
    assert result["invoice_number"]["loss_classification"] == "preserved"


def test_strategy_scores_and_multipage_are_reported_stably(tmp_path: Path) -> None:
    report = compare_dataset(DATASET, ["invoice_011", "invoice_036"], tmp_path)
    assert report == tmp_path / "recommendation.md"
    summary = json.loads((tmp_path / "strategy_comparison.json").read_text())
    assert set(summary["averages"]) == {
        "A-production-default",
        "D1-pdfplumber-standard",
        "D2-pdfplumber-layout",
        "D3-coordinate-aware",
    }
    invoice = json.loads((tmp_path / "invoice_036" / "comparison.json").read_text())
    assert invoice["integrity"]["page_count"] == 2
    metric_files = list((tmp_path / "invoice_036").glob("*/metrics.json"))
    assert len(metric_files) == 4
    assert all(
        "structural_fidelity_score" in json.loads(path.read_text())["metrics"]
        for path in metric_files
    )


def test_strategy_a_remains_exact_production_extraction() -> None:
    pdf = _pdf("invoice_012")
    assert extract_strategy_a(pdf).text == extract_production_text(pdf).text_content


def test_comparison_is_offline_and_has_no_ocr_or_provider_calls() -> None:
    source = inspect.getsource(inspect_text_layer)
    module_source = inspect.getsource(
        __import__("evaluation.compare_pdfplumber", fromlist=["main"])
    )
    assert "ocr" not in source.lower()
    assert "Groq" not in module_source
    assert str(DEFAULT_OUTPUT_DIR).endswith("pdfplumber_comparison")
