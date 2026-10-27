"""Offline tests for Task 4.2B evaluator behavior."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from app.schemas.structured_extractions import StructuredInvoiceData
from app.services.invoice_llm import InvoiceLLMProviderError, InvoiceLLMResponse
from app.services.structured_extraction_guardrails import (
    apply_structured_extraction_guardrails,
)
from evaluation.cache import PredictionCache, cache_key
from evaluation.comparators import compare_field
from evaluation.metrics import HEADER_FIELDS, evaluate_records
from evaluation.runner import (
    EvaluationConfigurationError,
    dry_run,
    evaluate,
    resolve_prompt_version,
    select_documents,
)

DATASET = Path(__file__).parents[2] / "evaluation"


def test_comparators_cover_conservative_normalization() -> None:
    assert compare_field(" Acme  Ltd ", "acme ltd", "vendor_name")[0]
    assert compare_field("INV-001", "001", "invoice_number")[1] == "wrong_value"
    assert compare_field("12.0", "12.00", "total_amount")[0]
    assert compare_field("2026-01-02", "2026-01-02", "invoice_date")[0]
    assert compare_field("USD", "usd", "currency")[0]
    assert compare_field(None, None, "due_date")[0]
    assert compare_field(None, "USD", "currency")[1] == "hallucinated_value"
    assert compare_field("USD", None, "currency")[1] == "missing_value"
    assert compare_field("1.00", "not-money", "total_amount")[1] == "invalid_format"
    assert compare_field("USD", "CAD", "currency")[1] == "wrong_value"


def test_cache_identity_and_corruption(tmp_path: Path) -> None:
    identity = {
        "input_sha256": "x",
        "provider": "groq",
        "model": "m",
        "prompt_version": "p",
        "schema_version": "s",
    }
    cache = PredictionCache(tmp_path)
    key = cache_key("text", "groq", "m", "p", "s")
    cache.save(key, identity, {"data": {"currency": "USD"}})
    assert cache.load(key, identity) is not None
    assert cache.load(cache_key("changed", "groq", "m", "p", "s"), identity) is None
    (tmp_path / f"{key}.json").write_text("broken", encoding="utf-8")
    assert cache.load(key, identity) is None


class GroundTruthProvider:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def extract(
        self, document_text: str, *, schema: dict[str, Any]
    ) -> InvoiceLLMResponse:
        self.calls += 1
        if self.fail:
            raise InvoiceLLMProviderError(
                "provider_unavailable", attempt_count=1, retryable=False
            )
        invoice_id = "invoice_001" if "AC-8213" in document_text else "invoice_002"
        truth = json.loads(
            (
                DATASET / "generated" / "development" / invoice_id / "ground_truth.json"
            ).read_text(encoding="utf-8")
        )
        return InvoiceLLMResponse(
            data=truth,
            raw_model_output=truth,
            input_tokens=10,
            output_tokens=5,
            attempt_count=1,
        )


class StaticProvider:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, data: dict[str, Any], *, fail: bool = False) -> None:
        self.data = data
        self.fail = fail
        self.calls = 0

    def extract(
        self, document_text: str, *, schema: dict[str, Any]
    ) -> InvoiceLLMResponse:
        self.calls += 1
        if self.fail:
            raise InvoiceLLMProviderError(
                "provider_unavailable", attempt_count=1, retryable=False
            )
        return InvoiceLLMResponse(
            data=deepcopy(self.data),
            raw_model_output=deepcopy(self.data),
            input_tokens=10,
            output_tokens=5,
            attempt_count=1,
        )


def _benchmark_truth(invoice_id: str) -> dict[str, Any]:
    return json.loads(
        (
            DATASET / "generated" / "development" / invoice_id / "ground_truth.json"
        ).read_text(encoding="utf-8")
    )


def _prediction_record(result_dir: Path) -> dict[str, Any]:
    return json.loads(
        (result_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )


def test_selection_dry_run_and_holdout_guard(tmp_path: Path) -> None:
    assert [p.name for p in select_documents(DATASET, limit=5)] == [
        f"invoice_{i:03d}" for i in range(1, 6)
    ]
    result = dry_run(DATASET, limit=5, cache_dir=tmp_path / "cache")
    assert result["provider_calls_required"] == 5
    with pytest.raises(EvaluationConfigurationError, match="allow-holdout"):
        select_documents(DATASET, split="holdout")
    assert (
        len(select_documents(DATASET, split="holdout", limit=1, allow_holdout=True))
        == 1
    )


def test_single_explicit_invoice_id_selection() -> None:
    selected = select_documents(DATASET, invoice_id="invoice_016")
    assert [path.name for path in selected] == ["invoice_016"]


def test_multiple_invoice_ids_preserve_user_order() -> None:
    selected = select_documents(
        DATASET,
        invoice_ids=["invoice_026", "invoice_016", "invoice_033"],
    )
    assert [path.name for path in selected] == [
        "invoice_026",
        "invoice_016",
        "invoice_033",
    ]


def test_unknown_and_duplicate_invoice_ids_are_rejected_before_evaluation() -> None:
    with pytest.raises(EvaluationConfigurationError, match="not found"):
        select_documents(DATASET, invoice_ids=["invoice_016", "invoice_999"])
    with pytest.raises(EvaluationConfigurationError, match="duplicate"):
        select_documents(DATASET, invoice_ids=["invoice_016", "invoice_016"])


def test_explicit_invoice_ids_cannot_be_combined_with_limit() -> None:
    with pytest.raises(EvaluationConfigurationError, match="mutually exclusive"):
        select_documents(DATASET, limit=2, invoice_ids=["invoice_016"])


def test_dry_run_supports_multiple_invoice_ids_without_provider_calls(
    tmp_path: Path,
) -> None:
    result = dry_run(
        DATASET,
        invoice_ids=["invoice_016", "invoice_026", "invoice_033"],
        cache_dir=tmp_path / "cache",
    )
    assert result["selected_documents"] == 3
    assert result["provider_calls_required"] == 3
    assert result["dry_run"] is True


def test_prompt_version_defaults_to_v1(tmp_path: Path) -> None:
    assert resolve_prompt_version() == "invoice-extraction-v1"
    result = dry_run(DATASET, limit=1, cache_dir=tmp_path / "cache")
    assert result["prompt_version"] == "invoice-extraction-v1"


def test_explicit_v2_prompt_version_is_recorded_in_dry_run(tmp_path: Path) -> None:
    result = dry_run(
        DATASET,
        limit=1,
        prompt_version="invoice-extraction-v2",
        cache_dir=tmp_path / "cache",
    )
    assert result["prompt_version"] == "invoice-extraction-v2"
    assert result["provider_calls_required"] == 1


def test_selected_prompt_version_is_persisted_in_run_and_predictions(
    tmp_path: Path,
) -> None:
    result_dir, _ = evaluate(
        DATASET,
        limit=1,
        prompt_version="invoice-extraction-v2",
        provider=GroundTruthProvider(),
        output_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    config = json.loads((result_dir / "run_config.json").read_text(encoding="utf-8"))
    prediction = json.loads(
        (result_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    metrics = json.loads((result_dir / "metrics.json").read_text(encoding="utf-8"))
    assert config["prompt_version"] == "invoice-extraction-v2"
    assert prediction["prompt_version"] == "invoice-extraction-v2"
    assert metrics["run_config"]["prompt_version"] == "invoice-extraction-v2"


def test_unknown_prompt_version_is_rejected_before_provider_use() -> None:
    with pytest.raises(
        EvaluationConfigurationError, match="unsupported invoice prompt"
    ):
        dry_run(DATASET, limit=1, prompt_version="invoice-extraction-v99")


def test_v1_and_v2_cache_keys_are_isolated() -> None:
    text = "invoice text"
    v1_key = cache_key(text, "groq", "model", "invoice-extraction-v1", "schema")
    v2_key = cache_key(text, "groq", "model", "invoice-extraction-v2", "schema")
    assert v1_key != v2_key


def test_evaluation_writes_results_and_cache_prevents_duplicate_calls(
    tmp_path: Path,
) -> None:
    first_provider = GroundTruthProvider()
    result_dir, metrics = evaluate(
        DATASET,
        limit=1,
        provider=first_provider,
        output_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    assert first_provider.calls == 1
    assert metrics["core_metrics"]["document_core_exact_match"] == 1
    for name in (
        "run_config.json",
        "predictions.jsonl",
        "errors.json",
        "metrics.json",
        "report.md",
    ):
        assert (result_dir / name).exists()
    second_provider = GroundTruthProvider(fail=True)
    _, second_metrics = evaluate(
        DATASET,
        limit=1,
        provider=second_provider,
        output_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    assert second_provider.calls == 0
    assert second_metrics["reliability"]["cache_hit_count"] == 1


def test_provider_failure_is_recorded_without_crashing(tmp_path: Path) -> None:
    result_dir, metrics = evaluate(
        DATASET,
        limit=2,
        provider=GroundTruthProvider(fail=True),
        output_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    assert metrics["reliability"]["failed_predictions"] == 2
    errors = json.loads((result_dir / "errors.json").read_text(encoding="utf-8"))
    assert all(error["error_type"] == "provider_error" for error in errors)


def test_evaluator_applies_guardrails_to_invoice_011_like_cached_raw_response(
    tmp_path: Path,
) -> None:
    raw = _benchmark_truth("invoice_011")
    raw.update(
        {
            "subtotal": "$658.00",
            "tax_total": "$98.70",
            "shipping_total": "$35.00",
            "total_amount": "$791.70",
        }
    )
    raw["line_items"][0].update(
        {
            "unit_price": "$329.00",
            "tax": "$98.70",
            "line_total": "$756.70",
        }
    )
    cache_dir = tmp_path / "cache"
    result_dir, metrics = evaluate(
        DATASET,
        invoice_id="invoice_011",
        provider=StaticProvider(raw),
        output_dir=tmp_path / "results",
        cache_dir=cache_dir,
    )
    record = _prediction_record(result_dir)
    assert record["prediction_status"] == "success"
    assert record["raw_provider_prediction"]["subtotal"] == "$658.00"
    assert record["final_guardrailed_prediction"]["subtotal"] == "658.00"
    assert record["prediction"]["shipping_total"] == "35.00"
    assert record["prediction"]["line_items"][0]["unit_price"] == "329.00"
    assert "schema_error" not in [error["error_type"] for error in record["errors"]]
    assert metrics["field_metrics"]["subtotal"]["correct"] == 1

    cached_result_dir, cached_metrics = evaluate(
        DATASET,
        invoice_id="invoice_011",
        provider=StaticProvider(raw, fail=True),
        output_dir=tmp_path / "cached-results",
        cache_dir=cache_dir,
    )
    cached_record = _prediction_record(cached_result_dir)
    assert cached_record["cache_hit"] is True
    assert cached_record["final_guardrailed_prediction"]["total_amount"] == "791.70"
    assert cached_metrics["reliability"]["successful_predictions"] == 1


def test_evaluator_recovers_invoice_012_like_labeled_dates(tmp_path: Path) -> None:
    raw = _benchmark_truth("invoice_012")
    raw["invoice_date"] = None
    raw["due_date"] = None
    result_dir, _ = evaluate(
        DATASET,
        invoice_id="invoice_012",
        provider=StaticProvider(raw),
        output_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    record = _prediction_record(result_dir)
    assert record["raw_provider_prediction"]["invoice_date"] is None
    assert record["final_guardrailed_prediction"]["invoice_date"] == "2026-03-14"
    assert record["final_guardrailed_prediction"]["due_date"] == "2026-04-13"
    assert record["guardrail_metadata"]["fallback_fields"] == [
        "invoice_date",
        "due_date",
    ]


def test_evaluator_leaves_ambiguous_slash_date_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _benchmark_truth("invoice_001")
    raw["invoice_date"] = None
    monkeypatch.setattr(
        "evaluation.runner._safe_input",
        lambda path, mode: "Invoice Date: 07/02/2026",
    )
    result_dir, _ = evaluate(
        DATASET,
        invoice_id="invoice_001",
        provider=StaticProvider(raw),
        output_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    assert _prediction_record(result_dir)["prediction"]["invoice_date"] is None


def test_evaluator_never_overwrites_existing_non_null_model_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _benchmark_truth("invoice_001")
    raw["invoice_date"] = "2026-01-01"
    monkeypatch.setattr(
        "evaluation.runner._safe_input",
        lambda path, mode: "Invoice Date: 14/03/2026",
    )
    result_dir, _ = evaluate(
        DATASET,
        invoice_id="invoice_001",
        provider=StaticProvider(raw),
        output_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    assert _prediction_record(result_dir)["prediction"]["invoice_date"] == "2026-01-01"


def test_evaluator_and_production_guardrails_share_final_schema_output(
    tmp_path: Path,
) -> None:
    raw = _benchmark_truth("invoice_012")
    raw.update({"invoice_date": None, "due_date": None, "subtotal": "$1,834.00"})
    source_text = (
        DATASET / "generated" / "development" / "invoice_012" / "invoice.txt"
    ).read_text(encoding="utf-8")
    expected = StructuredInvoiceData.model_validate(
        apply_structured_extraction_guardrails(raw, source_text).data
    )
    result_dir, _ = evaluate(
        DATASET,
        invoice_id="invoice_012",
        provider=StaticProvider(raw),
        output_dir=tmp_path / "results",
        cache_dir=tmp_path / "cache",
    )
    actual = StructuredInvoiceData.model_validate(
        _prediction_record(result_dir)["final_guardrailed_prediction"]
    )
    assert actual == expected


def test_reliability_denominators_separate_cache_calls_and_schema_failures() -> None:
    correct_fields = {
        field: {
            "correct": True,
            "error_type": None,
            "expected": "value",
            "predicted": "value",
        }
        for field in HEADER_FIELDS
    }
    cached_successes = [
        {
            "prediction_status": "success",
            "cache_hit": True,
            "provider_called": False,
            "provider_call_success": False,
            "schema_valid": True,
            "semantic_valid": True,
            "attempt_count": 3,
            "retries_performed_this_run": 0,
            "field_results": correct_fields,
            "errors": [],
        }
        for _ in range(4)
    ]
    provider_success = {
        "prediction_status": "failed",
        "cache_hit": False,
        "provider_called": True,
        "provider_call_success": True,
        "schema_valid": False,
        "semantic_valid": False,
        "attempt_count": 2,
        "retries_performed_this_run": 1,
        "field_results": {},
        "errors": [{"error_type": "schema_error"}],
    }

    reliability = evaluate_records(cached_successes + [provider_success])["reliability"]

    assert reliability["provider_call_count"] == 1
    assert reliability["provider_call_success_count"] == 1
    assert reliability["provider_success_rate"] == 1.0
    assert reliability["prediction_success_rate"] == 0.8
    assert reliability["schema_validity_rate"] == 0.8
    assert reliability["semantic_validation_success_rate"] == 0.8
    assert reliability["formatting_schema_failure_count"] == 1
    assert reliability["retries_performed_this_run"] == 1
    assert reliability["cached_historical_retry_count"] == 8
