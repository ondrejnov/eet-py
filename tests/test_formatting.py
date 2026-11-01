from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from eet import ValidationError, formatting

PRAGUE = ZoneInfo("Europe/Prague")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # Examples of the correct and forbidden representations from chapter 3.3.3.12 of the specification.
        (250, "250.00"),
        (-187.2, "-187.20"),
        ("0.56", "0.56"),
        ("020.45", "20.45"),
        (0, "0.00"),
        (-0.0, "0.00"),
        (-0.001, "0.00"),
        (".20", "0.20"),
        ("-00100.00", "-100.00"),
        ("-00.00", "0.00"),
        (-0.01, "-0.01"),
        (99999999.99, "99999999.99"),
        ("-99999999.99", "-99999999.99"),
        (Decimal("3264.50"), "3264.50"),
        (Decimal("-0.004"), "0.00"),
        (None, None),
        # A float is taken by its shortest representation and half a cent goes away from zero.
        (1.005, "1.01"),
        (2.675, "2.68"),
        (-2.675, "-2.68"),
        (0.1 + 0.2, "0.30"),
        ("1.005", "1.01"),
        (Decimal("1.005"), "1.01"),
    ],
)
def test_price(value, expected):
    assert formatting.price(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        100000000,
        -100000000,
        99999999.999,
        "1e300",
        1e300,
        Decimal("1E+400"),
        pytest.param(10**5000, id="extremely long int"),
        "12,50",
        "abc",
        "",
        " 12",
        "12\n",
        "1_000",
        "1e3",
        "١٢",  # non-ASCII digits are valid for Decimal() and int()
        "NaN",
        "Infinity",
        float("nan"),
        float("inf"),
        Decimal("NaN"),
        Decimal("sNaN"),
        Decimal("Infinity"),
        True,
        [1],
    ],
)
def test_invalid_price(value):
    with pytest.raises(ValidationError):
        formatting.price(value)


def test_price_is_independent_of_the_decimal_context():
    import decimal

    with decimal.localcontext() as context:
        context.prec = 2
        context.traps[decimal.Inexact] = True
        assert formatting.price("1234.565") == "1234.57"


def test_date_time():
    assert formatting.date_time(datetime(2027, 1, 9, 4, 25, 28, 123456, PRAGUE)) == "2027-01-09T04:25:28+01:00"
    assert formatting.date_time(datetime(2027, 6, 9, 5, 25, 28, tzinfo=PRAGUE)) == "2027-06-09T05:25:28+02:00"
    assert formatting.date_time(datetime(2027, 6, 9, 3, 25, 28, tzinfo=timezone.utc)) == "2027-06-09T03:25:28+00:00"
    assert formatting.date_time(datetime(2027, 6, 9, 3, 25, tzinfo=timezone(timedelta(hours=-5)))) == "2027-06-09T03:25:00-05:00"


def test_date_time_in_the_repeated_hour_of_the_autumn_clock_change():
    assert formatting.date_time(datetime(2027, 10, 31, 2, 30, tzinfo=PRAGUE)) == "2027-10-31T02:30:00+02:00"
    assert formatting.date_time(datetime(2027, 10, 31, 2, 30, fold=1, tzinfo=PRAGUE)) == "2027-10-31T02:30:00+01:00"


@pytest.mark.parametrize(
    "value",
    [
        datetime(2027, 1, 9, 4, 25, 28),  # naive, its time zone would be a guess
        datetime(1890, 1, 1, tzinfo=PRAGUE),  # local mean time with seconds in the offset
        date(2027, 1, 9),
        "2027-01-09T04:25:28+01:00",
    ],
)
def test_invalid_date_time(value):
    with pytest.raises(ValidationError):
        formatting.date_time(value)


def test_boolean():
    assert formatting.boolean(True) == "true"
    assert formatting.boolean(False) == "false"
