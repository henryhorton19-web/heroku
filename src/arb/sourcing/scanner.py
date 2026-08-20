"""The scanner. One pure function, and the reason automation is additive later.

`scan(candidates, ctx)` takes listings that have already been fetched and valued,
and returns a ranked result. It performs **no I/O**: no HTTP, no database, no clock
read. Everything time-dependent arrives in `ScoreContext.now`.

That constraint is load-bearing rather than stylistic:

* **Monitors** become a scheduler plus a seen-set diff wrapped around this function.
  Nothing in here changes.
* **AutoBuy** dry-runs become replaying recorded candidates through this function and
  comparing its output to the `decisions` rows already recorded.
* **Backtests** become the same call with historical input.

If anything in this module ever needs a network call or `utcnow()`, the feature
belongs in the caller, not here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from arb.sourcing.contest import assess_contest
from arb.sourcing.quality import assess
from arb.sourcing.rank import ScanResult, ScoredCandidate, VelocityPolicy, rank, score

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arb.models import Listing, Valuation
    from arb.sourcing.rank import ScoreContext

__all__ = ["Candidate", "RejectedListing", "ScanOutcome", "scan"]


class Candidate(NamedTuple):
    """A listing with its valuation, ready to be costed.

    `valuation` is `None` when the comp floor was not met. Carrying the refusal
    through rather than dropping it earlier is what lets the scan report how many
    candidates were unpriceable, which is a different problem from a quiet market.
    """

    listing: Listing
    valuation: Valuation | None
    description: str = ""


class RejectedListing(NamedTuple):
    listing: Listing
    reason: str


class ScanOutcome(NamedTuple):
    """The full picture: what to buy, and everything that fell out on the way.

    Rejections carry a ready-made `skip_reason`, so writing a `decisions` row for a
    pass is a lookup rather than a judgement call. AutoBuy's dry-run scores itself
    against those reasons, so they have to exist for every skip.
    """

    result: ScanResult
    rejected_quality: tuple[RejectedListing, ...]
    unpriceable: tuple[RejectedListing, ...]
    rejected_contest: tuple[RejectedListing, ...] = ()
    """Listings dropped because too many other buyers are already watching. Counted
    apart from quality because they mean opposite things: a quality rejection says
    the item is bad, a contest rejection says the item is good and you will lose the
    race for it. A scan dominated by contest rejections is a signal to search a
    thinner niche, not to loosen the filter."""

    @property
    def ranked(self) -> tuple[ScoredCandidate, ...]:
        return self.result.ranked


def scan(
    candidates: Sequence[Candidate],
    ctx: ScoreContext,
    *,
    policy: VelocityPolicy = VelocityPolicy.EXCLUDE,
    assumed_days_to_sell: float = 30.0,
    min_net_pence: int = 1,
) -> ScanOutcome:
    """Filter, cost, and rank. Pure: same input, same output, always.

    The three gates run in a fixed order -- quality, contest, valuation -- and the
    order is load-bearing. Quality first because "this item is damaged" is the most
    actionable reason and should win when several are true. Contest second because
    it needs no comps, so a caller pre-filtering on it (see `pipeline.run_scan`)
    spends nothing to apply it. Valuation last because it is the only one that costs
    a request against a 100-per-month quota.
    """
    rejected: list[RejectedListing] = []
    contested: list[RejectedListing] = []
    unpriceable: list[RejectedListing] = []
    scored: list[ScoredCandidate] = []

    for candidate in candidates:
        verdict = assess(candidate.listing.attrs.title_norm, candidate.description)
        if not verdict.accepted:
            reason = verdict.skip_reason or "quality"
            rejected.append(RejectedListing(candidate.listing, reason))
            continue
        contest = assess_contest(candidate.listing, ctx.contest_policy)
        if not contest.accepted:
            contested.append(RejectedListing(candidate.listing, contest.skip_reason or "contest"))
            continue
        if candidate.valuation is None:
            unpriceable.append(RejectedListing(candidate.listing, "no_valuation:comp_floor"))
            continue
        scored.append(
            ScoredCandidate(candidate.listing, score(candidate.listing, candidate.valuation, ctx))
        )

    return ScanOutcome(
        result=rank(
            scored,
            policy=policy,
            assumed_days_to_sell=assumed_days_to_sell,
            min_net_pence=min_net_pence,
        ),
        rejected_quality=tuple(rejected),
        unpriceable=tuple(unpriceable),
        rejected_contest=tuple(contested),
    )
