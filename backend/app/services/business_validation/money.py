"""Shared Decimal arithmetic for the supported benchmark contract."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")


def parse_money(value: str | None) -> Decimal | None:
    """Parse a finite decimal string without changing its value."""

    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def quantize_money(value: Decimal) -> Decimal:
    """Quantize using the audited two-place ROUND_HALF_UP policy."""

    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, ".2f")


def difference(actual: Decimal, expected: Decimal) -> Decimal:
    return quantize_money(actual - expected)
