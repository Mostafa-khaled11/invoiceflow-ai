"""Validation rules for the synthetic invoice benchmark."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from pypdf import PdfReader

from evaluation import DATASET_VERSION, SCHEMA_VERSION
from evaluation.models import DatasetManifest, DatasetMetadata, StructuredInvoiceData

EXPECTED_TOTALS = {
    "documents": {"total": 60, "development": 40, "holdout": 20},
    "difficulty": {"easy": 20, "medium": 25, "hard": 15},
    "split_difficulty": {
        "development": {"easy": 15, "medium": 17, "hard": 8},
        "holdout": {"easy": 5, "medium": 8, "hard": 7},
    },
    "templates": {
        "template_a": 12,
        "template_b": 12,
        "template_c": 12,
        "template_d": 12,
        "template_e": 12,
    },
    "missing": {
        "vendor_tax_id": 18,
        "due_date": 20,
        "purchase_order_number": 24,
        "discount_total": 36,
        "shipping_total": 42,
    },
    "currency_cases": {"ambiguous_symbol": 8, "explicit": 52},
    "line_item_groups": {"1": 14, "2-3": 20, "4-5": 16, "6-10": 10},
    "adversarial": 5,
    "multi_page": 8,
}


class DatasetValidationError(ValueError):
    """Raised when generated benchmark artifacts violate the specification."""


def validate_dataset(dataset_dir: Path) -> DatasetManifest:
    """Validate full generated data, samples, manifest, PDFs, and ground truth."""

    dataset_dir = dataset_dir.resolve()
    generated = dataset_dir / "generated"
    if not generated.is_dir():
        raise DatasetValidationError(f"missing generated directory: {generated}")
    records = []
    for split in ("development", "holdout"):
        split_dir = generated / split
        if not split_dir.is_dir():
            raise DatasetValidationError(f"missing split directory: {split_dir}")
        for directory in sorted(path for path in split_dir.iterdir() if path.is_dir()):
            records.append(_validate_record(directory, expected_split=split))
    if len(records) != 60:
        raise DatasetValidationError(
            f"expected 60 generated invoices, found {len(records)}"
        )
    _validate_distribution(records)
    _validate_samples(dataset_dir / "samples")

    manifest_path = dataset_dir / "dataset_manifest.json"
    if not manifest_path.is_file():
        raise DatasetValidationError("missing dataset_manifest.json")
    manifest = DatasetManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    expected = _manifest_from_records(records, manifest.seed)
    if manifest.model_dump() != expected.model_dump():
        raise DatasetValidationError(
            "dataset_manifest.json does not match generated records"
        )
    return manifest


def _validate_record(
    directory: Path, *, expected_split: str
) -> tuple[DatasetMetadata, StructuredInvoiceData]:
    required = ("invoice.pdf", "invoice.txt", "ground_truth.json", "metadata.json")
    for filename in required:
        if not (directory / filename).is_file():
            raise DatasetValidationError(f"{directory}: missing {filename}")
    metadata = DatasetMetadata.model_validate(
        json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    )
    ground_truth = StructuredInvoiceData.model_validate(
        json.loads((directory / "ground_truth.json").read_text(encoding="utf-8"))
    )
    if metadata.split != expected_split or metadata.invoice_id != directory.name:
        raise DatasetValidationError(f"{directory}: split or invoice ID mismatch")
    text = (directory / "invoice.txt").read_text(encoding="utf-8")
    if not text.strip():
        raise DatasetValidationError(f"{directory}: invoice.txt is empty")
    for item in ground_truth.line_items:
        if item.description and item.description not in text:
            raise DatasetValidationError(
                f"{directory}: line item absent from source text"
            )
    if metadata.line_item_count != len(ground_truth.line_items):
        raise DatasetValidationError(f"{directory}: line item count mismatch")
    reader = PdfReader(str(directory / "invoice.pdf"), strict=False)
    if reader.is_encrypted or len(reader.pages) < 1:
        raise DatasetValidationError(f"{directory}: PDF is unreadable or encrypted")
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not extracted.strip():
        raise DatasetValidationError(
            f"{directory}: PDF has no extractable digital text"
        )
    if metadata.multi_page != (len(reader.pages) > 1):
        raise DatasetValidationError(f"{directory}: multi-page metadata mismatch")
    _validate_date_ambiguity(directory, metadata, ground_truth)
    _validate_financial_consistency(directory, ground_truth)
    if (
        metadata.currency_case == "ambiguous_symbol"
        and ground_truth.currency is not None
    ):
        raise DatasetValidationError(f"{directory}: ambiguous currency must be null")
    return metadata, ground_truth


def _validate_date_ambiguity(
    directory: Path, metadata: DatasetMetadata, ground_truth: StructuredInvoiceData
) -> None:
    """Prevent accidental locale-dependent slash dates in the benchmark."""

    if metadata.date_format != "slash":
        return
    if metadata.date_ambiguity == "slash_day_month_ambiguous":
        if ground_truth.invoice_date is not None:
            raise DatasetValidationError(
                f"{directory}: ambiguous invoice date must have null ground truth"
            )
        if "ambiguous_invoice_date" not in ground_truth.warnings:
            raise DatasetValidationError(
                f"{directory}: ambiguous invoice date warning is missing"
            )
    elif metadata.date_ambiguity != "none":
        raise DatasetValidationError(f"{directory}: unsupported date ambiguity case")
    for label, value in (
        ("invoice date", ground_truth.invoice_date),
        ("due date", ground_truth.due_date),
    ):
        if value is not None and int(value[8:10]) <= 12:
            raise DatasetValidationError(
                f"{directory}: slash {label} is ambiguous from document text"
            )


def _validate_financial_consistency(
    directory: Path, data: StructuredInvoiceData
) -> None:
    subtotal = _required_decimal(data.subtotal, "subtotal")
    discount_total = _decimal(data.discount_total) or Decimal("0")
    tax_total = _required_decimal(data.tax_total, "tax_total")
    shipping_total = _decimal(data.shipping_total) or Decimal("0")
    line_gross = Decimal("0")
    line_discount = Decimal("0")
    line_tax = Decimal("0")
    line_net = Decimal("0")
    line_tax_visible = True
    for item in data.line_items:
        gross = _required_decimal(item.quantity, "quantity") * _required_decimal(
            item.unit_price, "unit_price"
        )
        discount = _decimal(item.discount) or Decimal("0")
        tax = _decimal(item.tax)
        line_gross += gross
        line_discount += discount
        line_net += gross - discount
        if tax is None:
            line_tax_visible = False
        else:
            line_tax += tax
        if _required_decimal(item.line_total, "line_total") != (
            gross - discount + (tax or Decimal("0"))
        ):
            raise DatasetValidationError(f"{directory}: line item arithmetic mismatch")
    if subtotal != line_gross or discount_total != line_discount:
        raise DatasetValidationError(
            f"{directory}: subtotal or discount arithmetic mismatch"
        )
    if line_tax_visible and tax_total != line_tax:
        raise DatasetValidationError(f"{directory}: line tax arithmetic mismatch")
    if (
        _required_decimal(data.total_amount, "total_amount")
        != subtotal - discount_total + tax_total + shipping_total
    ):
        raise DatasetValidationError(f"{directory}: total arithmetic mismatch")
    if data.currency is None and "ambiguous_currency" not in data.warnings:
        raise DatasetValidationError(f"{directory}: missing currency warning")


def _validate_distribution(
    records: list[tuple[DatasetMetadata, StructuredInvoiceData]],
) -> None:
    metadata = [item[0] for item in records]
    if len({item.invoice_id for item in metadata}) != 60:
        raise DatasetValidationError("invoice IDs are not unique")
    if Counter(item.difficulty for item in metadata) != Counter(
        EXPECTED_TOTALS["difficulty"]
    ):
        raise DatasetValidationError("difficulty distribution mismatch")
    if Counter(item.template for item in metadata) != Counter(
        EXPECTED_TOTALS["templates"]
    ):
        raise DatasetValidationError("template distribution mismatch")
    if Counter(item.split for item in metadata) != Counter(
        {"development": 40, "holdout": 20}
    ):
        raise DatasetValidationError("split distribution mismatch")
    for split, expected in EXPECTED_TOTALS["split_difficulty"].items():
        actual = Counter(item.difficulty for item in metadata if item.split == split)
        if actual != Counter(expected):
            raise DatasetValidationError(f"{split} difficulty distribution mismatch")
    if any(
        item.template == "template_e"
        for item in metadata
        if item.split == "development"
    ):
        raise DatasetValidationError("template_e appears in development")
    for field, expected in EXPECTED_TOTALS["missing"].items():
        actual = sum(
            getattr(item, f"has_{_metadata_field(field)}") is False for item in metadata
        )
        if actual != expected:
            raise DatasetValidationError(
                f"missing-field distribution mismatch for {field}"
            )
    if Counter(item.currency_case for item in metadata) != Counter(
        EXPECTED_TOTALS["currency_cases"]
    ):
        raise DatasetValidationError("currency-case distribution mismatch")
    if Counter(item.line_item_group for item in metadata) != Counter(
        EXPECTED_TOTALS["line_item_groups"]
    ):
        raise DatasetValidationError("line-item distribution mismatch")
    if sum(item.adversarial for item in metadata) != EXPECTED_TOTALS["adversarial"]:
        raise DatasetValidationError("adversarial count mismatch")
    if sum(item.multi_page for item in metadata) != EXPECTED_TOTALS["multi_page"]:
        raise DatasetValidationError("multi-page count mismatch")


def _validate_samples(samples_dir: Path) -> None:
    if not samples_dir.is_dir():
        raise DatasetValidationError("missing representative samples directory")
    directories = sorted(path for path in samples_dir.iterdir() if path.is_dir())
    if len(directories) != 5:
        raise DatasetValidationError(
            f"expected 5 representative samples, found {len(directories)}"
        )
    templates = []
    for directory in directories:
        metadata, _ = _validate_record(
            directory,
            expected_split="development"
            if directory.name != "invoice_049"
            else "holdout",
        )
        templates.append(metadata.template)
    if set(templates) != {
        "template_a",
        "template_b",
        "template_c",
        "template_d",
        "template_e",
    }:
        raise DatasetValidationError(
            "representative samples do not cover all templates"
        )


def _manifest_from_records(
    records: list[tuple[DatasetMetadata, StructuredInvoiceData]], seed: int
) -> DatasetManifest:
    metadata = [item[0] for item in records]
    ground_truth = [item[1] for item in records]
    split_difficulty = {
        split: dict(
            sorted(
                Counter(
                    item.difficulty for item in metadata if item.split == split
                ).items()
            )
        )
        for split in ("development", "holdout")
    }
    missing = {
        "vendor_tax_id": sum(item.vendor_tax_id is None for item in ground_truth),
        "due_date": sum(item.due_date is None for item in ground_truth),
        "purchase_order_number": sum(
            item.purchase_order_number is None for item in ground_truth
        ),
        "discount_total": sum(item.discount_total is None for item in ground_truth),
        "shipping_total": sum(item.shipping_total is None for item in ground_truth),
    }
    return DatasetManifest(
        dataset_version=DATASET_VERSION,
        schema_version=SCHEMA_VERSION,
        generator_version="1.0.0",
        seed=seed,
        total_documents=60,
        development_documents=40,
        holdout_documents=20,
        difficulty_distribution=dict(
            sorted(Counter(item.difficulty for item in metadata).items())
        ),
        split_difficulty_distribution=split_difficulty,
        templates=dict(sorted(Counter(item.template for item in metadata).items())),
        missing_field_distribution=missing,
        currency_cases=dict(
            sorted(Counter(item.currency_case for item in metadata).items())
        ),
        line_item_groups=dict(
            sorted(Counter(item.line_item_group for item in metadata).items())
        ),
        adversarial_documents=sum(item.adversarial for item in metadata),
        multi_page_documents=sum(item.multi_page for item in metadata),
    )


def _metadata_field(field: str) -> str:
    return {
        "vendor_tax_id": "vendor_tax_id",
        "due_date": "due_date",
        "purchase_order_number": "po_number",
        "discount_total": "discount",
        "shipping_total": "shipping",
    }[field]


def _decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _required_decimal(value: str | None, field: str) -> Decimal:
    parsed = _decimal(value)
    if parsed is None:
        raise DatasetValidationError(f"required generated value is missing: {field}")
    return parsed
