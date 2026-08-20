"""Valuation tests. The refusal path matters as much as the estimate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arb.comps.valuation import trim_outliers, value
from arb.models import SoldObservation

T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _obs(
    price: int,
    *,
    upper_bound: bool = False,
    days: int | None = None,
) -> SoldObservation:
    listed = T0 if days is not None else None
    sold = T0 + timedelta(days=days) if days is not None else None
    return SoldObservation(
        brand_norm="nike",
        title_norm="nike air max 90",
        size_norm="M",
        price_pence=price,
        price_is_upper_bound=upper_bound,
        listed_at=listed,
        sold_at=sold,
    )


def _many(prices: list[int]) -> list[SoldObservation]:
    return [_obs(p) for p in prices]


# ------------------------------------------------------------------ refusal


def test_returns_none_below_the_comp_floor() -> None:
    """Refusing is a valid outcome. A missed opportunity costs nothing."""
    assert value(_many([1000, 1100]), min_comp_n=3, match_confidence=0.9) is None


def test_returns_none_on_empty_input() -> None:
    assert value([], min_comp_n=3, match_confidence=0.9) is None


def test_returns_a_valuation_once_the_floor_is_met() -> None:
    result = value(_many([1000, 1100, 1200]), min_comp_n=3, match_confidence=0.9)
    assert result is not None
    assert result.comp_n == 3


# ------------------------------------------------------------------ best offer bias


def test_best_offer_sales_are_excluded_by_default() -> None:
    """SoldComps reports the LISTED price on a Best Offer sale, not the realised one.
    Including them biases every estimate upward, which is how you overpay."""
    real = _many([1000, 1000, 1000])
    inflated = [*real, _obs(9000, upper_bound=True), _obs(9500, upper_bound=True)]
    baseline = value(real, min_comp_n=3, match_confidence=0.9)
    with_offers = value(inflated, min_comp_n=3, match_confidence=0.9)
    assert baseline is not None
    assert with_offers is not None
    assert with_offers.est_p60_pence == baseline.est_p60_pence


def test_common_best_offer_sales_skew_the_estimate_past_what_trimming_catches() -> None:
    """The case that actually matters, and the reason the flag exists.

    Outlier trimming removes a lone inflated row, so a single Best Offer sale looks
    harmless. Once they are a third of the set -- ordinary for fashion, where Best
    Offer is standard -- they are no longer outliers, they are the distribution, and
    every estimate drawn from it is biased upward."""
    real = _many([1000, 1020, 1040, 1060, 1080, 1100])
    offers = [_obs(p, upper_bound=True) for p in (1500, 1550, 1600)]
    honest = value(real + offers, min_comp_n=3, match_confidence=0.9)
    naive = value(
        real + offers, min_comp_n=3, match_confidence=0.9, include_upper_bound_prices=True
    )
    assert honest is not None
    assert naive is not None
    assert naive.est_p60_pence > honest.est_p60_pence
    assert naive.comp_n > honest.comp_n


def test_excluding_best_offers_can_drop_below_the_floor() -> None:
    """Correct: three upper-bound rows are not three comps."""
    obs = [_obs(1000), _obs(9000, upper_bound=True), _obs(9500, upper_bound=True)]
    assert value(obs, min_comp_n=3, match_confidence=0.9) is None


def test_best_offers_can_be_re_admitted_for_measurement() -> None:
    """The flag exists to size the bias against realised data, not for normal use."""
    obs = [_obs(1000), _obs(9000, upper_bound=True), _obs(9500, upper_bound=True)]
    result = value(obs, min_comp_n=3, match_confidence=0.9, include_upper_bound_prices=True)
    assert result is not None
    assert result.comp_n == 3


# ------------------------------------------------------------------ percentiles


def test_p25_is_at_or_below_p60() -> None:
    result = value(_many([100, 200, 300, 400, 500, 600]), min_comp_n=3, match_confidence=0.9)
    assert result is not None
    assert result.est_p25_pence <= result.est_p60_pence


@given(
    st.lists(st.integers(min_value=1, max_value=100_000), min_size=4, max_size=60),
    st.floats(min_value=0.0, max_value=1.0),
)
def test_percentiles_always_ordered_and_in_range(prices: list[int], conf: float) -> None:
    result = value(_many(prices), min_comp_n=1, match_confidence=conf)
    assert result is not None
    assert result.est_p25_pence <= result.est_p60_pence
    assert min(prices) <= result.est_p25_pence <= max(prices)


def test_outliers_are_trimmed_before_percentiles() -> None:
    tight = [1000, 1010, 1020, 1030, 1040, 1050]
    assert trim_outliers([*tight, 500_000]) == sorted(tight)


def test_tiny_sets_pass_through_untrimmed() -> None:
    """With three points the quartiles are the points; fencing would discard signal."""
    assert trim_outliers([100, 200, 90_000]) == [100, 200, 90_000]


# ------------------------------------------------------------------ confidence


def test_confidence_rises_with_comp_count() -> None:
    few = value(_many([1000] * 3), min_comp_n=3, match_confidence=0.9)
    many = value(_many([1000] * 40), min_comp_n=3, match_confidence=0.9)
    assert few is not None
    assert many is not None
    assert many.est_confidence > few.est_confidence


def test_confidence_falls_as_the_set_spreads() -> None:
    tight = value(_many([1000, 1010, 1020, 1030, 1040]), min_comp_n=3, match_confidence=0.9)
    loose = value(_many([200, 900, 1500, 2600, 5000]), min_comp_n=3, match_confidence=0.9)
    assert tight is not None
    assert loose is not None
    assert tight.est_confidence > loose.est_confidence


def test_three_comps_never_looks_trustworthy() -> None:
    """The documented floor should not produce a number anyone would act on blind."""
    result = value(_many([1000, 1000, 1000]), min_comp_n=3, match_confidence=1.0)
    assert result is not None
    assert result.est_confidence < 0.25


def test_match_confidence_is_passed_through_not_blended() -> None:
    """A tight set matched badly and a loose set matched well are different problems."""
    result = value(_many([1000] * 20), min_comp_n=3, match_confidence=0.31)
    assert result is not None
    assert result.match_confidence == pytest.approx(0.31)


# ------------------------------------------------------------------ days to sell


def test_days_to_sell_is_none_when_no_comp_carries_dates() -> None:
    """The normal case for eBay: the sold endpoint has no listing-start date."""
    result = value(_many([1000, 1100, 1200]), min_comp_n=3, match_confidence=0.9)
    assert result is not None
    assert result.days_to_sell_p50 is None


def test_days_to_sell_is_the_median_of_what_is_known() -> None:
    obs = [_obs(1000, days=2), _obs(1100, days=10), _obs(1200, days=30)]
    result = value(obs, min_comp_n=3, match_confidence=0.9)
    assert result is not None
    assert result.days_to_sell_p50 == pytest.approx(10.0)


def test_min_comp_n_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        value(_many([1000]), min_comp_n=0, match_confidence=0.9)
