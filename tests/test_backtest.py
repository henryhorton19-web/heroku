"""The backtest — P3's mechanism.

Two properties matter more than the arithmetic: it **refuses below the sample floor**,
and it measures error against **`est_p25`**, the figure the buy side actually scores
on. Backtesting a number you do not trade against would be measuring the wrong thing
accurately.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arb.comps.backtest import (
    MAX_MEDIAN_ERROR,
    MIN_LABELLED_ITEMS,
    LabelledItem,
    backtest,
)


def _item(est: int, realised: int, ident: str = "x") -> LabelledItem:
    return LabelledItem(
        external_id=ident,
        est_p25_pence=est,
        realised_pence=realised,
        comp_n=8,
        est_confidence=0.8,
    )


def _sample(count: int, *, est: int = 4000, realised: int = 4000) -> list[LabelledItem]:
    return [_item(est, realised, f"i{i}") for i in range(count)]


# ---------------------------------------------------------------- refusing


def test_a_thin_sample_refuses() -> None:
    """A 15% error measured on eleven items is not a result. Reporting one would be
    worse than reporting nothing, because it would carry the authority of a
    measurement."""
    assert backtest(_sample(11)) is None


def test_the_floor_is_the_documented_one() -> None:
    assert backtest(_sample(MIN_LABELLED_ITEMS - 1)) is None
    assert backtest(_sample(MIN_LABELLED_ITEMS)) is not None


def test_an_empty_sample_refuses() -> None:
    assert backtest([]) is None


# ---------------------------------------------------------------- accuracy


def test_perfect_estimates_pass() -> None:
    result = backtest(_sample(MIN_LABELLED_ITEMS))
    assert result is not None
    assert result.median_absolute_error == 0
    assert result.passed


def test_estimates_beyond_tolerance_fail() -> None:
    """The documented response is a second comps source, not a wider tolerance."""
    result = backtest(_sample(MIN_LABELLED_ITEMS, est=6000, realised=4000))
    assert result is not None
    assert not result.passed


def test_the_tolerance_boundary_is_inclusive() -> None:
    result = backtest(_sample(MIN_LABELLED_ITEMS, est=4600, realised=4000))
    assert result is not None
    assert result.median_absolute_error == Decimal("0.15")
    assert result.passed


# ---------------------------------------------------------------- direction


def test_signed_error_is_positive_when_estimates_run_high() -> None:
    """Optimistic estimates are the dangerous direction: they turn a plausible margin
    into a loss."""
    result = backtest(_sample(MIN_LABELLED_ITEMS, est=5000, realised=4000))
    assert result is not None
    assert result.median_signed_error > 0


def test_signed_error_is_negative_when_estimates_run_low() -> None:
    result = backtest(_sample(MIN_LABELLED_ITEMS, est=3000, realised=4000))
    assert result is not None
    assert result.median_signed_error < 0


def test_consistent_overestimation_reads_as_bias() -> None:
    """12% out in both directions is noise. 12% high every time is bias, and bias has
    a fix."""
    result = backtest(_sample(MIN_LABELLED_ITEMS, est=4400, realised=4000))
    assert result is not None
    assert result.is_biased


def test_symmetric_error_does_not_read_as_bias() -> None:
    half = MIN_LABELLED_ITEMS // 2
    items = [
        *[_item(4400, 4000, f"h{i}") for i in range(half)],
        *[_item(3600, 4000, f"l{i}") for i in range(MIN_LABELLED_ITEMS - half)],
    ]
    result = backtest(items)
    assert result is not None
    assert not result.is_biased


def test_within_tolerance_counts_items_not_the_median() -> None:
    """A passing median can hide a long tail, so the count is reported too."""
    items = [
        *[_item(4000, 4000, f"good{i}") for i in range(60)],
        *[_item(9000, 4000, f"bad{i}") for i in range(40)],
    ]
    result = backtest(items)
    assert result is not None
    assert result.passed
    assert result.within_tolerance == 60


# ---------------------------------------------------------------- edges


def test_a_zero_realised_price_is_refused() -> None:
    """Free is a data error, not a 100% error. Dividing by it would produce a number."""
    item = _item(4000, 0)
    with pytest.raises(ValueError, match="positive"):
        _ = item.signed_error


@given(
    est=st.integers(min_value=0, max_value=1_000_000),
    realised=st.integers(min_value=1, max_value=1_000_000),
)
def test_absolute_error_is_never_negative(est: int, realised: int) -> None:
    assert _item(est, realised).absolute_error >= 0


@given(realised=st.integers(min_value=1, max_value=1_000_000))
def test_an_exact_estimate_has_no_error(realised: int) -> None:
    assert _item(realised, realised).absolute_error == 0


def test_the_gate_constants_match_the_build_plan() -> None:
    """If these move, someone has tuned the threshold rather than the valuation."""
    assert MIN_LABELLED_ITEMS == 100
    assert Decimal("0.15") == MAX_MEDIAN_ERROR
