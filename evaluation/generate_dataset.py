"""Command-line generator for the deterministic invoice benchmark."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Callable

from evaluation import DATASET_VERSION, GENERATOR_VERSION, SCHEMA_VERSION
from evaluation.invoice_factory import build_invoice_specs
from evaluation.models import DatasetManifest, InvoiceSpec
from evaluation.templates import classic, compact, modern, table_heavy, unseen
from evaluation.validator import validate_dataset

TemplateRenderer = Callable[[InvoiceSpec, Path], None]
RENDERERS: dict[str, TemplateRenderer] = {
    "template_a": classic.render,
    "template_b": modern.render,
    "template_c": compact.render,
    "template_d": table_heavy.render,
    "template_e": unseen.render,
}


def generate_dataset(
    output_dir: Path, *, seed: int = 42, count: int = 60
) -> DatasetManifest:
    """Generate, validate, and summarize the complete fixed-size benchmark."""

    if count != 60:
        raise ValueError("invoice-eval-v1 supports exactly count=60")
    output_dir = output_dir.resolve()
    generated_dir = output_dir / "generated"
    if generated_dir.exists():
        shutil.rmtree(generated_dir)
    samples_dir = output_dir / "samples"
    if samples_dir.exists():
        shutil.rmtree(samples_dir)
    specs = build_invoice_specs(seed)
    for spec in specs:
        _write_spec(spec, generated_dir / spec.split / spec.invoice_id)
    for spec in (specs[0], specs[10], specs[20], specs[30], specs[48]):
        _write_spec(spec, samples_dir / spec.invoice_id)

    manifest = _build_manifest(specs, seed)
    (output_dir / "dataset_manifest.json").write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    validate_dataset(output_dir)
    return manifest


def _write_spec(spec: InvoiceSpec, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "invoice.txt").write_text(spec.source_text, encoding="utf-8")
    (directory / "ground_truth.json").write_text(
        json.dumps(spec.ground_truth.model_dump(mode="json"), indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    (directory / "metadata.json").write_text(
        json.dumps(
            spec.metadata.model_dump(mode="json", exclude_defaults=True),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    RENDERERS[spec.template](spec, directory / "invoice.pdf")


def _build_manifest(specs: list[InvoiceSpec], seed: int) -> DatasetManifest:
    def count(attribute: str) -> dict[str, int]:
        return dict(sorted(Counter(getattr(spec, attribute) for spec in specs).items()))

    split_difficulty: dict[str, dict[str, int]] = {}
    for split in ("development", "holdout"):
        split_difficulty[split] = dict(
            sorted(
                Counter(
                    spec.difficulty for spec in specs if spec.split == split
                ).items()
            )
        )
    missing = {
        "vendor_tax_id": sum(spec.ground_truth.vendor_tax_id is None for spec in specs),
        "due_date": sum(spec.ground_truth.due_date is None for spec in specs),
        "purchase_order_number": sum(
            spec.ground_truth.purchase_order_number is None for spec in specs
        ),
        "discount_total": sum(
            spec.ground_truth.discount_total is None for spec in specs
        ),
        "shipping_total": sum(
            spec.ground_truth.shipping_total is None for spec in specs
        ),
    }
    return DatasetManifest(
        dataset_version=DATASET_VERSION,
        schema_version=SCHEMA_VERSION,
        generator_version=GENERATOR_VERSION,
        seed=seed,
        total_documents=len(specs),
        development_documents=sum(spec.split == "development" for spec in specs),
        holdout_documents=sum(spec.split == "holdout" for spec in specs),
        difficulty_distribution=count("difficulty"),
        split_difficulty_distribution=split_difficulty,
        templates=count("template"),
        missing_field_distribution=missing,
        currency_cases=dict(
            sorted(Counter(spec.metadata.currency_case for spec in specs).items())
        ),
        line_item_groups=dict(
            sorted(Counter(spec.metadata.line_item_group for spec in specs).items())
        ),
        adversarial_documents=sum(spec.metadata.adversarial for spec in specs),
        multi_page_documents=sum(spec.metadata.multi_page for spec in specs),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation"))
    args = parser.parse_args()
    manifest = generate_dataset(args.output_dir, seed=args.seed, count=args.count)
    print(json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
