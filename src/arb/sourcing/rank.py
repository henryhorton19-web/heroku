"""Scoring and ranking candidate buys.

`scan` is a **pure function**: listings and a clock in, opportunities out, no I/O of
its own. That is the whole reason monitors, AutoBuy and dry-run comparison can be
added later without touching this file -- a scheduler plus a seen-set diff wraps it,
and a dry-run harness calls it with recorded input and gets identical output.

Ranking is on **capital velocity, not ROI**. Capital, not margin, is the binding
constraint: 40% clearing in five days beats 120% sitting for ninety.

    capital_velocity = net_pence / cost_pence / max(days_to_sell_p50, 1)

Which raises the awkward question this module has to answer honestly. The eBay sold
endpoint carries no listing-start date, so `days_to_sell_p50` is usually `None` and
the denominator is unknown. `VelocityPolicy` makes the response an explicit, tested
choice rather than an accident:

* `EXCLUDE` (default) is the precision-over-recall answer. An item whose clearing
  speed is unknown is not ranked, and the count of suppressed candidates is reported
  so the silence is visible rather than looking like a quiet market.
* `ASSUME_DEFAULT` ranks it against `assumed_days_to_sell`. Useful early, while the
  cache has not yet observed enough active-to-sold transitions to measure anything,
  but every figure it produces rests on a number nobody measured.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

from arb.models import Opportunity
from arb.sourcing.contest import DEFAULT_CONTEST_POLICY, ContestPolicy

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from arb.models import Listing, Valuation
    from arb.protocols import FeeModel

__all__ = [
    "ScanResult",
    "ScoreContext",
    "ScoredCandidate",
    "VelocityPolicy",
    "capital_velocity",
    "rank",
    "score",
]


class VelocityPolicy(StrEnum):
    EXCLUDE = "exclude"
    ASSUME_DEFAULT = "assume_default"


def capital_velocity(net_pence: int, cost_pence: int, days_to_sell: float | None) -> float | None:
    """Return on capital per day. `None` when clearing speed is unknown.

    Days are floored at 1: a same-day sale is not infinitely fast, and without the
    floor a single fast comp would dominate every ranking.
    """
    if days_to_sell is None or cost_pence <= 0:
        return None
    return (net_pence / cost_pence) / max(days_to_sell, 1.0)


class ScoredCandidate(NamedTuple):
    listing: Listing
    opportunity: Opportunity


class ScanResult(NamedTuple):
    """Ranked opportunities plus what was dropped and why.

    The counts are not decoration. An empty buy list because the market is quiet and
    an empty buy list because every candidate lacked a velocity estimate are
    different situations, and a bare list cannot tell them apart.
    """

    ranked: tuple[ScoredCandidate, ...]
    suppressed_unknown_velocity: int
    suppressed_below_floor: int
    suppressed_anomalous_cost: int = 0
    """Listings whose deployed capital is zero or negative. Counted apart from
    unknown velocity on purpose: a free item is not a fast-clearing bargain, it is a
    data error or a scam, and folding it into the unpriceable count would make these
    diagnostics lie about why the buy list is empty."""


class ScoreContext(NamedTuple):
    """Everything a scan needs beyond the candidates themselves.

    Grouped rather than passed as seven arguments so that adding a component later
    is a field, not a signature change at every call site -- which is exactly what
    happened when the contest filter arrived, and why `contest_policy` lives here
    rather than widening `scan`. The type carries two kinds of thing: the cost
    inputs `score` needs, and the per-scan filter policy the gates need. Keeping
    them together is what lets a monitor, a backtest and a dry-run each hold their
    own configuration without any of them mutating global state.
    """

    fee_model: FeeModel
    now: datetime
    ship_in_pence: int
    ship_out_pence: int
    qty: int = 1
    contest_policy: ContestPolicy = DEFAULT_CONTEST_POLICY
    """Thresholds for the contest gate. Read by `scan`, not by `score`: contest
    decides whether to bid at all, it does not change what the item is worth."""


def score(listing: Listing, valuation: Valuation, ctx: ScoreContext) -> Opportunity:
    """Cost out a single listing at the fast-sale price.

    Scored at `est_p25` (fast-sale), not `est_p60` (optimal). Buying against the
    optimistic figure is how a plausible-looking margin becomes a loss when the item
    does not clear at the price you hoped for.
    """
    cost_pence = listing.total_pence or listing.price_pence
    sale_pence = valuation.est_p25_pence
    fees = ctx.fee_model.fees_pence(sale_pence, ctx.qty)
    net = sale_pence - cost_pence - fees - ctx.ship_in_pence - ctx.ship_out_pence
    deployed = cost_pence + ctx.ship_in_pence
    return Opportunity(
        listing_id=0,
        valuation=valuation,
        fees_pence=fees,
        ship_in_pence=ctx.ship_in_pence,
        ship_out_pence=ctx.ship_out_pence,
        net_pence=net,
        roi=net / deployed if deployed > 0 else 0.0,
        capital_velocity=capital_velocity(net, deployed, valuation.days_to_sell_p50),
        qty=ctx.qty,
        fee_table_version=ctx.fee_model.version,
        scored_at=ctx.now,
    )


def rank(
    candidates: Sequence[ScoredCandidate],
    *,
    policy: VelocityPolicy = VelocityPolicy.EXCLUDE,
    assumed_days_to_sell: float = 30.0,
    min_net_pence: int = 1,
) -> ScanResult:
    """Order candidates by capital velocity, applying the unknown-velocity policy."""
    kept: list[ScoredCandidate] = []
    unknown = 0
    below_floor = 0
    anomalous = 0

    for candidate in candidates:
        if _deployed(candidate) <= 0:
            anomalous += 1
            continue
        if candidate.opportunity.net_pence < min_net_pence:
            below_floor += 1
            continue
        resolved = _resolve_velocity(candidate, policy, assumed_days_to_sell)
        if resolved is None:
            unknown += 1
            continue
        kept.append(resolved)

    kept.sort(key=lambda c: c.opportunity.capital_velocity or 0.0, reverse=True)
    return ScanResult(
        ranked=tuple(kept),
        suppressed_unknown_velocity=unknown,
        suppressed_below_floor=below_floor,
        suppressed_anomalous_cost=anomalous,
    )


def _resolve_velocity(
    candidate: ScoredCandidate, policy: VelocityPolicy, assumed_days: float
) -> ScoredCandidate | None:
    """Return the candidate with a usable velocity, or None to suppress it."""
    opportunity = candidate.opportunity
    if opportunity.capital_velocity is not None:
        return candidate
    if policy is VelocityPolicy.EXCLUDE:
        return None
    velocity = capital_velocity(opportunity.net_pence, _deployed(candidate), assumed_days)
    if velocity is None:
        return None
    return ScoredCandidate(
        candidate.listing, opportunity.model_copy(update={"capital_velocity": velocity})
    )


def _deployed(candidate: ScoredCandidate) -> int:
    """Capital actually at risk: what the item costs plus what it costs to receive."""
    listing = candidate.listing
    return (listing.total_pence or listing.price_pence) + candidate.opportunity.ship_in_pence
