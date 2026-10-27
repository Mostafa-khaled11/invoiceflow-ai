"""Run the deterministic validation core against all benchmark ground truth."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Any
import sys

_BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.services.business_validation import validate_invoice  # noqa: E402
from app.services.business_validation.models import RULESET_VERSION  # noqa: E402
from app.schemas.structured_extractions import StructuredInvoiceData  # noqa: E402

DEFAULT_OUTPUT = Path("evaluation/diagnostics/business_validation/smoke.json")


def run_smoke(dataset_dir: Path) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    for split in ("development", "holdout"):
        for directory in sorted((dataset_dir / "generated" / split).glob("invoice_*")):
            data = StructuredInvoiceData.model_validate_json(
                (directory / "ground_truth.json").read_text(encoding="utf-8")
            )
            report = validate_invoice(data).model_dump(mode="json")
            reports.append(
                {
                    "invoice_id": directory.name,
                    "split": split,
                    "final_decision": report["final_decision"],
                    "rule_results": report["rule_results"],
                }
            )
    decisions = Counter(report["final_decision"] for report in reports)
    rule_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for report in reports:
        for result in report["rule_results"]:
            rule_counts[result["rule_id"]][result["status"]] += 1
    example_data = StructuredInvoiceData.model_validate_json(
        (
            dataset_dir
            / "generated"
            / "development"
            / "invoice_002"
            / "ground_truth.json"
        ).read_text(encoding="utf-8")
    )
    corrupted_data = example_data.model_copy(deep=True)
    corrupted_data.line_items[0].line_total = "0.00"
    examples = {
        "pass": next(
            report for report in reports if report["final_decision"] == "PASS"
        ),
        "not_evaluable": next(
            report for report in reports if report["final_decision"] == "WARNING"
        ),
        "corrupted_fail": validate_invoice(corrupted_data).model_dump(mode="json"),
    }
    return {
        "ruleset_version": RULESET_VERSION,
        "documents_validated": len(reports),
        "final_decisions": dict(sorted(decisions.items())),
        "rule_counts": {
            rule_id: dict(sorted(counts.items()))
            for rule_id, counts in sorted(rule_counts.items())
        },
        "examples": examples,
        "reports": reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("evaluation"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = run_smoke(args.dataset_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "reports"},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
