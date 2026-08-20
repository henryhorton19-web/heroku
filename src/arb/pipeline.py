"""The buy-side pipeline: fetch listings, price them, rank them.

This is the composition layer, and it is deliberately thin. Every piece of judgement
lives somewhere else — valuation in `comps.valuation`, filtering in
`sourcing.quality`, ordering in `sourcing.rank` — and `scan()` itself stays a pure
function. What happens here is plumbing and the ordering of I/O.

The one real decision this module makes is **what to do when a listing cannot be
priced**. It becomes an unpriceable candidate rather than being dropped, so the scan
can tell you how many items it looked at and could not value. Silently discarding
them makes a thin comp database look like a quiet market.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from arb.comps.matching import select_comps
from arb.comps.valuation import value
from arb.models import CompQuery
from arb.sourcing.contest import DEFAULT_CONTEST_POLICY, ContestPolicy, assess_contest
from arb.sourcing.quality import assess
from arb.sourcing.rank import ScoreContext, VelocityPolicy
from arb.sourcing.scanner import Candidate, ScanOutcome, scan

if TYPE_CHECKING:
    from datetime import datetime

    from arb.comps.service import CompsService
    from arb.models import Listing, ListingFilter
    from arb.protocols import BuyVenue, FeeModel

__all__ = ["DEFAULT_SCAN_SETTINGS", "ScanDeps", "ScanSettings", "query_for", "run_scan"]


class ScanSettings(NamedTuple):
    """Everything the pipeline needs beyond its collaborators."""

    min_comp_n: int = 3
    ship_in_pence: int = 0
    ship_out_pence: int = 320
    min_net_pence: int = 1
    policy: VelocityPolicy = VelocityPolicy.EXCLUDE
    assumed_days_to_sell: float = 30.0
    contest_policy: ContestPolicy = DEFAULT_CONTEST_POLICY


DEFAULT_SCAN_SETTINGS = ScanSettings()
"""Module-level singleton so the default is not re-constructed per call."""


class ScanDeps(NamedTuple):
    """The pipeline's collaborators, bundled so adding one is a field not a signature
    change at every call site."""

    buy_venue: BuyVenue
    comps: CompsService
    fee_model: FeeModel


def query_for(listing: Listing) -> CompQuery:
    """Build the comp query for a listing.

    Condition is deliberately *not* included. Narrowing comps to the exact condition
    band sounds more precise but usually empties the set, and the valuation floor
    then refuses everything. Condition is better handled as a discount applied to a
    fuller comp set, fitted once there is realised data to fit it to.
    """
    attrs = listing.attrs
    return CompQuery(
        brand_norm=attrs.brand_norm,
        title_norm=attrs.title_norm,
        size_norm=attrs.size_norm,
        category_id=attrs.category_id,
    )


def run_scan(
    deps: ScanDeps,
    listing_filter: ListingFilter,
    now: datetime,
    settings: ScanSettings = DEFAULT_SCAN_SETTINGS,
) -> ScanOutcome:
    """Fetch, price and rank. All network access happens here, none inside `scan`."""
    listings = deps.buy_venue.search(listing_filter)

    candidates: list[Candidate] = []
    for listing in listings:
        # Quality first, deliberately. `scan` re-runs this and remains the
        # authoritative classifier, but assessing here means a listing we were always
        # going to reject never costs a comps request. On a 100-request month that is
        # the difference between scanning all day and running dry by the 10th.
        if not assess(listing.attrs.title_norm).accepted:
            candidates.append(Candidate(listing=listing, valuation=None))
            continue
        # Contest next, for the same reason and at no cost at all: it reads two
        # integers already on the listing. A heavily-watched item is a race we
        # expect to lose, and paying a comps request to price something we were
        # never going to win is the same waste in a different disguise.
        if not assess_contest(listing, settings.contest_policy).accepted:
            candidates.append(Candidate(listing=listing, valuation=None))
            continue
        query = query_for(listing)
        observations = deps.comps.comps_for(query)
        matched, match_confidence = select_comps(query, observations)
        valuation = (
            value(
                matched,
                min_comp_n=settings.min_comp_n,
                match_confidence=match_confidence,
            )
            if matched
            else None
        )
        candidates.append(Candidate(listing=listing, valuation=valuation))

    return scan(
        candidates,
        ScoreContext(
            fee_model=deps.fee_model,
            now=now,
            ship_in_pence=settings.ship_in_pence,
            ship_out_pence=settings.ship_out_pence,
            contest_policy=settings.contest_policy,
        ),
        policy=settings.policy,
        assumed_days_to_sell=settings.assumed_days_to_sell,
        min_net_pence=settings.min_net_pence,
    )
