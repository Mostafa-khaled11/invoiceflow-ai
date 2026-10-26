"""Standalone benchmark runner using the production provider contract."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any, Sequence

from pypdf import PdfReader

from app.core.config import Settings
from app.prompts.invoice_extraction_v1 import PROMPT_VERSION as DEFAULT_PROMPT_VERSION
from app.prompts.invoice_prompt_registry import get_invoice_prompt_builder
from app.schemas.structured_extractions import (
    StructuredInvoiceData,
    validate_structured_invoice_semantics,
)
from app.services.invoice_llm import (
    InvoiceLLMProvider,
    InvoiceLLMProviderError,
    GroqInvoiceLLMProvider,
)
from evaluation import SCHEMA_VERSION
from evaluation.cache import PredictionCache, cache_key
from evaluation.metrics import HEADER_FIELDS, evaluate_records
from evaluation.result_models import write_json, write_jsonl

EVALUATOR_VERSION = "1.0.0"


class EvaluationConfigurationError(ValueError):
    pass


def resolve_prompt_version(prompt_version: str = DEFAULT_PROMPT_VERSION) -> str:
    """Validate a prompt version through the shared prompt registry."""

    try:
        get_invoice_prompt_builder(prompt_version)
    except ValueError as exc:
        raise EvaluationConfigurationError(str(exc)) from exc
    return prompt_version


def select_documents(
    dataset_dir: Path,
    split: str = "development",
    limit: int | None = None,
    invoice_id: str | None = None,
    allow_holdout: bool = False,
    invoice_ids: Sequence[str] | None = None,
) -> list[Path]:
    if split not in {"development", "holdout"}:
        raise EvaluationConfigurationError("split must be development or holdout")
    if split == "holdout" and not allow_holdout:
        raise EvaluationConfigurationError(
            "holdout evaluation requires --allow-holdout"
        )
    root = dataset_dir / "generated" / split
    if not root.is_dir():
        raise EvaluationConfigurationError(f"dataset split not found: {root}")
    paths = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    requested_ids = list(invoice_ids or ())
    if invoice_id is not None:
        if requested_ids:
            raise EvaluationConfigurationError(
                "use either invoice_id or invoice_ids, not both"
            )
        requested_ids = [invoice_id]
    if requested_ids and limit is not None:
        raise EvaluationConfigurationError(
            "--limit and --invoice-id are mutually exclusive"
        )
    if len(requested_ids) != len(set(requested_ids)):
        raise EvaluationConfigurationError("duplicate invoice IDs are not allowed")
    if requested_ids:
        by_id = {path.name: path for path in paths}
        unknown = [
            invoice_id for invoice_id in requested_ids if invoice_id not in by_id
        ]
        if unknown:
            raise EvaluationConfigurationError(
                f"invoice ID(s) not found in {split}: {', '.join(unknown)}"
            )
        # Preserve explicit user order; the dataset itself remains stable-sorted.
        return [by_id[invoice_id] for invoice_id in requested_ids]
    if limit is not None:
        if limit < 1:
            raise EvaluationConfigurationError("limit must be positive")
        paths = paths[:limit]
    return paths


def _text_from_pdf(path: Path) -> str:
    reader = PdfReader(str(path), strict=False)
    return "\f".join(
        (page.extract_text() or "")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\x00", "")
        for page in reader.pages
    ).strip(" \t\r\n")


def _safe_input(path: Path, mode: str) -> str:
    if mode == "llm-only":
        return (path / "invoice.txt").read_text(encoding="utf-8")
    if mode == "end-to-end":
        return _text_from_pdf(path / "invoice.pdf")
    raise EvaluationConfigurationError("mode must be llm-only or end-to-end")


def _identity(
    text: str, provider: str, model: str, prompt: str, schema: str
) -> dict[str, Any]:
    return {
        "input_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "provider": provider,
        "model": model,
        "prompt_version": prompt,
        "schema_version": schema,
    }


def dry_run(
    dataset_dir: Path,
    *,
    split: str = "development",
    mode: str = "llm-only",
    limit: int | None = None,
    invoice_id: str | None = None,
    invoice_ids: Sequence[str] | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    allow_holdout: bool = False,
    cache_dir: Path | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    prompt_version = resolve_prompt_version(prompt_version)
    selected = select_documents(
        dataset_dir,
        split=split,
        limit=limit,
        invoice_id=invoice_id,
        invoice_ids=invoice_ids,
        allow_holdout=allow_holdout,
    )
    settings = settings or Settings()
    provider = settings.llm_provider
    model = settings.groq_model
    cache = PredictionCache(cache_dir or dataset_dir / "cache")
    cached = required = total_chars = 0
    for path in selected:
        text = _safe_input(path, mode)
        total_chars += len(text)
        if cache.load(
            cache_key(text, provider, model, prompt_version, SCHEMA_VERSION),
            _identity(text, provider, model, prompt_version, SCHEMA_VERSION),
        ):
            cached += 1
        else:
            required += 1
    return {
        "dataset_version": "invoice-eval-v1",
        "split": split,
        "mode": mode,
        "selected_documents": len(selected),
        "cached_predictions": cached,
        "provider_calls_required": required,
        "total_input_characters": total_chars,
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
        "schema_version": SCHEMA_VERSION,
        "dry_run": True,
    }


def evaluate(
    dataset_dir: Path,
    *,
    split: str = "development",
    mode: str = "llm-only",
    limit: int | None = None,
    invoice_id: str | None = None,
    invoice_ids: Sequence[str] | None = None,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
    allow_holdout: bool = False,
    cache_enabled: bool = True,
    output_dir: Path | None = None,
    provider: InvoiceLLMProvider | None = None,
    cache_dir: Path | None = None,
    settings: Settings | None = None,
) -> tuple[Path, dict[str, Any]]:
    prompt_version = resolve_prompt_version(prompt_version)
    selected = select_documents(
        dataset_dir,
        split=split,
        limit=limit,
        invoice_id=invoice_id,
        invoice_ids=invoice_ids,
        allow_holdout=allow_holdout,
    )
    settings = settings or Settings()
    provider = provider or GroqInvoiceLLMProvider(settings)
    cache = PredictionCache(cache_dir or dataset_dir / "cache")
    records: list[dict[str, Any]] = []
    for path in selected:
        metadata = json.loads((path / "metadata.json").read_text(encoding="utf-8"))
        truth = json.loads((path / "ground_truth.json").read_text(encoding="utf-8"))
        record: dict[str, Any] = {
            "invoice_id": path.name,
            "split": split,
            "difficulty": metadata["difficulty"],
            "template": metadata["template"],
            "metadata": metadata,
            "ground_truth": truth,
            "provider": provider.provider_name,
            "model": provider.model_name,
            "prompt_version": prompt_version,
            "prediction_status": "failed",
            "cache_hit": False,
            "provider_called": False,
            "provider_call_success": False,
            "retries_performed_this_run": 0,
            "schema_valid": False,
            "semantic_valid": False,
            "errors": [],
            "field_results": {},
        }
        try:
            text = _safe_input(path, mode)
        except Exception as exc:
            record["errors"] = [
                _error(
                    path.name,
                    "document",
                    "pdf_text_extraction_error"
                    if mode == "end-to-end"
                    else "input_error",
                    None,
                    None,
                    metadata,
                    str(exc),
                )
            ]
            records.append(record)
            continue
        identity = _identity(
            text,
            provider.provider_name,
            provider.model_name,
            prompt_version,
            SCHEMA_VERSION,
        )
        key = cache_key(
            text,
            provider.provider_name,
            provider.model_name,
            prompt_version,
            SCHEMA_VERSION,
        )
        response: dict[str, Any] | None = (
            cache.load(key, identity) if cache_enabled else None
        )
        if response:
            record["cache_hit"] = True
        else:
            record["provider_called"] = True
            started = perf_counter()
            try:
                result = provider.extract(
                    text, schema=StructuredInvoiceData.model_json_schema()
                )
                response = {
                    "data": result.data,
                    "raw_model_output": result.raw_model_output,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "attempt_count": result.attempt_count,
                    "latency_ms": max(0, round((perf_counter() - started) * 1000)),
                }
                record["provider_call_success"] = True
                if cache_enabled:
                    cache.save(key, identity, response)
            except InvoiceLLMProviderError as exc:
                record["attempt_count"] = exc.attempt_count
                record["retries_performed_this_run"] = max(exc.attempt_count - 1, 0)
                record["errors"] = [
                    _error(
                        path.name,
                        "document",
                        "provider_error",
                        None,
                        None,
                        metadata,
                        exc.error_code,
                    )
                ]
            except Exception as exc:
                record["attempt_count"] = 1
                record["errors"] = [
                    _error(
                        path.name,
                        "document",
                        "provider_error",
                        None,
                        None,
                        metadata,
                        type(exc).__name__,
                    )
                ]
        if response:
            record.update(
                {
                    k: response.get(k)
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "latency_ms",
                        "attempt_count",
                    )
                }
            )
            if not record["cache_hit"]:
                record["retries_performed_this_run"] = max(
                    int(response.get("attempt_count") or 1) - 1, 0
                )
            record["prediction"] = response["data"]
            try:
                validated = StructuredInvoiceData.model_validate(response["data"])
                record["schema_valid"] = True
                validate_structured_invoice_semantics(validated)
                record["semantic_valid"] = True
                record["prediction_status"] = "success"
            except Exception as exc:
                record["errors"] = [
                    _error(
                        path.name,
                        "document",
                        "schema_error"
                        if not record["schema_valid"]
                        else "semantic_validation_error",
                        truth,
                        response["data"],
                        metadata,
                        type(exc).__name__,
                    )
                ]
            if record["prediction_status"] == "success":
                for field in HEADER_FIELDS:
                    result = _field_result(
                        truth.get(field), record["prediction"].get(field), field
                    )
                    record["field_results"][field] = result
                    if result["error_type"]:
                        record["errors"].append(
                            _error(
                                path.name,
                                field,
                                result["error_type"],
                                result["expected"],
                                result["predicted"],
                                metadata,
                            )
                        )
                _line_errors(record)
        records.append(record)
    metrics = evaluate_records(records)
    config = {
        "dataset_version": "invoice-eval-v1",
        "schema_version": SCHEMA_VERSION,
        "seed": 42,
        "split": split,
        "mode": mode,
        "selected_document_count": len(selected),
        "provider": provider.provider_name,
        "model": provider.model_name,
        "prompt_version": prompt_version,
        "structured_schema_version": SCHEMA_VERSION,
        "cache_enabled": cache_enabled,
        "selected_invoice_ids": [p.name for p in selected],
        "evaluator_version": EVALUATOR_VERSION,
    }
    run_id = sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:12]
    result_root = (output_dir or dataset_dir / "results") / f"{split}-{mode}-{run_id}"
    result_root.mkdir(parents=True, exist_ok=True)
    write_json(result_root / "run_config.json", config)
    write_jsonl(result_root / "predictions.jsonl", records)
    write_json(
        result_root / "errors.json", [e for r in records for e in r.get("errors", [])]
    )
    write_json(result_root / "metrics.json", {"run_config": config, **metrics})
    from evaluation.reports import render_report

    (result_root / "report.md").write_text(
        render_report(config, metrics), encoding="utf-8"
    )
    return result_root, metrics


def _field_result(expected: Any, predicted: Any, field: str) -> dict[str, Any]:
    from evaluation.comparators import compare_field

    correct, error = compare_field(expected, predicted, field)
    return {
        "correct": correct,
        "error_type": error,
        "expected": expected,
        "predicted": predicted,
    }


def _error(
    invoice_id: str,
    field: str,
    error_type: str,
    expected: Any,
    predicted: Any,
    metadata: dict[str, Any],
    detail: str | None = None,
) -> dict[str, Any]:
    item = {
        "invoice_id": invoice_id,
        "field": field,
        "error_type": error_type,
        "expected": expected,
        "predicted": predicted,
        "difficulty": metadata.get("difficulty"),
        "template": metadata.get("template"),
    }
    if detail:
        item["detail"] = detail
    return item


def _line_errors(record: dict[str, Any]) -> None:
    expected = record["ground_truth"].get("line_items", [])
    predicted = record["prediction"].get("line_items", [])
    metadata = record["metadata"]
    if len(predicted) > len(expected):
        for i in range(len(expected), len(predicted)):
            record["errors"].append(
                _error(
                    record["invoice_id"],
                    f"line_items[{i}]",
                    "extra_line_item",
                    None,
                    predicted[i],
                    metadata,
                )
            )
    if len(predicted) < len(expected):
        for i in range(len(predicted), len(expected)):
            record["errors"].append(
                _error(
                    record["invoice_id"],
                    f"line_items[{i}]",
                    "missing_line_item",
                    expected[i],
                    None,
                    metadata,
                )
            )
    for index, expected_item in enumerate(expected[: len(predicted)]):
        predicted_item = predicted[index]
        for field in (
            "description",
            "quantity",
            "unit_price",
            "discount",
            "tax",
            "line_total",
        ):
            result = _field_result(
                expected_item.get(field), predicted_item.get(field), field
            )
            if result["error_type"]:
                error = _error(
                    record["invoice_id"],
                    f"line_items[{index}].{field}",
                    result["error_type"],
                    result["expected"],
                    result["predicted"],
                    metadata,
                )
                error["row_index"] = index
                record["errors"].append(error)
