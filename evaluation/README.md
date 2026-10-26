# Synthetic invoice evaluation dataset

This package generates the deterministic `invoice-eval-v1` dataset and provides
the structured extraction evaluation engine. Dataset generation and automated
tests do not call Groq.

## Generate

From the repository root, after installing the evaluation extra:

```powershell
python -m pip install -e ".\backend[dev,evaluation]"
python -m evaluation.generate_dataset --seed 42
```

The default output is:

```text
evaluation/
├── dataset_manifest.json
├── generated/
│   ├── development/invoice_001/...invoice_040/
│   └── holdout/invoice_041/...invoice_060/
└── samples/
```

Each invoice directory contains `invoice.pdf`, `invoice.txt`,
`ground_truth.json`, and `metadata.json`. The full `generated/` directory is
ignored by Git. Five representative samples are retained under `samples/`.

## Structured extraction evaluator (Task 4.2B)

The evaluator runs against `invoice-eval-v1` without PostgreSQL. `llm-only` reads
`invoice.txt`; `end-to-end` uses production-style pypdf extraction from
`invoice.pdf`. Both reuse the production prompt, schema, semantic validation,
and provider abstraction.

The default split is development. Holdout requires `--allow-holdout`; it is
never selected implicitly. Dry-run makes zero provider calls:

```powershell
python -m evaluation.run_benchmark --split development --mode llm-only --limit 5 --dry-run
python -m evaluation.run_benchmark --split development --mode llm-only --limit 5
python -m evaluation.run_benchmark --split holdout --mode end-to-end --limit 5 --allow-holdout
python -m evaluation.run_benchmark --split development --mode llm-only --invoice-id invoice_016 --invoice-id invoice_026 --invoice-id invoice_033 --dry-run
```

`--invoice-id` may be repeated. Explicit IDs are evaluated in the order supplied
and are mutually exclusive with `--limit`. Duplicate IDs and unknown IDs fail
before any provider call. A single `--invoice-id` retains the original behavior.

The second command is the explicit real-provider smoke benchmark and requires
`GROQ_API_KEY`. `--no-cache` forces calls and consumes quota. Cache entries are
inspectable JSON under `evaluation/cache/`, keyed by input SHA-256, provider,
model, prompt version, and schema version. Corrupt entries are safe misses.

Each run writes `run_config.json`, `predictions.jsonl`, `errors.json`,
`metrics.json`, and `report.md` under `evaluation/results/`. Metrics include
reliability, normalized field accuracy, core exact match, null accuracy,
hallucination rate, line items, percentiles, usage, and metadata segments.
Automated tests use fake providers and never call Groq.

The fixed v1 dataset has 60 invoices: 40 development and 20 holdout. Template
E is holdout-only. Ground truth is validated against the production
`StructuredInvoiceData` schema from Task 4.1; no competing invoice schema is
defined here.

To validate an existing generated root:

```powershell
python -c "from pathlib import Path; from evaluation.validator import validate_dataset; print(validate_dataset(Path('evaluation')))"
```

The generator uses a local seeded random generator, `Decimal` arithmetic, and
ReportLab `4.4.10` with invariant PDF metadata. It makes no network calls.

The optional live Groq smoke test is deliberately unrelated to this package and
must not be run for dataset generation.
