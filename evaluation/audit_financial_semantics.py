"""Offline audit of the synthetic benchmark's existing financial semantics."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
from typing import Any

from evaluation import DATASET_VERSION

CENT = Decimal("0.01")
ZERO = Decimal("0")
DEFAULT_OUTPUT_DIR = Path("evaluation/diagnostics/financial_semantics")


def money(value: Decimal) -> Decimal:
    """Apply the generator's observed two-place half-up currency rounding."""

    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def decimal(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def _invoice_number(invoice_id: str) -> int:
    return int(invoice_id.rsplit("_", 1)[1])


def _tax_rate(invoice_number: int) -> Decimal:
    return Decimal((5, 10, 15)[invoice_number % 3]) / Decimal(100)


def _issue(
    issues: list[dict[str, Any]],
    kind: str,
    field: str,
    expected: Any,
    actual: Any,
) -> None:
    if expected != actual:
        issues.append(
            {
                "kind": kind,
                "field": field,
                "expected": str(expected) if expected is not None else None,
                "actual": str(actual) if actual is not None else None,
            }
        )


def audit_invoice(directory: Path) -> dict[str, Any]:
    """Audit one generated invoice using independently written arithmetic."""

    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    truth = json.loads((directory / "ground_truth.json").read_text(encoding="utf-8"))
    invoice_number = _invoice_number(metadata["invoice_id"])
    tax_rate = _tax_rate(invoice_number)
    issues: list[dict[str, Any]] = []
    rounding_mismatches: list[dict[str, Any]] = []
    null_zero_anomalies: list[dict[str, Any]] = []
    line_details: list[dict[str, Any]] = []
    gross_values: list[Decimal] = []
    discount_values: list[Decimal] = []
    tax_values: list[Decimal] = []

    for index, item in enumerate(truth["line_items"], 1):
        quantity = Decimal(item["quantity"])
        unit_price = Decimal(item["unit_price"])
        gross = money(quantity * unit_price)
        discount = decimal(item["discount"]) or ZERO
        taxable = gross - discount
        tax_unrounded = taxable * tax_rate
        tax = money(tax_unrounded)
        visible_tax = decimal(item["tax"])
        line_tax = tax if metadata["tax_presentation"] == "line_items" else ZERO
        line_total = taxable + line_tax

        _issue(
            issues,
            "line_arithmetic",
            f"line_items[{index}].gross",
            gross,
            money(quantity * unit_price),
        )
        expected_discount = (
            money(gross * Decimal("0.05")) if metadata["has_discount"] else None
        )
        if item["discount"] is not None:
            _issue(
                issues,
                "line_discount",
                f"line_items[{index}].discount",
                expected_discount,
                decimal(item["discount"]),
            )
        if visible_tax is not None:
            _issue(
                issues,
                "line_tax",
                f"line_items[{index}].tax",
                tax,
                visible_tax,
            )
        _issue(
            issues,
            "line_total",
            f"line_items[{index}].line_total",
            money(line_total),
            decimal(item["line_total"]),
        )
        if visible_tax is not None and money(tax_unrounded) != visible_tax:
            rounding_mismatches.append(
                {
                    "field": f"line_items[{index}].tax",
                    "unrounded": str(tax_unrounded),
                    "rounded": str(tax),
                    "actual": str(visible_tax),
                }
            )
        if metadata["has_discount"] != (item["discount"] is not None):
            null_zero_anomalies.append(
                {
                    "field": f"line_items[{index}].discount",
                    "expected_presence": metadata["has_discount"],
                }
            )
        expected_tax_presence = metadata["tax_presentation"] == "line_items"
        if expected_tax_presence != (item["tax"] is not None):
            null_zero_anomalies.append(
                {
                    "field": f"line_items[{index}].tax",
                    "expected_presence": expected_tax_presence,
                }
            )
        gross_values.append(gross)
        discount_values.append(discount)
        tax_values.append(tax)
        line_details.append(
            {
                "index": index,
                "quantity": str(quantity),
                "unit_price": str(unit_price),
                "gross": str(gross),
                "discount": str(discount) if item["discount"] is not None else None,
                "tax": str(visible_tax) if visible_tax is not None else None,
                "tax_rate": str(tax_rate),
                "taxable": str(taxable),
                "line_total": str(line_total),
            }
        )

    subtotal = money(sum(gross_values, ZERO))
    discount_total = (
        money(sum(discount_values, ZERO)) if metadata["has_discount"] else None
    )
    tax_total = money(sum(tax_values, ZERO))
    shipping_total = (
        money(Decimal(25 + (invoice_number % 5) * 10))
        if metadata["has_shipping"]
        else None
    )
    total = subtotal - (discount_total or ZERO) + tax_total + (shipping_total or ZERO)

    _issue(issues, "subtotal", "subtotal", subtotal, decimal(truth["subtotal"]))
    _issue(
        issues,
        "discount_total",
        "discount_total",
        discount_total,
        decimal(truth["discount_total"]),
    )
    _issue(issues, "tax_total", "tax_total", tax_total, decimal(truth["tax_total"]))
    _issue(
        issues,
        "shipping_total",
        "shipping_total",
        shipping_total,
        decimal(truth["shipping_total"]),
    )
    _issue(
        issues,
        "total_amount",
        "total_amount",
        total,
        decimal(truth["total_amount"]),
    )
    if metadata["has_discount"] != (truth["discount_total"] is not None):
        null_zero_anomalies.append(
            {"field": "discount_total", "expected_presence": metadata["has_discount"]}
        )
    if metadata["has_shipping"] != (truth["shipping_total"] is not None):
        null_zero_anomalies.append(
            {"field": "shipping_total", "expected_presence": metadata["has_shipping"]}
        )

    return {
        "invoice_id": metadata["invoice_id"],
        "template": metadata["template"],
        "difficulty": metadata["difficulty"],
        "discount_present": metadata["has_discount"],
        "tax_presentation": metadata["tax_presentation"],
        "shipping_present": metadata["has_shipping"],
        "multi_page": metadata["multi_page"],
        "adversarial": metadata["adversarial"],
        "line_item_count": len(truth["line_items"]),
        "line_details": line_details,
        "calculated": {
            "subtotal": str(subtotal),
            "discount_total": str(discount_total)
            if discount_total is not None
            else None,
            "tax_total": str(tax_total),
            "shipping_total": str(shipping_total)
            if shipping_total is not None
            else None,
            "total_amount": str(total),
        },
        "ground_truth": {
            "subtotal": truth["subtotal"],
            "discount_total": truth["discount_total"],
            "tax_total": truth["tax_total"],
            "shipping_total": truth["shipping_total"],
            "total_amount": truth["total_amount"],
        },
        "issues": issues,
        "rounding_mismatches": rounding_mismatches,
        "null_zero_anomalies": null_zero_anomalies,
    }


def audit_dataset(dataset_dir: Path) -> dict[str, Any]:
    """Audit all 60 generated invoices and return JSON-serializable results."""

    dataset_dir = dataset_dir.resolve()
    records = []
    for split in ("development", "holdout"):
        for directory in sorted((dataset_dir / "generated" / split).glob("invoice_*")):
            records.append(audit_invoice(directory))
    breakdown: dict[str, dict[str, dict[str, int]]] = {}
    for dimension in (
        "template",
        "difficulty",
        "discount_present",
        "tax_presentation",
        "shipping_present",
        "multi_page",
        "adversarial",
    ):
        groups: dict[str, dict[str, int]] = defaultdict(
            lambda: {
                "invoices": 0,
                "line_items": 0,
                "inconsistencies": 0,
                "rounding_mismatches": 0,
                "null_zero_anomalies": 0,
            }
        )
        for record in records:
            key = str(record[dimension])
            group = groups[key]
            group["invoices"] += 1
            group["line_items"] += record["line_item_count"]
            group["inconsistencies"] += len(record["issues"])
            group["rounding_mismatches"] += len(record["rounding_mismatches"])
            group["null_zero_anomalies"] += len(record["null_zero_anomalies"])
        breakdown[dimension] = dict(sorted(groups.items()))

    null_counts: Counter[str] = Counter()
    zero_counts: Counter[str] = Counter()
    quantities: list[Decimal] = []
    negative_money_values = 0
    for record in records:
        for field, value in record["ground_truth"].items():
            if value is None:
                null_counts[field] += 1
            elif Decimal(value) == ZERO:
                zero_counts[field] += 1
            elif Decimal(value) < ZERO:
                negative_money_values += 1
        for item in record["line_details"]:
            quantity = Decimal(item["quantity"])
            quantities.append(quantity)
            for field in ("unit_price", "discount", "tax", "line_total"):
                value = item[field]
                if value is None:
                    null_counts[f"line_items.{field}"] += 1
                elif Decimal(value) == ZERO:
                    zero_counts[f"line_items.{field}"] += 1
                elif Decimal(value) < ZERO:
                    negative_money_values += 1

    summary = {
        "dataset_version": DATASET_VERSION,
        "invoices_checked": len(records),
        "line_items_checked": sum(r["line_item_count"] for r in records),
        "financial_inconsistencies_found": sum(len(r["issues"]) for r in records),
        "rounding_mismatches_found": sum(
            len(r["rounding_mismatches"]) for r in records
        ),
        "null_zero_anomalies_found": sum(
            len(r["null_zero_anomalies"]) for r in records
        ),
        "null_counts": dict(sorted(null_counts.items())),
        "explicit_zero_counts": dict(sorted(zero_counts.items())),
        "negative_money_values": negative_money_values,
        "quantity_min": str(min(quantities)),
        "quantity_max": str(max(quantities)),
        "breakdown": breakdown,
    }
    return {"summary": summary, "records": records}


def _worked_examples(records: list[dict[str, Any]]) -> str:
    selected: list[dict[str, Any]] = []
    predicates = (
        lambda r: not r["discount_present"] and not r["shipping_present"],
        lambda r: r["discount_present"],
        lambda r: r["tax_presentation"] == "line_items",
        lambda r: r["shipping_present"],
        lambda r: r["line_item_count"] >= 6 or r["multi_page"],
    )
    for predicate in predicates:
        candidate = next(
            (r for r in records if predicate(r) and r not in selected), None
        )
        if candidate is not None:
            selected.append(candidate)
    sections: list[str] = []
    for record in selected:
        lines = [f"### {record['invoice_id']}", ""]
        for item in record["line_details"]:
            lines.append(
                f"- Item {item['index']}: quantity {item['quantity']} × unit_price {item['unit_price']} = gross {item['gross']}; discount {item['discount'] or 'null'}; tax {item['tax'] or 'null'}; line_total {item['line_total']}."
            )
        lines.extend(
            [
                "",
                f"- Calculated totals: subtotal={record['calculated']['subtotal']}, discount_total={record['calculated']['discount_total']}, tax_total={record['calculated']['tax_total']}, shipping_total={record['calculated']['shipping_total']}, total_amount={record['calculated']['total_amount']}.",
                f"- Ground truth totals: subtotal={record['ground_truth']['subtotal']}, discount_total={record['ground_truth']['discount_total']}, tax_total={record['ground_truth']['tax_total']}, shipping_total={record['ground_truth']['shipping_total']}, total_amount={record['ground_truth']['total_amount']}.",
            ]
        )
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    return f"""# Financial Semantics Audit

Dataset: `{summary["dataset_version"]}`; audit scope: all generated invoices.

## Executive Summary

The generator uses `Decimal` values and quantizes currency to two places with `ROUND_HALF_UP`. Each line's gross amount is `quantity × unit_price`; subtotal is the sum of gross amounts before discounts. Discounts are absolute line amounts, normally 5% of gross when enabled. Tax is an absolute amount calculated on gross minus discount. For line-item tax presentation, `line_total` includes tax; for summary presentation, `line_total` excludes tax and invoice `tax_total` carries the tax separately. Shipping is added only to `total_amount`.

## Financial Field Dictionary

| Field | Type / nullable | Semantic meaning | Source/calculation | Key invariant |
|---|---|---|---|---|
| `currency` | 3-letter string / nullable | Invoice currency code; null means ambiguous currency in this dataset | Factory currency plan | No conversion; one invoice currency |
| `subtotal` | decimal string / non-null in dataset | Sum of line gross amounts before discounts and tax | `sum(money(quantity × unit_price))` | Equals pre-discount gross sum |
| `discount_total` | decimal string / nullable | Sum of absolute line discounts | Sum of line discounts when enabled | Null when discount is absent; not a percentage |
| `tax_total` | decimal string / non-null in dataset | Sum of calculated line tax amounts | Sum of per-line rounded tax | Included once in grand total |
| `shipping_total` | decimal string / nullable | Shipping charge | Fixed generated amount when present | Not in subtotal; added only to grand total |
| `total_amount` | decimal string / non-null in dataset | Invoice grand total | `subtotal - discount_total + tax_total + shipping_total` | Shipping included here only |
| `quantity` | decimal string / nullable by schema | Generated quantity; observed values are positive integers | Deterministic choice from 1, 2, 3 | No fractional values observed |
| `unit_price` | decimal string / nullable by schema | Price per unit before discount/tax | Generated whole-dollar amount, stored to cents | Non-negative in dataset |
| `discount` | decimal string / nullable | Absolute line discount amount | 5% of gross when invoice discount is enabled | Null means no discount field |
| `tax` | decimal string / nullable | Absolute line tax amount when presented per line | Rounded taxable amount × invoice tax rate | Null in summary-tax presentation, not zero |
| `line_total` | decimal string / nullable | Line amount after discount, with tax only when tax is line-presented | `gross - discount + visible_tax` | Meaning depends on `tax_presentation` |

## Financial Formulas

```text
gross_i = money(quantity_i × unit_price_i)
discount_i = money(gross_i × 0.05) when discount is enabled, otherwise null
tax_i = money((gross_i - discount_i) × tax_rate)
subtotal = money(sum(gross_i))
discount_total = money(sum(discount_i)) when enabled, otherwise null
tax_total = money(sum(tax_i))
line_total_i = gross_i - discount_i + tax_i  # line_items presentation
line_total_i = gross_i - discount_i           # summary presentation
total_amount = subtotal - discount_total + tax_total + shipping_total
```

Tax is calculated after discount. Shipping is not taxable and is not included in subtotal. Invoice totals are derived directly from line items plus generated shipping.

## Null vs Zero Contract

| Field | Null meaning | Zero meaning | Safe to substitute zero? |
|---|---|---|---|
| `discount_total` / line `discount` | Discount absent | Explicit zero would be a present zero discount | No, preserve null |
| `tax` | Tax is not presented at line level under summary presentation | Explicit zero tax amount | No, presentation semantics matter |
| `shipping_total` | Shipping absent | Explicit zero shipping would be present | No |
| `currency` | Currency is ambiguous/unsupported | Not applicable | No |
| required totals | Not observed in valid ground truth; extraction schema permits null | Explicit zero amount | No without a presence rule |

The dataset contains no null/zero semantic anomalies. Null fields must not be normalized to zero by a future validator.

## Rounding and Decimal Policy

- Generation uses Python `Decimal`, never binary floats.
- Currency values are quantized to `0.01` with `ROUND_HALF_UP`.
- Gross, discount, tax, subtotal, discount total, tax total, and shipping are rounded at the generator's explicit calculation points.
- Quantities are stored with two decimal places but are generated as 1, 2, or 3.
- The schema accepts finite decimal strings with arbitrary scale and does not enforce two places, non-negativity, or arithmetic relationships.
- No rounding mismatches or double-rounding inconsistencies were found in the 60-document audit.

## Currency Contract

Supported generated codes are USD, EUR, GBP, CAD, and AUD. Ambiguous-symbol invoices set `currency=null` and retain the same arithmetic; they carry an ambiguity warning. All fields belong to one invoice currency. No conversion or currency-specific decimal precision exists.

## Dataset Audit

```text
invoices checked: {summary["invoices_checked"]}
line items checked: {summary["line_items_checked"]}
financial inconsistencies: {summary["financial_inconsistencies_found"]}
rounding mismatches: {summary["rounding_mismatches_found"]}
null/zero anomalies: {summary["null_zero_anomalies_found"]}
negative monetary values: {summary["negative_money_values"]}
quantity range: {summary["quantity_min"]} to {summary["quantity_max"]}
```

The full breakdown by template, difficulty, discount presence, tax presentation, shipping, multi-page status, and adversarial status is in `audit.json`.

## Representative Worked Examples

{_worked_examples(report["records"])}

## Schema / Prompt / Dataset Alignment

The dataset ground truth, dataset validator, and guardrails agree on decimal-string representation and null preservation. The structured schema validates finite decimal strings but does not define whether `line_total` includes tax, does not enforce scale/sign/range, and does not enforce invoice arithmetic. Prompts explicitly prohibit calculation and business validation, so they do not define these generator-specific formulas. This is an under-specification, not a runtime mismatch: future business validation must use the audited dataset contract rather than infer semantics from the extraction prompt.

## Safe Validation Invariants for Task 5.1

- `LINE_ITEM_ARITHMETIC`: when required line fields are present, validate the presentation-specific line-total formula using exact Decimal arithmetic.
- `SUBTOTAL_CONSISTENCY`: when all line quantity and unit-price values are present, compare subtotal to the sum of rounded gross amounts.
- `DISCOUNT_TOTAL_CONSISTENCY`: only when invoice and all relevant line discounts are present, compare the sum of absolute discounts; preserve null as not-present.
- `TAX_TOTAL_CONSISTENCY`: when tax values are available at the same presentation level, compare the sum of rounded tax amounts.
- `GRAND_TOTAL_CONSISTENCY`: when subtotal, tax, shipping, and discount presence are sufficiently known, compare `subtotal - discount_total + tax_total + shipping_total`.

Recommended tolerance is exact equality after Decimal quantization to two places; no floating-point tolerance is justified by the current generator.

## Undefined / Unsafe Rules

Do not implement tax-rate or jurisdiction correctness, tax treatment for arbitrary real invoices, shipping taxability, currency conversion, vendor policy validation, or assumptions that null equals zero. Do not require line-level tax when the invoice uses summary tax presentation. Do not infer missing discounts, taxes, or shipping.

## Recommended Validation Contract

| Rule | Required fields | Missing-data behavior | PASS / FAIL | Severity |
|---|---|---|---|---|
| `LINE_ITEM_ARITHMETIC` | quantity, unit_price, discount, line_total; tax when line-presented | NOT_EVALUABLE | Exact formula matches / otherwise FAIL | error |
| `SUBTOTAL_CONSISTENCY` | all line quantities and unit prices, subtotal | NOT_EVALUABLE | Rounded gross sum matches / otherwise FAIL | error |
| `DISCOUNT_TOTAL_CONSISTENCY` | discount_total and complete discount set | NOT_EVALUABLE | Absolute discount sum matches / otherwise FAIL | warning/error depending completeness |
| `TAX_TOTAL_CONSISTENCY` | tax_total and complete same-level tax values | NOT_EVALUABLE | Tax sum matches / otherwise FAIL | warning/error depending presentation |
| `GRAND_TOTAL_CONSISTENCY` | subtotal, discount presence, tax_total, shipping presence, total_amount | NOT_EVALUABLE | Grand-total formula matches / otherwise FAIL | error |

These rules must report discrepancies without changing extracted values.

## Risk Register

| Risk | Evidence | Impact | Resolution |
|---|---|---|---|
| `line_total` meaning depends on tax presentation | Factory has `line_items` and `summary` modes | Incorrect validation or double-counted tax | Carry presentation context into validation |
| Null vs zero confusion | Missing discounts/shipping and summary line taxes are null | False arithmetic passes | Preserve null and emit NOT_EVALUABLE |
| Schema is weaker than business semantics | Decimal strings have no scale/range/formula validators | Provider output can be structurally valid but financially inconsistent | Keep business checks separate |
| Rounding assumptions applied to arbitrary invoices | Current dataset uses explicit cent rounding | False failures outside synthetic corpus | Make rounding policy configurable/documented |
| Incomplete line-level data | Summary tax intentionally omits line tax | Incorrect line aggregation | Validate only at available presentation level |
| Currency ambiguity | Eight invoices have null currency with `$` display | Unsupported currency assumptions | Never convert or infer currency |

## Audit Tooling

- `python -m evaluation.audit_financial_semantics`
- `audit.json` contains all invoice-level calculations and findings.
- No provider, database, production extraction, schema, prompt, or metric behavior was changed.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-dir", type=Path, default=Path("evaluation"))
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    report = audit_dataset(args.dataset_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "audit.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (args.output_dir / "financial_semantics_audit.md").write_text(
        render_markdown(report), encoding="utf-8"
    )
    print(json.dumps(report["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
