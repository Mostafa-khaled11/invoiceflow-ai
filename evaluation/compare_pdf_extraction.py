"""Offline comparison of PDF text extraction strategies."""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, cast

from pypdf import PdfReader

_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402
from evaluation.diagnose_pdf_text import extract_production_text  # noqa: E402

DEFAULT_INVOICE_IDS = ("invoice_011", "invoice_012", "invoice_036")
DEFAULT_OUTPUT_DIR = Path("evaluation/diagnostics/pdf_extraction_comparison")
_TOKEN_RE = re.compile(r"[A-Za-z0-9$€£¥][A-Za-z0-9$€£¥.,:/+#-]*")
_HEADER_TOKENS = ("description", "quantity", "unit", "price", "discount", "total")


@dataclass(frozen=True, slots=True)
class StrategyOutput:
    name: str
    description: str
    text: str
    page_count: int
    form_feed_count: int


@dataclass(frozen=True, slots=True)
class _Fragment:
    text: str
    x: float
    y: float
    order: int


def extract_strategy_a(
    pdf_path: Path, *, settings: Settings | None = None
) -> StrategyOutput:
    """Current production pypdf extraction, unchanged."""

    result = extract_production_text(pdf_path, settings=settings)
    return StrategyOutput(
        "A-production-default",
        "InvoiceTextExtractor with pypdf default extraction",
        result.text_content,
        result.page_count,
        result.text_content.count("\f"),
    )


def extract_strategy_b(pdf_path: Path) -> StrategyOutput:
    """pypdf layout-oriented extraction mode."""

    reader = PdfReader(str(pdf_path), strict=False)
    pages = [
        _normalize_page(page.extract_text(extraction_mode="layout") or "")
        for page in reader.pages
    ]
    text = _join_pages(pages)
    return StrategyOutput(
        "B-pypdf-layout",
        "pypdf extraction_mode=layout",
        text,
        len(reader.pages),
        text.count("\f"),
    )


def extract_strategy_c(pdf_path: Path) -> StrategyOutput:
    """Coordinate-aware reconstruction using pypdf visitor text positions."""

    reader = PdfReader(str(pdf_path), strict=False)
    pages = [_reconstruct_page(page) for page in reader.pages]
    text = _join_pages(pages)
    return StrategyOutput(
        "C-coordinate-aware",
        "pypdf visitor_text grouped by y-coordinate and ordered by x-coordinate",
        text,
        len(reader.pages),
        text.count("\f"),
    )


def compare_invoice(
    dataset_dir: Path, invoice_id: str, *, settings: Settings | None = None
) -> dict[str, Any]:
    directory = dataset_dir / "generated" / "development" / invoice_id
    canonical_path = directory / "invoice.txt"
    pdf_path = directory / "invoice.pdf"
    if not canonical_path.is_file() or not pdf_path.is_file():
        raise FileNotFoundError(f"missing benchmark artifacts for {invoice_id}")
    canonical = canonical_path.read_text(encoding="utf-8")
    ground_truth = json.loads(
        (directory / "ground_truth.json").read_text(encoding="utf-8")
    )
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    outputs = (
        extract_strategy_a(pdf_path, settings=settings),
        extract_strategy_b(pdf_path),
        extract_strategy_c(pdf_path),
    )
    return {
        "invoice_id": invoice_id,
        "canonical_character_count": len(canonical),
        "canonical_line_count": len(canonical.splitlines()),
        "metadata": metadata,
        "strategies": [
            {
                "name": output.name,
                "description": output.description,
                "page_count": output.page_count,
                "form_feed_count": output.form_feed_count,
                "text": output.text,
                "metrics": _score_structure(
                    canonical, output.text, ground_truth, metadata
                ),
            }
            for output in outputs
        ],
    }


