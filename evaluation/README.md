# Synthetic invoice evaluation dataset

This package generates the deterministic `invoice-eval-v1` dataset used by the
future extraction evaluation task. It does not call Groq and does not calculate
evaluation metrics.

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
