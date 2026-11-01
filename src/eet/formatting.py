"""Formatting of values to the only representations accepted by EET."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Context, Decimal

from .exceptions import ValidationError

Amount = int | float | str | Decimal

#: CastkaType mask from the XSD. The character classes are spelled out, \d also matches non-ASCII digits.
_PRICE_MASK = re.compile(r"(0|-?[1-9][0-9]{0,7})\.[0-9][0-9]|-0\.(0[1-9]|[1-9][0-9])")

#: Plain decimal notation. Decimal() itself would also accept "1_000", "NaN", "1E3" or " 1 ".
_NUMBER_MASK = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)")

#: Mask of tns:dateTime from the XSD. It refuses an offset with seconds, e.g. +00:57:44 of old Prague dates.
_DATE_TIME_MASK = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}[+-][0-9]{2}:[0-9]{2}")

_CENT = Decimal("0.01")
_ROUNDING_LIMIT = Decimal(1_000_000_000)


def _to_decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):  # bool is a subclass of int
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # The shortest representation, 1.005 means 1.005 and not 1.00499999999999989...
        # float.__repr__ is used because subclasses (numpy.float64) may print something else.
        return Decimal(float.__repr__(value))
    if isinstance(value, str) and _NUMBER_MASK.fullmatch(value):
        return Decimal(value)
    return None


def price(value: Amount | None) -> str | None:
    """Format an amount, e.g. 1000 => "1000.00". Half a cent is rounded away from zero.

    Raises ValidationError when the value is not a number or is out of the allowed range.
    """
    if value is None:
        return None

    number = _to_decimal(value)
    if number is None or not number.is_finite():
        raise ValidationError([f"Amount is not a number: {value!r}"])

    if number.copy_abs() >= _ROUNDING_LIMIT:  # quantize() of a huge number would fail on precision
        # The value is not printed: Python refuses to convert an extremely long int to a string.
        raise ValidationError(["Amount is out of the allowed range."])

    rounded = number.quantize(_CENT, rounding=ROUND_HALF_UP, context=Context())
    formatted = format(rounded, "f")
    if formatted == "-0.00":
        formatted = "0.00"

    if not _PRICE_MASK.fullmatch(formatted):
        raise ValidationError([f"Amount is out of the allowed range: {formatted}"])

    return formatted


def date_time(value: datetime) -> str:
    """Format a date as ISO 8601 with a mandatory UTC offset and without fractional seconds.

    Raises ValidationError for a naive datetime, whose time zone would have to be guessed.
    """
    if not isinstance(value, datetime):
        raise ValidationError([f"must be an instance of datetime, not {type(value).__name__}"])

    if value.utcoffset() is None:
        raise ValidationError(["must be timezone-aware, e.g. datetime.now(ZoneInfo('Europe/Prague'))"])

    formatted = value.isoformat(timespec="seconds")
    if not _DATE_TIME_MASK.fullmatch(formatted):
        raise ValidationError([f"has an unsupported format: {formatted}"])

    return formatted


def boolean(value: bool) -> str:
    return "true" if value else "false"
