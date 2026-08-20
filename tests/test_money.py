"""Money parsing. Property-tested because a rounding bug here is silent and cumulative."""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arb.money import CurrencyMismatchError, parse_pence, pence_to_decimal, percentage_of_pence


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("899.99", 89999),
        ("0.00", 0),
        ("45.00", 4500),
        ("5", 500),
        ("£12.50", 1250),
        ("1,299.00", 129900),
        (" 7.05 ", 705),
        ("0.125", 13),
    ],
)
def test_parse_pence_examples(raw: str, expected: int) -> None:
    assert parse_pence(raw) == expected


def test_parse_pence_rounds_half_up_not_bankers() -> None:
    """Python rounds 0.125 to 0.12 by default. Invoices do not."""
    assert parse_pence("0.125") == 13
    assert parse_pence("0.135") == 14


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_values_are_none(raw: str | None) -> None:
    assert parse_pence(raw) is None


@pytest.mark.parametrize("raw", ["abc", "12.3.4", "NaN", "Infinity"])
def test_unparseable_values_raise(raw: str) -> None:
    with pytest.raises(ValueError, match="money value"):
        parse_pence(raw)


def test_non_gbp_currency_is_refused_not_converted() -> None:
    """A USD comp in a GBP valuation is a wrong answer wearing the right units."""
    with pytest.raises(CurrencyMismatchError, match="USD"):
        parse_pence("899.99", currency="USD")
    assert parse_pence("899.99", currency="gbp") == 89999


@given(st.integers(min_value=0, max_value=10_000_000))
def test_pence_decimal_round_trip(pence: int) -> None:
    assert parse_pence(pence_to_decimal(pence)) == pence


@given(st.integers(min_value=0, max_value=1_000_000))
def test_parse_pence_never_returns_a_float(pence: int) -> None:
    parsed = parse_pence(pence_to_decimal(pence))
    assert parsed is not None
    assert isinstance(parsed, int)
    assert not isinstance(parsed, bool)


@given(
    st.integers(min_value=0, max_value=1_000_000),
    st.integers(min_value=0, max_value=1_000_000),
)
def test_parse_pence_is_monotonic(a: int, b: int) -> None:
    pa, pb = parse_pence(pence_to_decimal(a)), parse_pence(pence_to_decimal(b))
    assert pa is not None
    assert pb is not None
    assert (a <= b) == (pa <= pb)


@given(st.integers(min_value=0, max_value=1_000_000))
def test_zero_rate_costs_nothing(pence: int) -> None:
    assert percentage_of_pence(pence, Decimal(0)) == 0


@given(st.integers(min_value=0, max_value=1_000_000))
def test_full_rate_costs_everything(pence: int) -> None:
    assert percentage_of_pence(pence, Decimal(1)) == pence