def compare_dataset(
    dataset_dir: Path,
    invoice_ids: list[str],
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    *,
    settings: Settings | None = None,
) -> Path:
    if not invoice_ids:
        raise ValueError("at least one invoice ID is required")
    if len(invoice_ids) != len(set(invoice_ids)):
        raise ValueError("duplicate invoice IDs are not allowed")
    output_dir.mkdir(parents=True, exist_ok=True)
    comparisons = [
        compare_invoice(dataset_dir, invoice_id, settings=settings)
        for invoice_id in invoice_ids
    ]
    for comparison in comparisons:
        invoice_dir = output_dir / comparison["invoice_id"]
        invoice_dir.mkdir(parents=True, exist_ok=True)
        for strategy in comparison["strategies"]:
            strategy_dir = invoice_dir / strategy["name"]
            strategy_dir.mkdir(parents=True, exist_ok=True)
            (strategy_dir / "extracted.txt").write_text(
                strategy["text"], encoding="utf-8"
            )
            diff = _unified_diff(
                comparison["invoice_id"],
                comparison["canonical_text"]
                if "canonical_text" in comparison
                else _read_canonical(dataset_dir, comparison["invoice_id"]),
                strategy["text"],
            )
            (strategy_dir / "unified.diff").write_text(diff, encoding="utf-8")
            safe_strategy = {
                key: value for key, value in strategy.items() if key != "text"
            }
            (strategy_dir / "metrics.json").write_text(
                json.dumps(safe_strategy, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        (invoice_dir / "comparison.json").write_text(
            json.dumps(
                {
                    key: value
                    for key, value in comparison.items()
                    if key != "strategies"
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    recommendation = _recommend(comparisons)
    (output_dir / "comparison.json").write_text(
        json.dumps(comparisons, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "recommendation.md").write_text(recommendation, encoding="utf-8")
    return output_dir / "recommendation.md"


def _score_structure(
    canonical: str,
    extracted: str,
    ground_truth: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    lines = extracted.splitlines()
    invoice_number = _context_score(
        lines, "Invoice Number", ground_truth.get("invoice_number")
    )
    date_scores = [
        _context_score(
            lines,
            "Invoice Date",
            _canonical_labeled_value(canonical, "Invoice Date"),
        )
    ]
    if ground_truth.get("due_date") is not None:
        date_scores.append(
            _context_score(
                lines,
                "Due Date",
                _canonical_labeled_value(canonical, "Due Date"),
            )
        )
    table_header = _table_header_score(lines)
    row_scores, financial_scores = _line_item_scores(
        lines, ground_truth.get("line_items", [])
    )
    multi_page = _multi_page_score(extracted, metadata)
    token_score = _token_preservation(canonical, extracted)
    components = {
        "invoice_number_context_score": invoice_number["score"],
        "date_context_score": sum(item["score"] for item in date_scores)
        / len(date_scores),
        "table_header_score": table_header,
        "line_item_row_score": row_scores,
        "financial_column_association_score": financial_scores,
        "multi_page_structure_score": multi_page,
        "text_completeness_score": token_score,
    }
    return {
        **components,
        "structural_fidelity_score": sum(components.values()) / len(components),
        "invoice_number_detail": invoice_number,
        "line_count": len(lines),
        "character_count": len(extracted),
        "missing_canonical_tokens": _missing_tokens(canonical, extracted),
        "section_order": _section_order(canonical, extracted),
        "date_context_details": date_scores,
        "page_boundary_count": extracted.count("\f"),
    }


def _context_score(
    lines: list[str], label: str, expected_value: str | None
) -> dict[str, Any]:
    label_re = re.compile(re.escape(label), re.IGNORECASE)
    value = expected_value or ""
    label_indices = [index for index, line in enumerate(lines) if label_re.search(line)]
    value_indices = [
        index for index, line in enumerate(lines) if value and value in line
    ]
    associated = any(
        abs(label_index - value_index) <= 1
        for label_index in label_indices
        for value_index in value_indices
    )
    score = sum((bool(label_indices), bool(value_indices), associated)) / 3
    return {
        "label": label,
        "label_present": bool(label_indices),
        "value_present": bool(value_indices),
        "label_value_associated": associated,
        "score": score,
    }


def _canonical_labeled_value(canonical: str, label: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*:\s*(.+?)\s*$", re.IGNORECASE)
    values = [
        match.group(1)
        for line in canonical.splitlines()
        if (match := pattern.match(line))
    ]
    return values[0] if len(values) == 1 else None


def _table_header_score(lines: list[str]) -> float:
    header_start = next(
        (index for index, line in enumerate(lines) if "description" in line.lower()),
        None,
    )
    if header_start is None:
        return 0.0
    window = " ".join(lines[header_start : header_start + 7]).lower()
    return sum(token in window for token in _HEADER_TOKENS) / len(_HEADER_TOKENS)


def _line_item_scores(
    lines: list[str], items: list[dict[str, Any]]
) -> tuple[float, float]:
    if not items:
        return 1.0, 1.0
    row_scores: list[float] = []
    financial_scores: list[float] = []
    for item in items:
        description = item.get("description") or ""
        row_index = next(
            (i for i, line in enumerate(lines) if description in line), None
        )
        if row_index is None:
            row_scores.append(0.0)
            financial_scores.append(0.0)
            continue
        window = " ".join(lines[row_index : row_index + 9])
        expected_row_fields = [
            item.get("quantity"),
            item.get("unit_price"),
            item.get("line_total"),
        ]
        present = sum(
            bool(value and _money_token_present(window, value))
            for value in expected_row_fields
        )
        row_scores.append((1 + present) / 4)
        financial_values = [
            item.get(field) for field in ("unit_price", "discount", "tax", "line_total")
        ]
        financial_present = sum(
            bool(value and _money_token_present(window, value))
            for value in financial_values
        )
        financial_expected = sum(value is not None for value in financial_values)
        financial_scores.append(
            financial_present / financial_expected if financial_expected else 1.0
        )
    return sum(row_scores) / len(row_scores), sum(financial_scores) / len(
        financial_scores
    )


def _money_token_present(window: str, value: str) -> bool:
    return value in window or value.lstrip("+-").lstrip("0") in window


def _multi_page_score(text: str, metadata: dict[str, Any]) -> float:
    expected_multi_page = bool(metadata.get("multi_page"))
    if not expected_multi_page:
        return 1.0 if "\f" not in text else 0.0
    checks = (
        text.count("\f") >= 1,
        "continued" in text.lower(),
        text.lower().count("description") >= 2,
        text.count("\f") == 1,
    )
    return sum(checks) / len(checks)


def _token_preservation(canonical: str, extracted: str) -> float:
    expected = _missing_tokens(canonical, extracted)
    total = len(_TOKEN_RE.findall(canonical))
    return (total - len(expected)) / total if total else 1.0


def _missing_tokens(canonical: str, extracted: str) -> list[str]:
    expected = Counter(_TOKEN_RE.findall(canonical))
    actual = Counter(_TOKEN_RE.findall(extracted))
    return [
        token
        for token, count in sorted(expected.items())
        for _ in range(max(0, count - actual[token]))
    ]


def _section_order(canonical: str, extracted: str) -> dict[str, Any]:
    anchors = (
        "invoice number",
        "invoice date",
        "due date",
        "description",
        "subtotal",
        "discount",
        "tax",
        "total",
    )

    def order(text: str) -> list[str]:
        result: list[tuple[int, str]] = []
        for index, line in enumerate(text.lower().splitlines()):
            for anchor in anchors:
                if anchor in line:
                    result.append((index, anchor))
                    break
        return [anchor for _, anchor in sorted(result)]

    expected = order(canonical)
    actual = order(extracted)
    return {
        "canonical": expected,
        "extracted": actual,
        "same_order": expected == actual,
    }


def _normalize_page(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")


def _join_pages(pages: list[str]) -> str:
    return "\f".join(pages).strip(" \t\r\n")


def _reconstruct_page(page: Any) -> str:
    fragments: list[_Fragment] = []

    def visitor(text: str, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        if text.strip():
            fragments.append(
                _Fragment(
                    text.replace("\r", ""), float(tm[4]), float(tm[5]), len(fragments)
                )
            )

    page.extract_text(visitor_text=visitor)
    groups: list[list[_Fragment]] = []
    for fragment in sorted(fragments, key=lambda item: (-item.y, item.x, item.order)):
        group = next(
            (group for group in groups if abs(group[0].y - fragment.y) <= 2.0), None
        )
        if group is None:
            groups.append([fragment])
        else:
            group.append(fragment)
    lines: list[str] = []
    for group in sorted(groups, key=lambda value: (-value[0].y, value[0].x)):
        ordered = sorted(group, key=lambda item: (item.x, item.order))
        line = ordered[0].text.strip()
        previous_x = ordered[0].x
        for fragment in ordered[1:]:
            separator = " | " if fragment.x - previous_x > 24 else " "
            line += separator + fragment.text.strip()
            previous_x = fragment.x
        lines.append(line)
    return "\n".join(lines)


def _read_canonical(dataset_dir: Path, invoice_id: str) -> str:
    return (
        dataset_dir / "generated" / "development" / invoice_id / "invoice.txt"
    ).read_text(encoding="utf-8")


def _unified_diff(invoice_id: str, canonical: str, extracted: str) -> str:
    import difflib

    return (
        "\n".join(
            difflib.unified_diff(
                canonical.splitlines(),
                extracted.splitlines(),
                fromfile=f"{invoice_id}/invoice.txt",
                tofile=f"{invoice_id}/extracted.txt",
                lineterm="",
            )
        )
        + "\n"
    )


def _recommend(comparisons: list[dict[str, Any]]) -> str:
    summaries: dict[str, list[float]] = {}
    descriptions: dict[str, str] = {}
    for comparison in comparisons:
        for strategy in comparison["strategies"]:
            summaries.setdefault(strategy["name"], []).append(
                strategy["metrics"]["structural_fidelity_score"]
            )
            descriptions[strategy["name"]] = strategy["description"]
    averages = {name: sum(scores) / len(scores) for name, scores in summaries.items()}
    best = max(averages, key=lambda name: averages[name])
    lines = [
        "# PDF Text Extraction Strategy Recommendation",
        "",
        "This comparison is offline-only and uses the three benchmark PDFs. Production extraction was not changed.",
        "",
        f"## Recommendation: `{best}`",
        "",
        f"Best average component score: **{averages[best]:.3f}**.",
        "",
        "## Strategy averages",
        "",
        "| Strategy | Description | Average structural fidelity |",
        "|---|---|---:|",
    ]
    lines.extend(
        f"| `{name}` | {descriptions[name]} | {averages[name]:.3f} |"
        for name in sorted(averages)
    )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The component metrics remain visible in each invoice strategy `metrics.json`; the average is only a ranking aid, not a replacement for them.",
            "",
            "- Strategy A reflects current production behavior and establishes the baseline.",
            "- Strategy B is low-complexity to trial because it uses pypdf's supported layout mode.",
            "- Strategy C is more complex and sensitive to PDF coordinate/text-matrix behavior, but can preserve column separation when the layout is regular.",
            "",
            "## Production suitability",
            "",
            "No strategy is promoted automatically. Before replacement, inspect the per-invoice diffs and add a broader layout regression corpus. The recommended next implementation step is an offline production-like trial of the highest-scoring strategy with fallback behavior and regression tests.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice-id", action="append", dest="invoice_ids")
    parser.add_argument("--dataset-dir", type=Path, default=Path("evaluation"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    invoice_ids: list[str] = cast(
        list[str],
        list(args.invoice_ids) if args.invoice_ids else list(DEFAULT_INVOICE_IDS),
    )
    report = compare_dataset(args.dataset_dir, invoice_ids, args.output_dir)
    print(
        json.dumps(
            {"recommendation": str(report), "invoice_ids": invoice_ids}, indent=2
        )
    )


if __name__ == "__main__":
    main()
