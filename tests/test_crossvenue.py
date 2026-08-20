"""Cross-venue reconciliation.

One failure motivates this whole module: **selling the same item twice.** It costs a
refund, a defect, and sometimes the account, and it happens through ordinary partial
failure — the sale lands on one venue and the de-list on the other times out.

The tests below are mostly about that gap. Intent must survive a crash, a failed
de-list must not look like an untouched one, and the hazard view must be answerable
from state alone so it is still correct after a period when nothing was running.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from arb.db import Inventory, OwnListings
from arb.selling.crossvenue import (
    HazardKind,
    hazards,
    mark_sold,
    record_delist_failure,
    record_delisted,
    request_delists,
    unresolved_delists,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _item(session: Session, *, cost: int = 1200) -> Inventory:
    row = Inventory(cost_pence=cost, qty=1, state="listed", acquired_at=NOW)
    session.add(row)
    session.flush()
    return row


def _listed(session: Session, item: Inventory, venue: str, external_id: str) -> OwnListings:
    row = OwnListings(
        inventory_id=item.id,
        venue=venue,
        external_id=external_id,
        ask_pence=4000,
        listed_at=NOW,
    )
    session.add(row)
    session.flush()
    return row


def _on_two_venues(session: Session) -> tuple[Inventory, OwnListings, OwnListings]:
    item = _item(session)
    return item, _listed(session, item, "ebay", "E1"), _listed(session, item, "vinted", "V1")


# ---------------------------------------------------------------- the sale


def test_a_sale_is_attributed_to_the_right_item(session: Session) -> None:
    item, _, _ = _on_two_venues(session)
    assert mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW) == item.id


def test_a_sale_for_an_untracked_listing_is_reported_not_raised(session: Session) -> None:
    """A notification for something we do not track is worth reporting, not worth
    aborting a webhook handler over."""
    assert mark_sold(session, venue="ebay", external_id="unknown", sold_at=NOW) is None


# ---------------------------------------------------------------- intent first


def test_selling_marks_every_other_venue_for_delisting(session: Session) -> None:
    item, _, vinted = _on_two_venues(session)
    outcome = request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    assert outcome.requested == (vinted.id,)
    assert vinted.delist_requested_at == NOW


def test_the_selling_venue_is_not_delisted(session: Session) -> None:
    item, ebay, _ = _on_two_venues(session)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    assert ebay.delist_requested_at is None


def test_intent_survives_without_any_venue_call(session: Session) -> None:
    """The crash-safety property. Recording intent before the API call means a process
    that dies mid-sale leaves findable work; the other order loses it entirely and the
    hazard becomes invisible rather than pending."""
    item, _, _ = _on_two_venues(session)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    assert len(unresolved_delists(session)) == 1


def test_requesting_twice_does_not_move_the_original_timestamp(session: Session) -> None:
    """How long a de-list has been outstanding is the thing that decides whether it is
    benign. Refreshing it on every pass would reset that clock forever."""
    item, _, vinted = _on_two_venues(session)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    later = NOW + timedelta(hours=3)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=later)
    assert vinted.delist_requested_at == NOW


def test_an_already_delisted_venue_is_not_re_requested(session: Session) -> None:
    item, _, vinted = _on_two_venues(session)
    record_delisted(session, vinted.id, now=NOW)
    outcome = request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    assert outcome.requested == ()
    assert outcome.already_down == 1


# ---------------------------------------------------------------- confirmation


def test_only_a_venue_confirmation_clears_the_hazard(session: Session) -> None:
    item, _, vinted = _on_two_venues(session)
    mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    assert hazards(session)
    record_delisted(session, vinted.id, now=NOW)
    assert hazards(session) == ()


def test_a_failed_delist_is_distinguishable_from_an_untried_one(session: Session) -> None:
    """One is untouched, the other is actively resisting. Clearing the error would
    make them identical."""
    item, _, vinted = _on_two_venues(session)
    mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    record_delist_failure(session, vinted.id, error="429 rate limited")
    kinds = {h.kind for h in hazards(session)}
    assert kinds == {HazardKind.DELIST_FAILED}
    assert vinted.delisted_at is None


def test_a_failure_stays_in_the_work_queue(session: Session) -> None:
    item, _, vinted = _on_two_venues(session)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    record_delist_failure(session, vinted.id, error="boom")
    assert len(unresolved_delists(session)) == 1


def test_confirming_clears_the_error(session: Session) -> None:
    item, _, vinted = _on_two_venues(session)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    record_delist_failure(session, vinted.id, error="boom")
    record_delisted(session, vinted.id, now=NOW)
    assert vinted.delist_error is None
    assert unresolved_delists(session) == []


# ---------------------------------------------------------------- hazards


def test_the_dangerous_case_is_live_with_nothing_in_flight(session: Session) -> None:
    """Sold on one venue, still live on another, nobody has asked it to come down.
    Nothing is in flight, so nothing will fix it on its own."""
    _, _, _ = _on_two_venues(session)
    mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW)
    found = hazards(session)
    assert [h.kind for h in found] == [HazardKind.LIVE_AFTER_SALE]
    assert found[0].venue == "vinted"


def test_an_unsold_item_on_two_venues_is_not_a_hazard(session: Session) -> None:
    """Listing on two venues is the whole point. It only becomes dangerous on a sale."""
    _on_two_venues(session)
    assert hazards(session) == ()


def test_a_double_sale_is_reported_even_though_it_is_too_late(session: Session) -> None:
    """Not preventable from here. Reported so it is not discovered from a buyer's
    message."""
    _on_two_venues(session)
    mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW)
    mark_sold(session, venue="vinted", external_id="V1", sold_at=NOW + timedelta(minutes=5))
    assert HazardKind.SOLD_TWICE in {h.kind for h in hazards(session)}


def test_pending_is_distinguished_from_never_asked(session: Session) -> None:
    """Benign for minutes, a hazard for hours. The caller decides using requested_at,
    which it cannot do if both look the same."""
    item, _, _ = _on_two_venues(session)
    mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW)
    request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    found = hazards(session)
    assert found[0].kind is HazardKind.DELIST_PENDING
    assert found[0].requested_at == NOW


def test_hazards_are_answerable_from_state_alone(session: Session) -> None:
    """No events replayed, no process needed to have been running. This is what makes
    the view correct after a crash or a missed webhook."""
    item = _item(session)
    session.add(
        OwnListings(
            inventory_id=item.id,
            venue="ebay",
            external_id="E9",
            ask_pence=4000,
            listed_at=NOW,
            sold_at=NOW,
        )
    )
    session.add(
        OwnListings(
            inventory_id=item.id,
            venue="vinted",
            external_id="V9",
            ask_pence=4000,
            listed_at=NOW,
        )
    )
    session.flush()
    assert [h.kind for h in hazards(session)] == [HazardKind.LIVE_AFTER_SALE]


def test_items_do_not_contaminate_each_other(session: Session) -> None:
    first, _, _ = _on_two_venues(session)
    second = _item(session)
    _listed(session, second, "vinted", "V2")
    mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW)
    found = hazards(session)
    assert {h.inventory_id for h in found} == {first.id}


def test_an_empty_book_has_no_hazards(session: Session) -> None:
    assert hazards(session) == ()
