"""Conservative, schema-aware comparison helpers for invoice evaluation."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Callable


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    return " ".join(str(value).strip().casefold().split())


def normalize_id(value: Any) -> str | None:
    return None if value is None else str(value).strip().casefold()


def parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def normalize_currency(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip().upper()
    return value if re.fullmatch(r"[A-Z]{3}", value) else None


def compare_value(expected: Any, predicted: Any, kind: str) -> tuple[bool, str | None]:
    if expected is None:
        return (
            predicted is None,
            "hallucinated_value" if predicted is not None else None,
        )
    if predicted is None:
        return False, "missing_value"
    normalizers: dict[str, Callable[[Any], Any]] = {
        "text": normalize_text,
        "id": normalize_id,
        "date": parse_date,
        "currency": normalize_currency,
        "decimal": parse_decimal,
    }
    normalizer = normalizers[kind]
    actual = normalizer(predicted)
    wanted = normalizer(expected)
    if actual is None:
        return False, "invalid_format"
    return actual == wanted, None if actual == wanted else "wrong_value"


def compare_field(expected: Any, predicted: Any, field: str) -> tuple[bool, str | None]:
    if field in {"vendor_name", "description"}:
        kind = "text"
    elif field in {"invoice_number", "purchase_order_number", "vendor_tax_id"}:
        kind = "id"
    elif field in {"invoice_date", "due_date"}:
        kind = "date"
    elif field == "currency":
        kind = "currency"
    else:
        kind = "decimal"
    return compare_value(expected, predicted, kind)
