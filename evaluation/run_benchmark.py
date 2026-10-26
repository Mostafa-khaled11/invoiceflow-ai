"""CLI for offline-safe structured invoice benchmark evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.prompts.invoice_extraction_v1 import PROMPT_VERSION as DEFAULT_PROMPT_VERSION
from app.prompts.invoice_prompt_registry import PROMPT_BUILDERS
from evaluation.runner import EvaluationConfigurationError, dry_run, evaluate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--split", choices=("development", "holdout"), default="development"
    )
    parser.add_argument(
        "--mode", choices=("llm-only", "end-to-end"), default="llm-only"
    )
    parser.add_argument(
        "--prompt-version",
        choices=tuple(sorted(PROMPT_BUILDERS)),
        default=DEFAULT_PROMPT_VERSION,
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--limit", type=int)
    selection.add_argument(
        "--invoice-id",
        action="append",
        dest="invoice_ids",
        help="select one or more invoice IDs; repeat the option, preserving order",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("evaluation") / "results",
        help="directory for isolated run results (dataset remains under evaluation/)",
    )
    parser.add_argument("--allow-holdout", action="store_true")
    args = parser.parse_args()
    try:
        if args.dry_run:
            result = dry_run(
                Path("evaluation"),
                split=args.split,
                mode=args.mode,
                limit=args.limit,
                invoice_ids=args.invoice_ids,
                prompt_version=args.prompt_version,
                allow_holdout=args.allow_holdout,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            result_dir, metrics = evaluate(
                Path("evaluation"),
                split=args.split,
                mode=args.mode,
                limit=args.limit,
                invoice_ids=args.invoice_ids,
                prompt_version=args.prompt_version,
                allow_holdout=args.allow_holdout,
                cache_enabled=not args.no_cache,
                output_dir=args.output_dir,
            )
            print(
                json.dumps(
                    {"result_dir": str(result_dir), "metrics": metrics},
                    indent=2,
                    sort_keys=True,
                    default=str,
                )
            )
    except EvaluationConfigurationError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
