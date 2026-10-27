"""Diagnose benchmark PDF text fidelity using the production extractor."""

from __future__ import annotations

import argparse
from collections import Counter
import difflib
import json
from pathlib import Path
import re
import shutil
import sys
from tempfile import TemporaryDirectory
from typing import Any, Sequence

_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.config import Settings  # noqa: E402
from app.services.invoice_storage import InvoiceStorage  # noqa: E402
from app.services.invoice_text_extraction import (  # noqa: E402
    InvoiceTextExtractor,
    TextExtractionResult,
)

DEFAULT_INVOICE_IDS = ("invoice_011", "invoice_012", "invoice_036")
_TOKEN_RE = re.compile(r"[A-Za-z0-9$€£¥][A-Za-z0-9$€£¥.,:/+#-]*")
_SECTION_ANCHORS = (
    "vendor",
    "invoice_number",
    "invoice_date",
    "due_date",
    "table",
    "subtotal",
    "discount",
    "tax",
    "shipping",
    "total",
)


def extract_production_text(
    pdf_path: Path, *, settings: Settings | None = None
) -> TextExtractionResult:
    """Extract one PDF through the exact production InvoiceTextExtractor."""

    settings = settings or Settings()
    with TemporaryDirectory(prefix="invoiceflow-diagnostic-") as temporary:
        temporary_root = Path(temporary)
        stored_pdf = temporary_root / "invoice.pdf"
        shutil.copyfile(pdf_path, stored_pdf)
        storage = InvoiceStorage(temporary_root)
        return InvoiceTextExtractor().extract(
            storage,
            "invoice.pdf",
            max_pages=settings.max_invoice_pages,
            min_extracted_text_chars=settings.min_extracted_text_chars,
        )


def diagnose_invoice(
    dataset_dir: Path, invoice_id: str, *, settings: Settings | None = None
) -> dict[str, Any]:
    """Return a machine-readable fidelity diagnosis for one benchmark invoice."""

    directory = dataset_dir / "generated" / "development" / invoice_id
    canonical_path = directory / "invoice.txt"
    pdf_path = directory / "invoice.pdf"
    if not canonical_path.is_file() or not pdf_path.is_file():
        raise FileNotFoundError(f"missing benchmark artifacts for {invoice_id}")
    canonical = canonical_path.read_text(encoding="utf-8")
    extracted_result = extract_production_text(pdf_path, settings=settings)
    extracted = extracted_result.text_content
    canonical_lines = canonical.splitlines()
    extracted_lines = extracted.splitlines()
    diff = list(
        difflib.unified_diff(
            canonical_lines,
            extracted_lines,
            fromfile="invoice.txt",
            tofile="production-pypdf-extracted.txt",
            lineterm="",
        )
    )
    canonical_tokens = Counter(_TOKEN_RE.findall(canonical))
    extracted_tokens = Counter(_TOKEN_RE.findall(extracted))
    missing_tokens = list(
        token
        for token, count in sorted(canonical_tokens.items())
        for _ in range(max(0, count - extracted_tokens[token]))
    )
    missing_lines = [line for line in canonical_lines if line not in extracted_lines]
    page_texts = extracted.split("\f")
    return {
        "invoice_id": invoice_id,
        "canonical_path": str(canonical_path),
        "pdf_path": str(pdf_path),
        "extractor": extracted_result.extractor,
        "extractor_version": extracted_result.extractor_version,
        "status": extracted_result.status.value,
        "error_code": extracted_result.error_code,
        "page_count": extracted_result.page_count,
        "pages_with_text": extracted_result.pages_with_text,
        "canonical_line_count": len(canonical_lines),
        "extracted_line_count": len(extracted_lines),
        "canonical_character_count": len(canonical),
        "extracted_character_count": len(extracted),
        "canonical_non_whitespace_character_count": len("".join(canonical.split())),
        "extracted_non_whitespace_character_count": extracted_result.non_whitespace_character_count,
        "missing_canonical_lines": missing_lines,
        "missing_canonical_tokens": missing_tokens,
        "section_order": _section_order(canonical, extracted),
        "invoice_number_context": _contexts(canonical, extracted, r"invoice\s+number"),
        "date_label_context": _contexts(
            canonical, extracted, r"(?:invoice|document|due)\s+date"
        ),
        "line_item_table_context": _table_context(canonical, extracted),
        "page_boundaries": {
            "page_count": len(page_texts),
            "form_feed_count": extracted.count("\f"),
            "pages": [
                {
                    "page_number": index + 1,
                    "line_count": len(page.splitlines()),
                    "character_count": len(page),
                    "first_lines": page.splitlines()[:3],
                    "last_lines": page.splitlines()[-3:],
                }
                for index, page in enumerate(page_texts)
            ],
        },
        "canonical_text": canonical,
        "extracted_text": extracted,
        "unified_diff": "\n".join(diff) + ("\n" if diff else ""),
    }


