"""Human-readable Markdown report rendering."""

from __future__ import annotations

import json
from typing import Any


def _fmt(value: Any) -> str:
    return (
        "n/a"
        if value is None
        else f"{value:.3f}"
        if isinstance(value, float)
        else str(value)
    )


def render_report(config: dict[str, Any], metrics: dict[str, Any]) -> str:
    holdout = (
        "\n> **HOLDOUT EVALUATION** — use only for final assessment; do not tune prompts from this run.\n"
        if config["split"] == "holdout"
        else ""
    )
    fields = metrics["field_metrics"]
    rows = "\n".join(
        f"| `{field}` | {data['correct']} | {data['total_evaluated']} | {_fmt(data['accuracy'])} |"
        for field, data in fields.items()
    )
    segments = "\n".join(
        f"| `{key}` | {value['documents']} | {_fmt(value['document_core_exact_match'])} |"
        for key, value in metrics["segment_metrics"].items()
    )
    return f"""# InvoiceFlow AI — Structured Extraction Evaluation
{holdout}
## Run Configuration

```json
{json.dumps(config, indent=2, sort_keys=True)}
```

## Overall Results

- Documents: {metrics["reliability"]["total_documents"]}
- Successful predictions: {metrics["reliability"]["successful_predictions"]}
- Core field accuracy: {_fmt(metrics["core_metrics"]["core_field_accuracy"])}
- Document Core Exact Match: {_fmt(metrics["core_metrics"]["document_core_exact_match"])}

## Core Fields

Core fields are vendor name, invoice number, invoice date, currency, and total amount.

## Field-Level Accuracy

| Field | Correct | Evaluated | Accuracy |
|---|---:|---:|---:|
{rows}

## Null & Hallucination Analysis

Null accuracy and hallucination rate use ground-truth-null opportunities only. A hallucination is a non-null prediction where the ground truth is null.

## Line Items

{json.dumps(metrics["line_item_metrics"], indent=2, sort_keys=True)}

## Reliability

{json.dumps(metrics["reliability"], indent=2, sort_keys=True)}

## Performance

{json.dumps(metrics["performance"], indent=2, sort_keys=True)}

## By Difficulty / Template / Special Cases

| Segment | Documents | Core Exact Match |
|---|---:|---:|
{segments}

## Top Error Categories

{json.dumps(metrics["error_counts"], indent=2, sort_keys=True)}

## Worst Performing Fields

{", ".join(sorted(fields, key=lambda f: fields[f]["accuracy"] if fields[f]["accuracy"] is not None else 1)[:5])}

## Notes / Limitations

- Line items are compared by source order; no reordering or fuzzy matching is performed.
- Cached predictions retain original provider metadata and do not count as new latency or token usage.
- This report contains no API keys, authorization headers, or hidden reasoning.
"""
