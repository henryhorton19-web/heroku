"""Contest density: how many other buyers are chasing the same listing.

Margin is not the only thing that decides whether a trade happens. A 60% margin on
an item forty people have saved is a trade you will usually lose, and a 25% margin on
something nobody has noticed is one you will usually win. Thin-contest niches at
lower margin beat thick-contest niches at higher margin, and the data to tell them
apart -- `favourites` and `views` -- has been captured on every listing since day one
for exactly this.

**The age problem, and why the ratio solves it.** The obvious metric is favourites
per day, and it is unavailable: Vinted's search response carries no listing-creation
date, and `first_seen` is when *we* saw it, not when it was posted. On a first scan
`first_seen == now`, so the denominator is zero. This is the same missing-denominator
shape as `days_to_sell` in `comps/valuation.py`.

The save *rate* sidesteps it. Favourites and views accumulate over the same unknown
window, so the window cancels:

    save_rate = favourites / views

Eight saves from twenty views is a hot item whether that took two hours or two days.
No creation date needed.

**The asymmetry runs the other way from the quality filter.** `sourcing/quality.py`
rejects on ambiguity, because an ambiguous description usually means a flawed item.
Here, ambiguity is *accepted*: a missing favourite count says nothing about demand,
and a listing with two views has not been seen by enough people to measure. Rejecting
on absent data would systematically drop the newest listings -- the ones with the
least contest, which is precisely the stock worth buying. So both rules carry a
volume floor and unknown counters pass.

**The thresholds are guesses** (`provisional: True`). They are not derived from
anything; nobody has measured what favourite count actually predicts a lost race.
What closes the gap is realised win rate -- of the listings you tried to buy, which
ones were gone before checkout, and what did their counters look like. Until then
this is P9 in the placeholder register and `arb provenance` lists it as open.

Nothing here is stamped onto a persisted number, unlike `fee_table_version`. A
contest verdict influences a boolean rejection, not a margin, so there is no
historical figure for a wrong threshold to poison -- retuning changes what future
scans reject and nothing already written.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from arb.models import Listing

__all__ = [
    "DEFAULT_CONTEST_POLICY",
    "ContestPolicy",
    "ContestReason",
    "ContestVerdict",
    "assess_contest",
]


class ContestReason(StrEnum):
    HIGH_FAVOURITES = "high_favourites"
    """Enough people are watching that the race is probably already lost."""

    HIGH_SAVE_RATE = "high_save_rate"
    """A large share of viewers saved it. Intense demand, whatever the raw count."""


class ContestPolicy(NamedTuple):
    """Thresholds for calling a listing contested.

    Passed as data rather than read from module constants so that a monitor, a
    backtest and a dry-run can each hold their own policy without mutating global
    state -- the same reason `ListingFilter` is a frozen object.
    """

    max_favourites: int = 25
    """Absolute cap. Above this the item is contested however many views it has:
    twenty-five people have actively saved it and any of them can buy first."""

    min_favourites_for_rate: int = 5
    """Volume floor under the save rate. One save from one view is 100% and means
    nothing; without this floor the rate rejects every brand-new listing."""

    max_save_rate: float = 0.20
    """Share of viewers who saved it. Vinted counts a view generously, so a fifth of
    viewers saving is already well above a typical listing."""

    version: str = "contest-v0"
    """Bumped on any threshold change, so a retune is visible in `arb provenance`
    rather than silently changing what future scans reject."""

    provisional: bool = True
    """These numbers are assumptions. Closed by realised win rate -- of the listings
    you tried to buy, which were gone before checkout. See P9 in `arb.provenance`."""


DEFAULT_CONTEST_POLICY = ContestPolicy()
"""Module-level singleton so the default is not rebuilt per listing."""


class ContestVerdict(NamedTuple):
    """Mirrors `quality.QualityVerdict` deliberately: a reader who knows one knows
    this one, and `scan` treats the two gates identically."""

    accepted: bool
    reasons: tuple[ContestReason, ...]
    save_rate: float | None = None
    """`None` when it could not be computed -- absent views, zero views, or too few
    favourites to be meaningful. Carried for diagnostics, never for ranking."""

    @property
    def skip_reason(self) -> str | None:
        """A `decisions.skip_reason` string, or None when accepted.

        Sorted, because AutoBuy's dry-run diffs against these strings and ordering
        that depended on iteration order would make the comparison flap.
        """
        if self.accepted:
            return None
        return "contest:" + ",".join(sorted({r.value for r in self.reasons}))


def assess_contest(
    listing: Listing, policy: ContestPolicy = DEFAULT_CONTEST_POLICY
) -> ContestVerdict:
    """Judge how contested a listing is. Pure: no I/O, no clock read.

    Returns accepted when the counters are missing or too thin to read. That is the
    deliberate direction: absent data is not evidence of demand.
    """
    favourites = listing.favourites
    if favourites is None:
        return ContestVerdict(accepted=True, reasons=())

    reasons: list[ContestReason] = []
    if favourites >= policy.max_favourites:
        reasons.append(ContestReason.HIGH_FAVOURITES)

    save_rate = _save_rate(favourites, listing.views, policy)
    if save_rate is not None and save_rate >= policy.max_save_rate:
        reasons.append(ContestReason.HIGH_SAVE_RATE)

    return ContestVerdict(accepted=not reasons, reasons=tuple(reasons), save_rate=save_rate)


def _save_rate(favourites: int, views: int | None, policy: ContestPolicy) -> float | None:
    """Favourites per view, or None when the sample is too thin to mean anything.

    Zero views alongside a positive favourite count is inconsistent data rather than
    infinite demand, so it returns None and leaves the absolute rule to decide.
    """
    if views is None or views <= 0:
        return None
    if favourites < policy.min_favourites_for_rate:
        return None
    return favourites / views
