"""Offline PDF text-layer integrity and pdfplumber strategy comparison."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, cast

import pdfplumber

from evaluation.compare_pdf_extraction import (
    DEFAULT_INVOICE_IDS,
    _join_pages,
    _normalize_page,
    _score_structure,
    _unified_diff,
    extract_strategy_a,
)

DEFAULT_OUTPUT_DIR = Path("evaluation/diagnostics/pdfplumber_comparison")
_DATE_RE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b|\b\w+\s+\d{1,2},\s+\d{4}\b")
_NUMBER_RE = re.compile(r"\b[A-Z]{1,4}-\d{3,}\b")
_TABLE_TOKENS = (
    "description",
    "qty",
    "quantity",
    "unit",
    "price",
    "discount",
    "tax",
    "total",
)


@dataclass(frozen=True, slots=True)
class PdfplumberOutput:
    name: str
    description: str
    text: str
    page_count: int
    word_count: int


def _words(page: Any) -> list[dict[str, Any]]:
    return list(page.extract_words(use_text_flow=True, keep_blank_chars=False))


def _word_lines(
    words: list[dict[str, Any]], tolerance: float = 3.0
) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (float(item["top"]), float(item["x0"]))):
        group = next(
            (
                g
                for g in groups
                if abs(float(g[0]["top"]) - float(word["top"])) <= tolerance
            ),
            None,
        )
        if group is None:
            groups.append([word])
        else:
            group.append(word)
    return [
        sorted(group, key=lambda item: float(item["x0"]))
        for group in sorted(groups, key=lambda g: float(g[0]["top"]))
    ]


def _words_to_text(words: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for line in _word_lines(words):
        parts: list[str] = []
        previous: float | None = None
        for word in line:
            x0 = float(word["x0"])
            separator = " | " if previous is not None and x0 - previous > 24 else " "
            parts.append(separator + str(word["text"]))
            previous = float(word["x1"])
        lines.append("".join(parts).strip())
    return "\n".join(lines)


def _extract_pdfplumber(pdf_path: Path, *, layout: bool = False) -> PdfplumberOutput:
    pages: list[str] = []
    word_count = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = _words(page)
            word_count += len(words)
            pages.append(_normalize_page(page.extract_text(layout=layout) or ""))
    name = "D2-pdfplumber-layout" if layout else "D1-pdfplumber-standard"
    description = (
        "pdfplumber page.extract_text(layout=True)"
        if layout
        else "pdfplumber page.extract_text()"
    )
    text = _join_pages(pages)
    return PdfplumberOutput(name, description, text, len(pages), word_count)


def _extract_coordinate_aware(
    pdf_path: Path,
) -> tuple[PdfplumberOutput, list[dict[str, Any]], list[dict[str, Any]]]:
    pages: list[str] = []
    summaries: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            words = _words(page)
            lines = _word_lines(words)
            pages.append(_words_to_text(words))
            summaries.append(
                {
                    "page": page_number,
                    "word_count": len(words),
                    "lines": [" ".join(str(w["text"]) for w in line) for line in lines],
                    "words": words,
                }
            )
            extracted_tables = page.extract_tables() or []
            tables.append(
                {
                    "page": page_number,
                    "table_count": len(extracted_tables),
                    "tables": extracted_tables,
                }
            )
    text = _join_pages(pages)
    return (
        PdfplumberOutput(
            "D3-coordinate-aware",
            "pdfplumber words grouped by y-coordinate and ordered by x-coordinate",
            text,
            len(pages),
            sum(item["word_count"] for item in summaries),
        ),
        summaries,
        tables,
    )


def _canonical_value(canonical: str, label: str) -> str | None:
    match = re.search(
        rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$",
        canonical,
        re.MULTILINE | re.IGNORECASE,
    )
    return match.group(1) if match else None


def _contains_near(
    words: list[dict[str, Any]], label_tokens: tuple[str, ...], value: str | None
) -> dict[str, Any]:
    lowered = [str(word["text"]).lower().strip(":,") for word in words]
    label_indexes = [
        i
        for i in range(len(words) - len(label_tokens) + 1)
        if tuple(lowered[i : i + len(label_tokens)]) == label_tokens
    ]
    value_tokens = (
        [token.strip(":,") for token in value.lower().split()] if value else []
    )
    value_indexes = [
        i
        for i in range(len(words) - len(value_tokens) + 1)
        if value_tokens
        and " ".join(lowered[i : i + len(value_tokens)]) == " ".join(value_tokens)
    ]
    distances = [abs(i - j) for i in label_indexes for j in value_indexes]
    return {
        "label_present": bool(label_indexes),
        "value_present": bool(value_indexes),
        "associated": bool(distances and min(distances) <= 8),
        "minimum_word_distance": min(distances) if distances else None,
    }


def inspect_text_layer(
    pdf_path: Path, canonical: str, ground_truth: dict[str, Any], pypdf_text: str
) -> dict[str, Any]:
    all_words: list[dict[str, Any]] = []
    page_summaries: list[dict[str, Any]] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, 1):
            words = _words(page)
            all_words.extend(words)
            page_summaries.append(
                {
                    "page": page_number,
                    "width": page.width,
                    "height": page.height,
                    "word_count": len(words),
                    "text": page.extract_text() or "",
                }
            )
    invoice_number = str(ground_truth.get("invoice_number") or "")
    invoice_context = _contains_near(all_words, ("invoice", "number"), invoice_number)
    pypdf_invoice = {
        "label_present": "invoice number" in pypdf_text.lower(),
        "value_present": invoice_number in pypdf_text,
    }
    date_context = {
        label: _contains_near(
            all_words, tuple(label.lower().split()), _canonical_value(canonical, label)
        )
        for label in ("Invoice Date", "Due Date")
        if _canonical_value(canonical, label)
    }
    table_tokens = {
        token: any(token in str(word["text"]).lower() for word in all_words)
        for token in _TABLE_TOKENS
    }
    values = {
        field: ground_truth.get(field)
        for field in ("discount_total", "tax_total", "shipping_total")
    }
    values["line_item_financial_values"] = [
        item.get(field)
        for item in ground_truth.get("line_items", [])
        for field in ("discount", "tax")
    ]
    value_presence = {
        field: all(
            value is None
            or str(value) in " ".join(str(word["text"]) for word in all_words)
            for value in (items if isinstance(items, list) else [items])
        )
        for field, items in values.items()
    }
    return {
        "pdf": str(pdf_path),
        "page_count": len(page_summaries),
        "pages": page_summaries,
        "word_count": len(all_words),
        "invoice_number": {
            "pdfplumber": invoice_context,
            "pypdf": pypdf_invoice,
            "loss_classification": "pypdf_loss"
            if invoice_context["value_present"] and not pypdf_invoice["value_present"]
            else "text_layer_or_renderer_missing"
            if not invoice_context["value_present"]
            else "preserved",
        },
        "date_context": date_context,
        "table_token_presence": table_tokens,
        "financial_value_presence": value_presence,
        "canonical_date_tokens": _DATE_RE.findall(canonical),
        "coordinate_words": all_words,
    }


def compare_invoice(dataset_dir: Path, invoice_id: str) -> dict[str, Any]:
    directory = dataset_dir / "generated" / "development" / invoice_id
    canonical = (directory / "invoice.txt").read_text(encoding="utf-8")
    ground_truth = json.loads(
        (directory / "ground_truth.json").read_text(encoding="utf-8")
    )
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    pdf_path = directory / "invoice.pdf"
    production = extract_strategy_a(pdf_path)
    standard = _extract_pdfplumber(pdf_path)
    layout = _extract_pdfplumber(pdf_path, layout=True)
    coordinate, word_summary, table_summary = _extract_coordinate_aware(pdf_path)
    outputs = (production, standard, layout, coordinate)
    integrity = inspect_text_layer(pdf_path, canonical, ground_truth, production.text)
    return {
        "invoice_id": invoice_id,
        "metadata": metadata,
        "integrity": integrity,
        "word_summary": word_summary,
        "table_summary": table_summary,
        "strategies": [
            {
                "name": output.name,
                "description": output.description,
                "page_count": output.page_count,
                "word_count": getattr(output, "word_count", None),
                "text": output.text,
                "metrics": _score_structure(
                    canonical, output.text, ground_truth, metadata
                ),
            }
            for output in outputs
        ],
    }


def compare_dataset(
    dataset_dir: Path, invoice_ids: list[str], output_dir: Path = DEFAULT_OUTPUT_DIR
) -> Path:
    if not invoice_ids or len(invoice_ids) != len(set(invoice_ids)):
        raise ValueError("one or more unique invoice IDs are required")
    output_dir.mkdir(parents=True, exist_ok=True)
    comparisons = [
        compare_invoice(dataset_dir, invoice_id) for invoice_id in invoice_ids
    ]
    for comparison in comparisons:
        invoice_dir = output_dir / comparison["invoice_id"]
        invoice_dir.mkdir(parents=True, exist_ok=True)
        canonical = (
            dataset_dir
            / "generated"
            / "development"
            / comparison["invoice_id"]
            / "invoice.txt"
        ).read_text(encoding="utf-8")
        (invoice_dir / "text_layer_inspection.json").write_text(
            json.dumps(
                {
                    k: v
                    for k, v in comparison["integrity"].items()
                    if k != "coordinate_words"
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (invoice_dir / "word_coordinate_summary.json").write_text(
            json.dumps(comparison["word_summary"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (invoice_dir / "table_summary.json").write_text(
            json.dumps(comparison["table_summary"], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for strategy in comparison["strategies"]:
            strategy_dir = invoice_dir / strategy["name"]
            strategy_dir.mkdir(parents=True, exist_ok=True)
            (strategy_dir / "extracted.txt").write_text(
                strategy["text"], encoding="utf-8"
            )
            (strategy_dir / "unified.diff").write_text(
                _unified_diff(comparison["invoice_id"], canonical, strategy["text"]),
                encoding="utf-8",
            )
            (strategy_dir / "metrics.json").write_text(
                json.dumps(
                    {k: v for k, v in strategy.items() if k != "text"},
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        (invoice_dir / "comparison.json").write_text(
            json.dumps(
                {k: v for k, v in comparison.items() if k != "strategies"},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    summary: dict[str, list[float]] = defaultdict(list)
    for comparison in comparisons:
        for strategy in comparison["strategies"]:
            summary[strategy["name"]].append(
                strategy["metrics"]["structural_fidelity_score"]
            )
    averages: dict[str, float] = {
        name: sum(scores) / len(scores) for name, scores in summary.items()
    }
    component_averages: dict[str, dict[str, float]] = {}
    for name in summary:
        strategies = [
            strategy
            for comparison in comparisons
            for strategy in comparison["strategies"]
            if strategy["name"] == name
        ]
        keys = [key for key in strategies[0]["metrics"] if key.endswith("_score")]
        component_averages[name] = {
            key: sum(strategy["metrics"][key] for strategy in strategies)
            / len(strategies)
            for key in keys
        }
    (output_dir / "strategy_comparison.json").write_text(
        json.dumps(
            {
                "invoice_ids": invoice_ids,
                "averages": averages,
                "components": component_averages,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    production_score = averages["A-production-default"]
    alternative_scores = {
        name: score
        for name, score in averages.items()
        if name != "A-production-default"
    }
    best_alternative = max(
        alternative_scores, key=lambda name: alternative_scores[name]
    )
    advantage = alternative_scores[best_alternative] - production_score
    recommendation = (
        "A — Keep current production pypdf"
        if advantage <= 0.01
        else f"C — Hybrid pypdf with {best_alternative} fallback"
    )
    recommendation_text = f"# PDF Text-Layer Integrity Recommendation\n\nRecommendation: **{recommendation}**.\n\nCompared current production pypdf with pdfplumber standard, layout, and coordinate-aware reconstruction for: {', '.join(invoice_ids)}.\n\nProduction pypdf scored **{production_score:.3f}**. The best pdfplumber alternative (`{best_alternative}`) scored **{alternative_scores[best_alternative]:.3f}**, an advantage of **{advantage:.3f}**.\n\nThe independent text layer is the source of truth for whether content exists in the PDF; no OCR or provider calls were used. Component scores remain in each strategy metrics file. No production extraction change is made by this recommendation.\n"
    renderer_source = (
        Path(__file__).with_name("pdf_renderer.py").read_text(encoding="utf-8")
    )
    number_findings = {
        comparison["invoice_id"]: comparison["integrity"]["invoice_number"]
        for comparison in comparisons
    }
    (output_dir / "renderer_integrity.json").write_text(
        json.dumps(
            {
                "renderer": "evaluation/pdf_renderer.py",
                "renderer_library": "reportlab.pdfgen.canvas.Canvas",
                "uses_reportlab_text_objects": "drawString" in renderer_source,
                "invoice_number_rendering_paths": [
                    line.strip()
                    for line in renderer_source.splitlines()
                    if "invoice_number" in line
                ],
                "production_changed": False,
                "ocr_used": False,
                "invoice_number_findings": number_findings,
                "finding": "Invoice-number presence was checked independently from pdfplumber words and coordinates.",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "recommendation.md").write_text(recommendation_text, encoding="utf-8")
    return output_dir / "recommendation.md"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice-id", action="append", dest="invoice_ids")
    parser.add_argument("--dataset-dir", type=Path, default=Path("evaluation"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    invoice_ids = cast(list[str], args.invoice_ids or list(DEFAULT_INVOICE_IDS))
    print(
        json.dumps(
            {
                "recommendation": str(
                    compare_dataset(args.dataset_dir, invoice_ids, args.output_dir)
                ),
                "invoice_ids": invoice_ids,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
