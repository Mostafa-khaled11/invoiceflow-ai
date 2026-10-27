# Deterministic Business Validation v1

This package validates `StructuredInvoiceData` against the currently supported
InvoiceFlow benchmark financial contract. It is not a universal accounting or
tax engine.

Ruleset version: `invoice-validation-v1`.

## Statuses and decisions

Rule statuses are `PASS`, `FAIL`, and `NOT_EVALUABLE`. `NOT_EVALUABLE` is a
first-class result: it means required evidence is unavailable and is never
silently treated as success, failure, or zero.

Final decisions are:

- `PASS`: no failures or review-relevant unevaluable rules.
- `WARNING`: only warning-level unevaluable results remain. Informational
  unevaluable rules, such as an optional missing due date, do not change a
  passing decision.
- `REVIEW_REQUIRED`: a rule failed or a high/critical rule cannot be evaluated.
- `BLOCKED`: reserved for a result explicitly marked `metadata.blocking=true`;
  v1 rules do not use this path for ordinary financial mismatches.

## Rule catalog

| ID | Rule | Purpose |
|---|---|---|
| FIN001 | `LINE_ITEM_ARITHMETIC` | Validates each line using the derived tax presentation. |
| FIN002 | `SUBTOTAL_CONSISTENCY` | Compares subtotal with rounded gross line sum. |
| FIN003 | `DISCOUNT_TOTAL_CONSISTENCY` | Validates absolute line discounts, including proven absence. |
| FIN004 | `TAX_TOTAL_CONSISTENCY` | Validates complete line-level tax only. |
| FIN005 | `GRAND_TOTAL_CONSISTENCY` | Validates the grand-total formula using proven absent optional components as zero contributions. |
| FIN006 | `FINANCIAL_VALUE_INTEGRITY` | Rejects negative values outside the supported MVP contract. |
| VAL001 | `REQUIRED_FIELDS` | Checks critical fields and supported ambiguous currency. |
| VAL002 | `DATE_LOGIC` | Checks due date is on or after invoice date. |

All rules use `Decimal`, cent quantization, and `ROUND_HALF_UP`. No rule
changes the input model or repairs extracted values.

## Null and tax presentation policy

Null is preserved in the input model. Under the audited benchmark contract,
these specific proven absence values may contribute zero internally without
being rewritten:

- null line discount → effective discount contribution `0.00`;
- null `discount_total` → effective invoice discount contribution `0.00`;
- null `shipping_total` → effective shipping contribution `0.00`.

This is rule-specific absence semantics, not global null coercion. Required
operands such as subtotal, tax total, total amount, quantity, and unit price
remain `NOT_EVALUABLE` when missing.

If `discount_total` is null while any line discount is present, FIN003 fails for
contradictory evidence. If a non-null discount total has incomplete line
discount evidence, FIN003 is `NOT_EVALUABLE`.

Tax presentation is derived deterministically:

- all line taxes present → `LINE_ITEM`;
- all line taxes absent and invoice tax total present → `SUMMARY`;
- mixed or insufficient evidence → `UNKNOWN`.

Summary tax is not inferred into line items. Currency null with the existing
`ambiguous_currency` warning is accepted as supported ambiguity. FIN004 remains
`NOT_EVALUABLE` for summary-level tax because no same-level line tax evidence is
available.

## Limitations

The engine does not infer tax rates, tax jurisdictions, shipping taxability,
currency conversions, vendor policies, payment terms, or missing values. It
does not persist reports or expose an API endpoint; those are future tasks.
