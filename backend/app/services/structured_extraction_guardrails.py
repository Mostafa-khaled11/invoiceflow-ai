"""Narrow deterministic guardrails before structured invoice validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

MONEY_FIELDS = (
    "subtotal",
    "discount_total",
    "tax_total",
    "shipping_total",
    "total_amount",
)
LINE_MONEY_FIELDS = ("unit_price", "discount", "tax", "line_total")
_KNOWN_CURRENCY_CODES = frozenset(
    {"AUD", "CAD", "CHF", "CNY", "EUR", "GBP", "INR", "JPY", "USD"}
)
_CURRENCY_SYMBOLS = frozenset({"$", "€", "£", "¥"})
_CURRENCY_SYMBOLS = frozenset({"$", "\u20ac", "\u00a3", "\u00a5"})
_DATE_LABELS = {
    "invoice_date": r"(?:invoice\s+date|document\s+date)",
    "due_date": r"due\s+date",
}
_DATE_VALUE_RE = re.compile(
    r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|"
    r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}|"
    r"[A-Za-z]{3,9}\s+\d{1,2},\s+\d{4})"
)


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    """Canonicalized provider data and safe observability metadata."""

    data: Any
    normalized_fields: tuple[str, ...]
    fallback_fields: tuple[str, ...]


def canonicalize_monetary_value(
    value: str | None, *, currency: str | None = None
) -> str | None:
    """Normalize safe monetary representation, or return the original value.

    Returning an unsafe non-null value unchanged deliberately lets the strict
    schema/semantic validation reject it instead of guessing from arbitrary text.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        return value
    original = value
    text = value.strip()
    if not text:
        return original
    sign = ""
    if text[0] in "+-":
        sign, text = text[0], text[1:].strip()
    prefix, text = _strip_currency_token(text, currency)
    if prefix is None:
        return original
    suffix, text = _strip_currency_token(text, currency, suffix=True)
    if suffix is None:
        return original
    if not text:
        return original
    if "," in text:
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
            return original
        text = text.replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return original
    return f"{sign}{text}"


def apply_structured_extraction_guardrails(
    provider_data: Any, source_text: str
) -> GuardrailResult:
    """Apply monetary canonicalization and null-only labeled date fallback."""

    if not isinstance(provider_data, dict):
        return GuardrailResult(provider_data, (), ())
    data = dict(provider_data)
    normalized: list[str] = []
    fallback: list[str] = []
    currency = data.get("currency") if isinstance(data.get("currency"), str) else None
    for field in MONEY_FIELDS:
        value = data.get(field)
        canonical = canonicalize_monetary_value(value, currency=currency)
        if canonical != value:
            data[field] = canonical
            normalized.append(field)
    line_items = data.get("line_items")
    if isinstance(line_items, list):
        copied_items: list[Any] = []
        for index, item in enumerate(line_items):
            if not isinstance(item, dict):
                copied_items.append(item)
                continue
            copied = dict(item)
            for field in LINE_MONEY_FIELDS:
                value = copied.get(field)
                canonical = canonicalize_monetary_value(value, currency=currency)
                if canonical != value:
                    copied[field] = canonical
                    normalized.append(f"line_items[{index}].{field}")
            copied_items.append(copied)
        data["line_items"] = copied_items
    for field in ("invoice_date", "due_date"):
        if data.get(field) is None:
            recovered = extract_labeled_date(source_text, field)
            if recovered is not None:
                data[field] = recovered
                fallback.append(field)
    return GuardrailResult(data, tuple(normalized), tuple(fallback))


def extract_labeled_date(source_text: str, field: str) -> str | None:
    """Return one confidently parsed date associated with an explicit label."""

    label = _DATE_LABELS.get(field)
    if label is None:
        return None
    matches = re.findall(rf"(?im)^\s*{label}\s*[:#-]\s*(.+?)\s*$", source_text)
    if len(matches) != 1:
        return None
    value_match = _DATE_VALUE_RE.fullmatch(matches[0].strip())
    if value_match is None:
        return None
    return _parse_explicit_date(value_match.group(0))


def _strip_currency_token(
    text: str, currency: str | None, *, suffix: bool = False
) -> tuple[bool | None, str]:
    token_pattern = r"(?:\$|€|£|¥|[A-Z]{3})"
    token_pattern = r"(?:\$|\u20ac|\u00a3|\u00a5|[A-Z]{3})"
    match = (
        re.search(rf"\s*({token_pattern})\s*$", text)
        if suffix
        else re.match(rf"^({token_pattern})\s*", text)
    )
    if match is None:
        return True, text
    token = match.group(1)
    if token.isalpha() and token not in _KNOWN_CURRENCY_CODES:
        return None, text
    if token.isalpha() and currency is not None and token != currency.upper():
        return None, text
    if token in _CURRENCY_SYMBOLS:
        return True, text[: match.start()] if suffix else text[match.end() :]
    return True, text[: match.start()] if suffix else text[match.end() :]


def _parse_explicit_date(value: str) -> str | None:
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    if re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
        day, month, year = (int(part) for part in value.split("/"))
        if day <= 12 or month > 12:
            return None
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return None
    for pattern in ("%d %b %Y", "%d %B %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(value, pattern).date().isoformat()
        except ValueError:
            continue
    return None
