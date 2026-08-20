"""Repository tests. The decision write path is the important one -- manual entry and
AutoBuy share it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from arb.db import Decisions, Inventory, Listings
from arb.models import (
    Attributes,
    Decision,
    DecisionMode,
    DecisionOutcome,
    Listing,
    Opportunity,
    Valuation,
    Venue,
)
from arb.repo import record_decision, top_opportunities, upsert_listing, write_opportunity

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

T0 = datetime(2026, 8, 1, tzinfo=UTC)


def _listing(external_id: str = "1", price: int = 1200, *, seen: datetime = T0) -> Listing:
    return Listing(
        venue=Venue.VINTED,
        external_id=external_id,
        price_pence=price,
        attrs=Attributes(brand_norm="nike", title_norm="nike air max 90", size_norm="M"),
        first_seen=seen,
        last_seen=seen,
    )


def _opportunity(velocity: float | None = 0.05) -> Opportunity:
    return Opportunity(
        listing_id=0,
        valuation=Valuation(
            est_p25_pence=4500,
            est_p60_pence=5000,
            comp_n=8,
            est_confidence=0.5,
            match_confidence=0.9,
            days_to_sell_p50=7,
        ),
        fees_pence=600,
        ship_in_pence=0,
        ship_out_pence=300,
        net_pence=2400,
        roi=2.0,
        capital_velocity=velocity,
        fee_table_version="ebay_uk@abc123def456",
        scored_at=T0,
    )


def _seed(session: Session, velocity: float | None = 0.05, external_id: str = "1") -> int:
    listing_id = upsert_listing(session, _listing(external_id))
    return write_opportunity(session, _opportunity(velocity), listing_id=listing_id)


# ------------------------------------------------------------------ listings


def test_upsert_returns_a_stable_id(session: Session) -> None:
    first = upsert_listing(session, _listing())
    second = upsert_listing(session, _listing())
    assert first == second
    assert session.scalar(select(func.count()).select_from(Listings)) == 1


def test_upsert_preserves_first_seen_and_advances_last_seen(session: Session) -> None:
    """first_seen/last_seen is the entire basis of time-on-market and wardrobe
    tracking. Overwriting first_seen would destroy it silently."""
    upsert_listing(session, _listing(seen=T0))
    later = T0 + timedelta(days=3)
    upsert_listing(session, _listing().model_copy(update={"first_seen": later, "last_seen": later}))
    row = session.scalars(select(Listings)).one()
    assert row.first_seen == T0
    assert row.last_seen == later


def test_upsert_refreshes_volatile_fields(session: Session) -> None:
    upsert_listing(session, _listing(price=1200))
    upsert_listing(session, _listing(price=900))
    assert session.scalars(select(Listings)).one().price_pence == 900


# ------------------------------------------------------------------ opportunities


def test_opportunity_carries_the_fee_table_version(session: Session) -> None:
    opportunity_id = _seed(session)
    rows = top_opportunities(session)
    assert rows[0][0].id == opportunity_id
    assert rows[0][0].fee_table_version == "ebay_uk@abc123def456"


def test_buy_list_is_ordered_by_capital_velocity(session: Session) -> None:
    _seed(session, velocity=0.01, external_id="slow")
    _seed(session, velocity=0.90, external_id="fast")
    velocities = [opp.capital_velocity for opp, _ in top_opportunities(session)]
    assert velocities == sorted(velocities, key=lambda v: v or 0.0, reverse=True)


# ------------------------------------------------------------------ decisions


def test_a_skip_records_its_reason(session: Session) -> None:
    opportunity_id = _seed(session)
    record_decision(
        session,
        Decision(
            opportunity_id=opportunity_id,
            mode=DecisionMode.MANUAL,
            outcome=DecisionOutcome.SKIPPED,
            skip_reason="seller feedback too thin",
            decided_at=T0,
        ),
    )
    row = session.scalars(select(Decisions)).one()
    assert row.outcome == "skipped"
    assert row.skip_reason == "seller feedback too thin"


def test_a_reasonless_skip_cannot_be_constructed(session: Session) -> None:
    """Enforced by construction, so AutoBuy cannot later write something manual
    entry would have rejected."""
    with pytest.raises(ValidationError, match="skip_reason is required"):
        Decision(
            opportunity_id=_seed(session),
            mode=DecisionMode.AUTOBUY,
            outcome=DecisionOutcome.SKIPPED,
            decided_at=T0,
        )


def test_a_buy_opens_an_inventory_row_with_its_cost_basis(session: Session) -> None:
    opportunity_id = _seed(session)
    record_decision(
        session,
        Decision(
            opportunity_id=opportunity_id,
            mode=DecisionMode.MANUAL,
            outcome=DecisionOutcome.BOUGHT,
            decided_at=T0,
            spend_pence=1150,
        ),
    )
    item = session.scalars(select(Inventory)).one()
    assert item.cost_pence == 1150
    assert item.sold_at is None


def test_a_skip_does_not_create_inventory(session: Session) -> None:
    record_decision(
        session,
        Decision(
            opportunity_id=_seed(session),
            mode=DecisionMode.MANUAL,
            outcome=DecisionOutcome.SKIPPED,
            skip_reason="damaged in photos",
            decided_at=T0,
        ),
    )
    assert session.scalar(select(func.count()).select_from(Inventory)) == 0


def test_autobuy_writes_through_the_same_door(session: Session) -> None:
    """One write path for manual and automated decisions, so the dry-run comparison
    is against like for like."""
    record_decision(
        session,
        Decision(
            opportunity_id=_seed(session),
            mode=DecisionMode.AUTOBUY,
            outcome=DecisionOutcome.SKIPPED,
            skip_reason="rails: daily cap reached",
            decided_at=T0,
        ),
    )
    assert session.scalars(select(Decisions)).one().mode == "autobuy"
