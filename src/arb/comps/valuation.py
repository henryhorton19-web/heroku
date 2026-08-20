"""Valuation: what an item sells for, how fast, and how much to trust the answer.

`value()` returns `None` rather than a weak estimate. A missed opportunity costs
nothing; a confident wrong number costs the trade. Every threshold here is set for
precision, and none of them should be relaxed to make a buy list look fuller.

Three decisions worth stating, because each one is a place the number could be
quietly wrong:

**Best Offer sales are excluded by default.** SoldComps documents that when
`bestOfferAccepted` is true, `soldPrice` is the listed price and eBay never
discloses what the item actually sold for. Those rows are upper bounds. Fashion
leans heavily on Best Offer, so including them at face value biases p25 and p60
upward -- and an upward-biased valuation is precisely what makes you overpay for
stock that then will not clear.

**Outliers are trimmed before percentiles, using Tukey fences.** Sold sets contain
mispriced lots, bundles, and the occasional wrong-item match that survived scoring.

**`days_to_sell_p50` is usually `None`, and that is honest.** The eBay sold endpoint
returns `endedAt` with no listing-start date, so time-to-sell cannot be derived from
a comp on its own. It becomes available once the append-only cache has seen an item
active and then sold, which is a measurement that accrues rather than one you can
fetch.
"""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING

from arb.models import Valuation

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arb.models import SoldObservation

__all__ = [
    "DISPERSION_CAP",
    "MIN_POINTS_TO_FENCE",
    "SAMPLE_HALF_WEIGHT",
    "trim_outliers",
    "value",
]

SAMPLE_HALF_WEIGHT = 12
"""Comp count at which sample confidence reaches 0.5. Chosen so three comps -- the
documented floor -- scores ~0.2 rather than anything that reads as trustworthy."""

MIN_POINTS_TO_FENCE = 4
"""Below this, the quartiles *are* the data points, so Tukey fencing would either do
nothing or discard real observations as if they were noise."""

DISPERSION_CAP = 1.0
"""IQR/median at or above which spread confidence hits zero. A set whose
interquartile range equals its median is not describing one product."""


def trim_outliers(prices: Sequence[int]) -> list[int]:
    """Drop points outside the Tukey fences. Sets of three or fewer pass through.

    With very few points the quartiles are the points, so fencing would either do
    nothing or discard real data on noise.
    """
    if len(prices) < MIN_POINTS_TO_FENCE:
        return sorted(prices)
    ordered = sorted(prices)
    q1, _, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
    iqr = q3 - q1
    low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    return [p for p in ordered if low <= p <= high]


def _percentile(ordered: Sequence[int], pct: int) -> int:
    """Percentile by linear interpolation. `ordered` must be sorted and non-empty."""
    if len(ordered) == 1:
        return ordered[0]
    cuts = statistics.quantiles(ordered, n=100, method="inclusive")
    return round(cuts[pct - 1])


def _sample_confidence(n: int) -> float:
    return n / (n + SAMPLE_HALF_WEIGHT)


def _spread_confidence(ordered: Sequence[int]) -> float:
    """1.0 for a tight set, 0.0 once dispersion reaches `DISPERSION_CAP`."""
    median = statistics.median(ordered)
    if median <= 0:
        return 0.0
    if len(ordered) < MIN_POINTS_TO_FENCE:
        spread = (max(ordered) - min(ordered)) / median
    else:
        q1, _, q3 = statistics.quantiles(ordered, n=4, method="inclusive")
        spread = (q3 - q1) / median
    return max(0.0, min(1.0, 1.0 - spread / DISPERSION_CAP))


def value(
    observations: Sequence[SoldObservation],
    *,
    min_comp_n: int,
    match_confidence: float,
    include_upper_bound_prices: bool = False,
) -> Valuation | None:
    """Value an item from comparable sales, or return `None`.

    Returns `None` when fewer than `min_comp_n` usable observations survive
    filtering and trimming. Refusing is a valid, expected outcome and callers must
    handle it rather than substituting a default.

    `include_upper_bound_prices` re-admits Best Offer sales. It exists so the bias
    can be measured against real settlement data, not for normal operation.
    """
    if min_comp_n < 1:
        msg = "min_comp_n must be at least 1"
        raise ValueError(msg)

    usable = [o for o in observations if include_upper_bound_prices or not o.price_is_upper_bound]
    trimmed = trim_outliers([o.price_pence for o in usable])
    if len(trimmed) < min_comp_n:
        return None

    p25 = _percentile(trimmed, 25)
    p60 = _percentile(trimmed, 60)
    # Degenerate sets (heavy ties) can invert after rounding. Collapsing to the
    # lower figure keeps the fast-sale price conservative.
    p60 = max(p60, p25)

    known_days = [o.days_to_sell for o in usable if o.days_to_sell is not None]
    return Valuation(
        est_p25_pence=p25,
        est_p60_pence=p60,
        comp_n=len(trimmed),
        est_confidence=_sample_confidence(len(trimmed)) * _spread_confidence(trimmed),
        match_confidence=match_confidence,
        days_to_sell_p50=statistics.median(known_days) if known_days else None,
    )
