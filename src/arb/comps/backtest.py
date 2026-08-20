"""Backtest: does `value()` actually predict what things sell for?

**P3** is the placeholder that governs how much any other number here can be trusted.
`est_confidence` is currently a shape derived from comp count and spread — it has never
been checked against an outcome. This module is the check.

The gate, from the build plan: **100 items with known realised prices, median absolute
percentage error of `est_p25` under 15%.** Two rules about that number:

*It refuses below the sample floor.* A 15% error measured on eleven items is not a
result. Reporting one would be worse than reporting nothing, because it would carry
the authority of having been measured.

*Do not tune thresholds to pass.* If the error is over 15%, the documented response is
to add a second comps source, not to move the line. A backtest you adjust until it
passes measures your patience, not your valuation.

**Error is measured against `est_p25`, not `est_p60`**, because p25 is what the buy
side scores on. Backtesting the number you do not trade against would be measuring the
wrong thing accurately.

**Signed error is reported alongside absolute error.** They answer different questions.
Absolute error says how far off the estimates are; signed error says whether they are
systematically optimistic — and a valuation that is 12% out in both directions is a
noise problem, while one that is 12% high every time is a bias problem with a fix.
"""

from __future__ import annotations

from decimal import Decimal
from statistics import median
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "MAX_MEDIAN_ERROR",
    "MIN_LABELLED_ITEMS",
    "BacktestResult",
    "LabelledItem",
    "backtest",
]

MIN_LABELLED_ITEMS = 100
"""Sample floor from the build plan's day-6 gate. Below this the module refuses."""

MAX_MEDIAN_ERROR = Decimal("0.15")
"""Median absolute percentage error of `est_p25` that counts as a pass. **Do not tune
this to make a run pass.** The documented response to failure is a second comps
source; moving the line measures patience rather than accuracy."""


class LabelledItem(NamedTuple):
    """One item whose estimate and true outcome are both known."""

    external_id: str
    est_p25_pence: int
    realised_pence: int
    comp_n: int
    est_confidence: float

    @property
    def signed_error(self) -> Decimal:
        """Positive when the estimate was **above** what the item actually fetched."""
        if self.realised_pence <= 0:
            msg = f"{self.external_id}: realised price must be positive"
            raise ValueError(msg)
        return Decimal(self.est_p25_pence - self.realised_pence) / Decimal(self.realised_pence)

    @property
    def absolute_error(self) -> Decimal:
        return abs(self.signed_error)


class BacktestResult(NamedTuple):
    items: int
    median_absolute_error: Decimal
    median_signed_error: Decimal
    """Negative means estimates run low, positive means they run high. A valuation
    12% out in both directions is noise; 12% high every time is bias, and bias has a
    fix."""

    within_tolerance: int
    passed: bool

    @property
    def is_biased(self) -> bool:
        """True when most of the error has a direction rather than being spread."""
        return abs(self.median_signed_error) > self.median_absolute_error / 2


def backtest(
    items: Sequence[LabelledItem], *, min_items: int = MIN_LABELLED_ITEMS
) -> BacktestResult | None:
    """Measure valuation accuracy. `None` below the sample floor.

    Refusing is the correct output on a thin sample, and it is the same posture as
    `value()` returning `None` below the comp floor: a number produced from too little
    data is not a weaker answer, it is a different and worse kind of thing — one that
    looks like evidence.
    """
    if len(items) < min_items:
        return None

    absolute = [item.absolute_error for item in items]
    signed = [item.signed_error for item in items]
    median_absolute = median(absolute)
    return BacktestResult(
        items=len(items),
        median_absolute_error=median_absolute,
        median_signed_error=median(signed),
        within_tolerance=sum(1 for error in absolute if error <= MAX_MEDIAN_ERROR),
        passed=median_absolute <= MAX_MEDIAN_ERROR,
    )