def diagnose_dataset(
    dataset_dir: Path,
    invoice_ids: Sequence[str],
    output_dir: Path,
    *,
    settings: Settings | None = None,
) -> Path:
    """Write per-invoice artifacts and a Markdown summary report."""

    if not invoice_ids:
        raise ValueError("at least one invoice ID is required")
    if len(invoice_ids) != len(set(invoice_ids)):
        raise ValueError("duplicate invoice IDs are not allowed")
    output_dir.mkdir(parents=True, exist_ok=True)
    diagnoses = [
        diagnose_invoice(dataset_dir, invoice_id, settings=settings)
        for invoice_id in invoice_ids
    ]
    for diagnosis in diagnoses:
        invoice_dir = output_dir / diagnosis["invoice_id"]
        invoice_dir.mkdir(parents=True, exist_ok=True)
        (invoice_dir / "canonical_invoice.txt").write_text(
            diagnosis["canonical_text"], encoding="utf-8"
        )
        (invoice_dir / "pypdf_extracted.txt").write_text(
            diagnosis["extracted_text"], encoding="utf-8"
        )
        (invoice_dir / "unified.diff").write_text(
            diagnosis["unified_diff"], encoding="utf-8"
        )
        safe_diagnosis = {
            key: value
            for key, value in diagnosis.items()
            if key not in {"canonical_text", "extracted_text", "unified_diff"}
        }
        (invoice_dir / "diagnostics.json").write_text(
            json.dumps(safe_diagnosis, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    report = output_dir / "report.md"
    report.write_text(_render_report(diagnoses), encoding="utf-8")
    return report


def _contexts(canonical: str, extracted: str, pattern: str) -> dict[str, list[str]]:
    regex = re.compile(pattern, re.IGNORECASE)
    return {
        "canonical": _matching_context(canonical, regex),
        "extracted": _matching_context(extracted, regex),
    }


def _matching_context(text: str, regex: re.Pattern[str]) -> list[str]:
    lines = text.splitlines()
    contexts: list[str] = []
    for index, line in enumerate(lines):
        if regex.search(line):
            start = max(0, index - 1)
            end = min(len(lines), index + 2)
            contexts.append("\n".join(lines[start:end]))
    return contexts


def _table_context(canonical: str, extracted: str) -> dict[str, list[str]]:
    return {
        "canonical": _table_lines(canonical),
        "extracted": _table_lines(extracted),
    }


def _table_lines(text: str) -> list[str]:
    lines = text.splitlines()
    starts = [
        index for index, line in enumerate(lines) if "description" in line.lower()
    ]
    if not starts:
        return []
    start = starts[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"^\s*(?:subtotal|total)\s*:", lines[index], re.IGNORECASE):
            end = index + 1
            break
    return lines[start:end]


def _section_order(canonical: str, extracted: str) -> dict[str, Any]:
    def locate(text: str) -> list[str]:
        lines = text.splitlines()
        found: list[tuple[int, str]] = []
        patterns = {
            "vendor": r"^\s*vendor\s*:",
            "invoice_number": r"^\s*invoice\s+number\s*:",
            "invoice_date": r"^\s*invoice\s+date\s*:",
            "due_date": r"^\s*due\s+date\s*:",
            "table": r"description\s*\|",
            "subtotal": r"^\s*subtotal\s*:",
            "discount": r"^\s*discount\s*:",
            "tax": r"^\s*tax\s*:",
            "shipping": r"^\s*shipping\s*:",
            "total": r"^\s*total\s*:",
        }
        for index, line in enumerate(lines):
            for name in _SECTION_ANCHORS:
                if name in patterns and re.search(patterns[name], line, re.IGNORECASE):
                    found.append((index, name))
                    break
        return [name for _, name in sorted(found)]

    canonical_order = locate(canonical)
    extracted_order = locate(extracted)
    return {
        "canonical": canonical_order,
        "extracted": extracted_order,
        "same_order": canonical_order == extracted_order,
    }


def _render_report(diagnoses: list[dict[str, Any]]) -> str:
    sections = [
        "# InvoiceFlow AI — PDF Text Extraction Fidelity Diagnosis",
        "",
        "This report compares canonical `invoice.txt` with text returned by the "
        "production `InvoiceTextExtractor` using pypdf and its normalizer. No "
        "model or extraction behavior was changed.",
        "",
    ]
    for diagnosis in diagnoses:
        sections.extend(
            [
                f"## {diagnosis['invoice_id']}",
                "",
                f"- Pages: {diagnosis['page_count']}",
                f"- Canonical lines/chars: {diagnosis['canonical_line_count']} / {diagnosis['canonical_character_count']}",
                f"- Extracted lines/chars: {diagnosis['extracted_line_count']} / {diagnosis['extracted_character_count']}",
                f"- Missing canonical lines: {len(diagnosis['missing_canonical_lines'])}",
                f"- Missing canonical tokens: {len(diagnosis['missing_canonical_tokens'])}",
                f"- Section order preserved: {diagnosis['section_order']['same_order']}",
                f"- Form-feed page boundaries: {diagnosis['page_boundaries']['form_feed_count']}",
                "",
                "### Invoice-number context",
                "",
                "```text",
                "\n---\n".join(diagnosis["invoice_number_context"]["extracted"])
                or "(no extracted context)",
                "```",
                "",
                "### Date-label context",
                "",
                "```text",
                "\n---\n".join(diagnosis["date_label_context"]["extracted"])
                or "(no extracted context)",
                "```",
                "",
                "### Line-item table context",
                "",
                "```text",
                "\n".join(diagnosis["line_item_table_context"]["extracted"])
                or "(no extracted table context)",
                "```",
                "",
                f"Detailed artifacts: `{diagnosis['invoice_id']}/canonical_invoice.txt`, `{diagnosis['invoice_id']}/pypdf_extracted.txt`, `{diagnosis['invoice_id']}/unified.diff`, and `{diagnosis['invoice_id']}/diagnostics.json`.",
                "",
            ]
        )
    return "\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invoice-id", action="append", dest="invoice_ids")
    parser.add_argument("--dataset-dir", type=Path, default=Path("evaluation"))
    parser.add_argument(
        "--output-dir", type=Path, default=Path("evaluation/diagnostics/pdf_text")
    )
    args = parser.parse_args()
    invoice_ids = args.invoice_ids or list(DEFAULT_INVOICE_IDS)
    report = diagnose_dataset(args.dataset_dir, invoice_ids, args.output_dir)
    print(json.dumps({"report": str(report), "invoice_ids": invoice_ids}, indent=2))


if __name__ == "__main__":
    main()
