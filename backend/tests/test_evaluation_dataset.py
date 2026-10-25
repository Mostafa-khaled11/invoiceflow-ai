"""Tests for the offline synthetic benchmark generator."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from app.schemas.structured_extractions import WarningCode
from evaluation.generate_dataset import generate_dataset
from evaluation.invoice_factory import build_invoice_specs
from evaluation.validator import (
    DatasetValidationError,
    _validate_date_ambiguity,
    validate_dataset,
)
from pypdf import PdfReader


def test_dataset_generation_and_validation(tmp_path: Path) -> None:
    root = tmp_path / "dataset"

    manifest = generate_dataset(root, seed=42)

    assert manifest.total_documents == 60
    assert manifest.development_documents == 40
    assert manifest.holdout_documents == 20
    assert manifest.difficulty_distribution == {"easy": 20, "hard": 15, "medium": 25}
    assert manifest.templates == {
        "template_a": 12,
        "template_b": 12,
        "template_c": 12,
        "template_d": 12,
        "template_e": 12,
    }
    assert validate_dataset(root).model_dump() == manifest.model_dump()


def test_generation_is_reproducible_including_pdf_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    generate_dataset(first, seed=42)
    generate_dataset(second, seed=42)

    first_files = sorted(
        path.relative_to(first) for path in first.rglob("*") if path.is_file()
    )
    second_files = sorted(
        path.relative_to(second) for path in second.rglob("*") if path.is_file()
    )
    assert first_files == second_files
    for relative in first_files:
        assert (first / relative).read_bytes() == (second / relative).read_bytes(), (
            relative
        )

    pdf_hash = hashlib.sha256(
        (first / "generated/development/invoice_001/invoice.pdf").read_bytes()
    ).hexdigest()
    assert len(pdf_hash) == 64


def test_representative_pdfs_are_digital_text_pdfs(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    generate_dataset(root, seed=42)
    for pdf in sorted((root / "samples").glob("*/invoice.pdf")):
        reader = PdfReader(str(pdf), strict=False)
        assert reader.is_encrypted is False
        assert len(reader.pages) >= 1
        assert any((page.extract_text() or "").strip() for page in reader.pages)


def test_adversarial_documents_are_limited_and_do_not_change_ground_truth(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dataset"
    generate_dataset(root, seed=42)
    adversarial = []
    for metadata_path in (root / "generated").rglob("metadata.json"):
        metadata = metadata_path.read_text(encoding="utf-8")
        if '"adversarial": true' in metadata:
            adversarial.append(metadata_path.parent)
    assert len(adversarial) == 5
    for directory in adversarial:
        source = (directory / "invoice.txt").read_text(encoding="utf-8")
        ground_truth = (directory / "ground_truth.json").read_text(encoding="utf-8")
        assert "Ignore previous instructions" in source
        assert '"5000.00"' not in ground_truth


def test_generator_rejects_unsupported_counts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exactly count=60"):
        generate_dataset(tmp_path / "dataset", count=59)


def test_validator_rejects_missing_artifacts(tmp_path: Path) -> None:
    with pytest.raises(DatasetValidationError, match="missing generated directory"):
        validate_dataset(tmp_path / "missing")


def test_slash_dates_are_unambiguous_and_not_silently_reinterpreted() -> None:
    specs = build_invoice_specs(seed=42)
    slash_specs = [spec for spec in specs if spec.metadata.date_format == "slash"]

    assert len(slash_specs) == 12
    assert all(spec.metadata.date_ambiguity == "none" for spec in slash_specs)
    for spec in slash_specs:
        assert int(spec.render_data["invoice_date"][0:2]) > 12
        if spec.render_data["due_date"] is not None:
            assert int(spec.render_data["due_date"][0:2]) > 12
        assert spec.ground_truth.invoice_date is not None
        assert (
            spec.ground_truth.due_date is None
            or int(spec.ground_truth.due_date[8:10]) > 12
        )
        assert "ambiguous_invoice_date" not in spec.ground_truth.warnings


def test_intentional_ambiguous_date_contract_is_null_and_warned() -> None:
    specs = build_invoice_specs(seed=42)
    assert not any(
        spec.metadata.date_ambiguity == "slash_day_month_ambiguous" for spec in specs
    )
    source = next(spec for spec in specs if spec.metadata.date_format == "slash")
    metadata = source.metadata.model_copy(
        update={"date_ambiguity": "slash_day_month_ambiguous"}
    )
    ground_truth = source.ground_truth.model_copy(
        update={
            "invoice_date": None,
            "due_date": None,
            "warnings": [WarningCode.AMBIGUOUS_INVOICE_DATE],
        }
    )
    _validate_date_ambiguity(Path("intentional"), metadata, ground_truth)
    with pytest.raises(DatasetValidationError, match="null ground truth"):
        _validate_date_ambiguity(Path("intentional"), metadata, source.ground_truth)


def test_seed_42_date_generation_is_deterministic() -> None:
    first = build_invoice_specs(seed=42)
    second = build_invoice_specs(seed=42)
    assert [spec.source_text for spec in first] == [spec.source_text for spec in second]
    assert [spec.ground_truth.model_dump() for spec in first] == [
        spec.ground_truth.model_dump() for spec in second
    ]
