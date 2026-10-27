"""Regression tests for synthetic PDF text-layer integrity."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from app.services.invoice_text_extraction import InvoiceTextExtractor
from evaluation.generate_dataset import RENDERERS, generate_dataset
from evaluation.invoice_factory import build_invoice_specs
from evaluation.text_layer_validator import (
    extract_independent_text,
    validate_text_layer,
)


@pytest.fixture(scope="module")
def repaired_dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("renderer-integrity")
    generate_dataset(root, seed=42)
    return root


def _directory(root: Path, invoice_id: str) -> Path:
    split = "development" if int(invoice_id[-3:]) <= 40 else "holdout"
    return root / "generated" / split / invoice_id


@pytest.mark.parametrize("invoice_id", ["invoice_011", "invoice_012", "invoice_016"])
def test_template_b_invoice_number_label_and_value_are_extractable(
    repaired_dataset: Path, invoice_id: str
) -> None:
    directory = _directory(repaired_dataset, invoice_id)
    truth = json.loads((directory / "ground_truth.json").read_text(encoding="utf-8"))
    text, _ = extract_independent_text(directory / "invoice.pdf")

    assert "Invoice Number" in text
    assert truth["invoice_number"] in text


def test_all_templates_pass_key_field_integrity(repaired_dataset: Path) -> None:
    report = validate_text_layer(repaired_dataset)

    assert report["documents_checked"] == 60
    assert report["failures"] == 0
    assert report["templates"] == {
        "template_a": 12,
        "template_b": 12,
        "template_c": 12,
        "template_d": 12,
        "template_e": 12,
    }


def test_null_fields_are_not_required(repaired_dataset: Path) -> None:
    directory = _directory(repaired_dataset, "invoice_011")
    truth = json.loads((directory / "ground_truth.json").read_text(encoding="utf-8"))
    record = next(
        item
        for item in validate_text_layer(repaired_dataset)["records"]
        if item["invoice_id"] == "invoice_011"
    )

    assert truth["due_date"] is None
    assert truth["discount_total"] is None
    assert record["missing"] == []


def test_visible_line_item_columns_and_values_remain_in_text_layer(
    repaired_dataset: Path,
) -> None:
    directory = _directory(repaired_dataset, "invoice_011")
    text, _ = extract_independent_text(directory / "invoice.pdf")

    assert all(
        token in text
        for token in ("Description", "Qty", "Unit", "Discount", "Tax", "Total")
    )
    assert "Platform Configuration Engagement 011-1" in " ".join(text.split())
    assert all(value in text for value in ("$329.00", "$98.70", "$756.70"))


def test_multi_page_content_is_represented(repaired_dataset: Path) -> None:
    directory = _directory(repaired_dataset, "invoice_036")
    truth = json.loads((directory / "ground_truth.json").read_text(encoding="utf-8"))
    text, page_count = extract_independent_text(directory / "invoice.pdf")

    assert page_count == 2
    assert "continued" in text.lower()
    assert all(
        all(token in text for token in item["description"].split())
        for item in truth["line_items"]
    )


def test_renderer_output_remains_byte_deterministic(tmp_path: Path) -> None:
    spec = build_invoice_specs(seed=42)[10]
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.pdf"

    RENDERERS[spec.template](spec, first)
    RENDERERS[spec.template](spec, second)

    assert first.read_bytes() == second.read_bytes()


def test_integrity_validator_is_independent_and_offline() -> None:
    source = inspect.getsource(
        __import__("evaluation.text_layer_validator", fromlist=["main"])
    )

    assert "pdfplumber" in source
    assert "InvoiceTextExtractor" not in source
    assert "groq" not in source.lower()
    assert "ocr" not in source.lower()

    production_source = inspect.getsource(InvoiceTextExtractor)
    assert "PdfReader" in production_source
    assert "pdfplumber" not in production_source
