"""Independent text-layer integrity validation for generated benchmark PDFs."""

from __future__ import annotations

import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
from typing import Any

import pdfplumber

from evaluation.models import DatasetMetadata, StructuredInvoiceData

_MONEY_RE = re.compile(r"[-+]?(?:[A-Z]{3}\s*)?[$€£¥]?\d[\d,]*\.\d{2}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)*")


class TextLayerIntegrityError(ValueError):
    """Raised when visible benchmark information is absent from a PDF text layer."""


def extract_independent_text(pdf_path: Path) -> tuple[str, int]:
    """Extract digital text with pdfplumber, independently of production pypdf."""

    with pdfplumber.open(pdf_path) as pdf:
        pages = [page.extract_text() or "" for page in pdf.pages]
    return "\f".join(pages), len(pages)


def validate_text_layer(dataset_dir: Path) -> dict[str, Any]:
    """Validate discoverability of visible fields in every generated PDF."""

    records: list[dict[str, Any]] = []
    generated = dataset_dir.resolve() / "generated"
    if not generated.is_dir():
        raise TextLayerIntegrityError(f"missing generated directory: {generated}")
    for split in ("development", "holdout"):
        for directory in sorted((generated / split).glob("invoice_*")):
            records.append(_validate_record(directory))
    failures = [record for record in records if record["missing"]]
    if failures:
        details = "; ".join(
            f"{record['invoice_id']}: {', '.join(record['missing'])}"
            for record in failures
        )
        raise TextLayerIntegrityError(f"text-layer integrity failures: {details}")
    return {
        "documents_checked": len(records),
        "templates": dict(sorted(Counter(r["template"] for r in records).items())),
        "multi_page_documents": sum(record["page_count"] > 1 for record in records),
        "failures": 0,
        "records": records,
    }


def _validate_record(directory: Path) -> dict[str, Any]:
    metadata = DatasetMetadata.model_validate_json(
        (directory / "metadata.json").read_text(encoding="utf-8")
    )
    truth = StructuredInvoiceData.model_validate_json(
        (directory / "ground_truth.json").read_text(encoding="utf-8")
    )
    canonical = (directory / "invoice.txt").read_text(encoding="utf-8")
    extracted, page_count = extract_independent_text(directory / "invoice.pdf")
    missing: list[str] = []

    _require_text(missing, extracted, truth.invoice_number, "invoice_number")
    if metadata.template == "template_b":
        _require_text(missing, extracted, "Invoice Number", "invoice_number_label")
    _require_text(missing, extracted, truth.vendor_name, "vendor_name")
    _require_text(
        missing,
        extracted,
        _canonical_value(canonical, "Invoice Date"),
        "invoice_date",
    )
    if truth.due_date is not None:
        _require_text(
            missing,
            extracted,
            _canonical_value(canonical, "Due Date"),
            "due_date",
        )
    for label, value in (
        ("vendor_tax_id", truth.vendor_tax_id),
        ("purchase_order_number", truth.purchase_order_number),
        ("currency", truth.currency),
    ):
        if value is not None:
            _require_text(missing, extracted, value, label)

    for label, value in (
        ("subtotal", truth.subtotal),
        ("discount_total", truth.discount_total),
        ("tax_total", truth.tax_total),
        ("shipping_total", truth.shipping_total),
        ("total_amount", truth.total_amount),
    ):
        if value is not None and not _money_present(extracted, value):
            missing.append(label)

    for index, item in enumerate(truth.line_items, 1):
        if item.description and not _tokens_present(extracted, item.description):
            missing.append(f"line_items[{index}].description")
        for field in ("quantity", "unit_price", "discount", "tax", "line_total"):
            value = getattr(item, field)
            if value is not None and not _money_present(extracted, value):
                missing.append(f"line_items[{index}].{field}")

    for header in ("Description", "Qty", "Unit", "Discount", "Tax", "Total"):
        _require_text(missing, extracted, header, f"table_header.{header.lower()}")
    if metadata.multi_page and page_count < 2:
        missing.append("multi_page_boundary")
    if metadata.multi_page:
        _require_text(missing, extracted, "continued", "continuation_marker")

    return {
        "invoice_id": metadata.invoice_id,
        "split": metadata.split,
        "template": metadata.template,
        "page_count": page_count,
        "character_count": len(extracted),
        "missing": missing,
    }


def _canonical_value(text: str, label: str) -> str | None:
    match = re.search(
        rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$",
        text,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    return match.group(1) if match else None


def _require_text(
    missing: list[str], extracted: str, expected: str | None, label: str
) -> None:
    if expected is not None and not _tokens_present(extracted, expected):
        missing.append(label)


def _tokens_present(text: str, expected: str) -> bool:
    actual_tokens = Counter(token.casefold() for token in _TOKEN_RE.findall(text))
    expected_tokens = Counter(token.casefold() for token in _TOKEN_RE.findall(expected))
    return all(
        actual_tokens[token] >= count for token, count in expected_tokens.items()
    )


def _money_present(text: str, expected: str) -> bool:
    try:
        target = Decimal(expected)
    except InvalidOperation:
        return False
    for match in _MONEY_RE.findall(text):
        candidate = re.sub(r"[^0-9.+-]", "", match)
        try:
            if Decimal(candidate) == target:
                return True
        except InvalidOperation:
            continue
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("evaluation"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/diagnostics/text_layer_integrity.json"),
    )
    args = parser.parse_args()
    report = validate_text_layer(args.dataset_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "records"}, indent=2))


if __name__ == "__main__":
    main()
