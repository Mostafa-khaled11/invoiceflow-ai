"""Field, document, line-item, reliability, and segment metrics."""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import Any, Iterable

from evaluation.comparators import compare_field

HEADER_FIELDS = (
    "vendor_name",
    "vendor_tax_id",
    "invoice_number",
    "invoice_date",
    "due_date",
    "purchase_order_number",
    "currency",
    "subtotal",
    "discount_total",
    "tax_total",
    "shipping_total",
    "total_amount",
)
CORE_FIELDS = (
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "currency",
    "total_amount",
)
LINE_FIELDS = ("description", "quantity", "unit_price", "discount", "tax", "line_total")


def percentile(values: Iterable[float], p: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if p <= 0:
        return ordered[0]
    if p >= 100:
        return ordered[-1]
    rank = (len(ordered) - 1) * p / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _score(expected: Any, predicted: Any, field: str) -> dict[str, Any]:
    correct, error = compare_field(expected, predicted, field)
    return {
        "correct": correct,
        "error_type": error,
        "expected": expected,
        "predicted": predicted,
    }


def evaluate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    field_metrics: dict[str, dict[str, Any]] = {}
    all_errors: list[dict[str, Any]] = []
    counts = Counter()
    for field in HEADER_FIELDS:
        results = [
            r["field_results"][field]
            for r in records
            if r.get("prediction_status") == "success"
        ]
        correct = sum(x["correct"] for x in results)
        null_opportunities = sum(x["expected"] is None for x in results)
        null_correct = sum(
            x["expected"] is None and x["predicted"] is None for x in results
        )
        hallucinated = sum(x["error_type"] == "hallucinated_value" for x in results)
        field_metrics[field] = {
            "correct": correct,
            "incorrect": len(results) - correct,
            "total_evaluated": len(results),
            "accuracy": correct / len(results) if results else None,
            "expected_null_count": null_opportunities,
            "correctly_predicted_null_count": null_correct,
            "null_accuracy": null_correct / null_opportunities
            if null_opportunities
            else None,
            "hallucination_count": hallucinated,
            "hallucination_rate": hallucinated / null_opportunities
            if null_opportunities
            else None,
        }
    for record in records:
        for error in record.get("errors", []):
            counts[error["error_type"]] += 1
            all_errors.append(error)
    successful = [r for r in records if r.get("prediction_status") == "success"]
    core_correct = sum(
        sum(r["field_results"][f]["correct"] for f in CORE_FIELDS) for r in successful
    )
    core_total = len(successful) * len(CORE_FIELDS)
    core_docs = sum(
        all(r["field_results"][f]["correct"] for f in CORE_FIELDS) for r in successful
    )
    line = _line_metrics(successful)
    latencies = [
        r["latency_ms"]
        for r in records
        if isinstance(r.get("latency_ms"), (int, float)) and not r.get("cache_hit")
    ]
    input_tokens = [
        r["input_tokens"]
        for r in records
        if isinstance(r.get("input_tokens"), int) and not r.get("cache_hit")
    ]
    output_tokens = [
        r["output_tokens"]
        for r in records
        if isinstance(r.get("output_tokens"), int) and not r.get("cache_hit")
    ]
    reliability = {
        "total_documents": len(records),
        "successful_predictions": len(successful),
        "failed_predictions": len(records) - len(successful),
        "provider_success_rate": sum(
            r.get("provider_call_success", False) for r in records
        )
        / len(records)
        if records
        else None,
        "schema_validity_rate": sum(r.get("schema_valid", False) for r in records)
        / len(records)
        if records
        else None,
        "semantic_validation_success_rate": sum(
            r.get("semantic_valid", False) for r in records
        )
        / len(records)
        if records
        else None,
        "cache_hit_count": sum(bool(r.get("cache_hit")) for r in records),
        "cache_hit_rate": sum(bool(r.get("cache_hit")) for r in records) / len(records)
        if records
        else None,
        "retry_count": sum(max(0, int(r.get("attempt_count", 1)) - 1) for r in records),
        "provider_calls": sum(bool(r.get("provider_called")) for r in records),
    }
    performance = {
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "average_latency_ms": mean(latencies) if latencies else None,
        "average_input_tokens": mean(input_tokens) if input_tokens else None,
        "average_output_tokens": mean(output_tokens) if output_tokens else None,
        "total_input_tokens": sum(input_tokens) if input_tokens else 0,
        "total_output_tokens": sum(output_tokens) if output_tokens else 0,
        "latency_sample_count": len(latencies),
    }
    safety_errors = Counter(
        result["error_type"]
        for record in successful
        for result in record["field_results"].values()
        if result["error_type"]
    )
    null_total = sum(item["expected_null_count"] for item in field_metrics.values())
    field_total = sum(item["total_evaluated"] for item in field_metrics.values())
    null_correct_total = sum(
        item["correctly_predicted_null_count"] for item in field_metrics.values()
    )
    safety = {
        "field_opportunities": field_total,
        "null_opportunities": null_total,
        "null_correct": null_correct_total,
        "null_accuracy": null_correct_total / null_total if null_total else None,
        "hallucination_count": safety_errors["hallucinated_value"],
        "hallucination_rate": safety_errors["hallucinated_value"] / null_total
        if null_total
        else None,
        "missing_value_count": safety_errors["missing_value"],
        "missing_value_rate": safety_errors["missing_value"] / field_total
        if field_total
        else None,
        "wrong_value_count": safety_errors["wrong_value"],
        "wrong_value_rate": safety_errors["wrong_value"] / field_total
        if field_total
        else None,
        "invalid_format_count": safety_errors["invalid_format"],
        "invalid_format_rate": safety_errors["invalid_format"] / field_total
        if field_total
        else None,
    }
    return {
        "reliability": reliability,
        "field_metrics": field_metrics,
        "core_metrics": {
            "core_field_accuracy": core_correct / core_total if core_total else None,
            "core_fields_correct": core_correct,
            "core_fields_evaluated": core_total,
            "document_core_exact_match": core_docs / len(successful)
            if successful
            else None,
            "document_core_exact_match_count": core_docs,
        },
        "document_metrics": {
            "all_header_fields_exact_match": sum(
                all(r["field_results"][f]["correct"] for f in HEADER_FIELDS)
                for r in successful
            )
            / len(successful)
            if successful
            else None
        },
        "safety_metrics": safety,
        "line_item_metrics": line,
        "performance": performance,
        "error_counts": dict(sorted(counts.items())),
        "segment_metrics": segment_metrics(records),
    }


def _line_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    field_results = {field: [] for field in LINE_FIELDS}
    count_correct = 0
    exact_docs = 0
    expected_rows = predicted_rows = 0
    for record in records:
        expected = record.get("ground_truth", {}).get("line_items", [])
        predicted = record.get("prediction", {}).get("line_items", [])
        expected_rows += len(expected)
        predicted_rows += len(predicted)
        count_correct += len(expected) == len(predicted)
        exact = len(expected) == len(predicted)
        for i, item in enumerate(expected):
            if i >= len(predicted):
                exact = False
                continue
            for field in LINE_FIELDS:
                result = _score(item.get(field), predicted[i].get(field), field)
                field_results[field].append(result)
                exact = exact and result["correct"]
        exact_docs += exact
    per_field = {
        f: {
            "correct": sum(x["correct"] for x in xs),
            "incorrect": len(xs) - sum(x["correct"] for x in xs),
            "total_evaluated": len(xs),
            "accuracy": sum(x["correct"] for x in xs) / len(xs) if xs else None,
        }
        for f, xs in field_results.items()
    }
    return {
        "expected_line_item_count": expected_rows,
        "predicted_line_item_count": predicted_rows,
        "line_item_count_correct": count_correct,
        "line_item_count_accuracy": count_correct / len(records) if records else None,
        "line_item_field_accuracy": sum(
            x["correct"] for xs in field_results.values() for x in xs
        )
        / sum(len(xs) for xs in field_results.values())
        if any(field_results.values())
        else None,
        "per_line_item_field": per_field,
        "all_line_items_exact_match": exact_docs / len(records) if records else None,
    }


def segment_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        meta = record.get("metadata", {})
        for key, value in (
            ("difficulty", meta.get("difficulty")),
            ("template", meta.get("template")),
            ("currency_case", meta.get("currency_case")),
            ("adversarial", meta.get("adversarial")),
            ("multi_page", meta.get("multi_page")),
            ("has_due_date", meta.get("has_due_date")),
            ("has_po_number", meta.get("has_po_number")),
            ("has_discount", meta.get("has_discount")),
            ("has_shipping", meta.get("has_shipping")),
        ):
            if value is not None:
                groups[f"{key}={value}"].append(record)
    return {
        key: _segment_summary(value) for key, value in sorted(groups.items()) if value
    }


def _segment_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    successful = [r for r in records if r.get("prediction_status") == "success"]
    return {
        "documents": len(records),
        "successful_predictions": len(successful),
        "document_core_exact_match": sum(
            all(r["field_results"][f]["correct"] for f in CORE_FIELDS)
            for r in successful
        )
        / len(successful)
        if successful
        else None,
        "total_field_accuracy": sum(
            x["correct"] for r in successful for x in r["field_results"].values()
        )
        / (len(successful) * len(HEADER_FIELDS))
        if successful
        else None,
    }
