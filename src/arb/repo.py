"""Persistence for the buy loop: listings in, opportunities out, decisions recorded.

The `record_decision` function is the one to read carefully. It is the write path for
manual decisions and for AutoBuy alike, which is why both go through the same door
rather than each having their own.

It refuses to record a skip without a reason. Not as validation hygiene — as the
thing that makes the whole exercise measurable. If you cannot say *why* you passed,
you can never afterwards ask whether an automated buyer would have passed too, and
every dry-run comparison flatters the automation by default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from arb.db import Decisions, Inventory, Listings, Opportunities
from arb.models import Decision, DecisionOutcome

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from arb.models import Listing, Opportunity

__all__ = ["record_decision", "top_opportunities", "upsert_listing", "write_opportunity"]


def upsert_listing(session: Session, listing: Listing) -> int:
    """Insert or refresh a listing, returning its row id.

    `first_seen` is preserved on conflict and only `last_seen` moves. That pair is
    the entire basis of wardrobe tracking and of buy-side time-on-market later, and
    overwriting `first_seen` would quietly destroy it.
    """
    attrs = listing.attrs
    values = {
        "venue": listing.venue.value,
        "external_id": listing.external_id,
        "url": listing.url,
        "price_pence": listing.price_pence,
        "total_pence": listing.total_pence,
        "brand_norm": attrs.brand_norm,
        "title_norm": attrs.title_norm,
        "size_norm": attrs.size_norm,
        "colour_norm": attrs.colour_norm,
        "condition_band": attrs.condition_band.value if attrs.condition_band else None,
        "category_id": attrs.category_id,
        "country": attrs.country,
        "seller_id": listing.seller_id,
        "favourites": listing.favourites,
        "views": listing.views,
        "first_seen": listing.first_seen,
        "last_seen": listing.last_seen,
    }
    stmt = sqlite_insert(Listings).values(**values)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["venue", "external_id"],
            set_={
                key: stmt.excluded[key]
                for key in values
                if key not in {"venue", "external_id", "first_seen"}
            },
        )
    )
    row_id = session.scalar(
        select(Listings.id).where(
            Listings.venue == listing.venue.value,
            Listings.external_id == listing.external_id,
        )
    )
    if row_id is None:
        msg = f"listing upsert produced no row for {listing.venue.value}:{listing.external_id}"
        raise RuntimeError(msg)
    return row_id


def write_opportunity(session: Session, opportunity: Opportunity, *, listing_id: int) -> int:
    """Persist a score. `fee_table_version` is carried through so the assumptions
    behind every historical number stay recoverable."""
    valuation = opportunity.valuation
    row = Opportunities(
        listing_id=listing_id,
        est_p25_pence=valuation.est_p25_pence,
        est_p60_pence=valuation.est_p60_pence,
        comp_n=valuation.comp_n,
        est_confidence=valuation.est_confidence,
        match_confidence=valuation.match_confidence,
        fees_pence=opportunity.fees_pence,
        ship_in_pence=opportunity.ship_in_pence,
        ship_out_pence=opportunity.ship_out_pence,
        net_pence=opportunity.net_pence,
        roi=opportunity.roi,
        days_to_sell_p50=valuation.days_to_sell_p50,
        capital_velocity=opportunity.capital_velocity,
        qty=opportunity.qty,
        fee_table_version=opportunity.fee_table_version,
        scored_at=opportunity.scored_at,
    )
    session.add(row)
    session.flush()
    return row.id


def top_opportunities(session: Session, *, limit: int = 20) -> list[tuple[Opportunities, Listings]]:
    """The current buy list, best capital velocity first."""
    stmt = (
        select(Opportunities, Listings)
        .join(Listings, Opportunities.listing_id == Listings.id)
        .order_by(Opportunities.capital_velocity.desc(), Opportunities.scored_at.desc())
        .limit(limit)
    )
    return [(opp, listing) for opp, listing in session.execute(stmt)]


def record_decision(session: Session, decision: Decision) -> int:
    """Persist an already-validated decision, and open an inventory row on a buy.

    Takes a `models.Decision` rather than loose arguments so the skip-reason rule is
    enforced by construction. A caller cannot reach this function holding an invalid
    decision, which matters because AutoBuy will eventually call it too and must not
    be able to write a reasonless skip that manual entry would have rejected.
    """
    row = Decisions(
        opportunity_id=decision.opportunity_id,
        mode=decision.mode.value,
        outcome=decision.outcome.value,
        skip_reason=decision.skip_reason,
        decided_at=decision.decided_at,
        spend_pence=decision.spend_pence,
    )
    session.add(row)
    session.flush()

    if decision.outcome is DecisionOutcome.BOUGHT:
        session.add(
            Inventory(
                decision_id=row.id,
                cost_pence=decision.spend_pence or 0,
                qty=1,
                acquired_at=decision.decided_at,
            )
        )
        session.flush()
    return row.id
