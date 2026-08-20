"""Vinted as a `SellVenue`.

The mapping is pure and tested without a session, as the read side is. The property
worth protecting: **an unmappable draft returns `None` rather than a guessed value.**
A wrongly-banded listing is compared against the wrong comps by every buyer and by us,
and nothing downstream can detect it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from arb.db import Inventory
from arb.models import VINTED_STATUS_TO_BAND, ConditionBand, ListingDraft
from arb.selling.crossvenue import (
    hazards,
    mark_sold,
    record_delisted,
    request_delists,
)
from arb.selling.vinted_sell import (
    BAND_TO_VINTED_STATUS,
    OwnListingRef,
    VintedListingPayload,
    VintedSellVenue,
    register_own_listing,
    to_vinted_payload,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _draft(**overrides: object) -> ListingDraft:
    base: dict[str, object] = {
        "title": "Nike Air Max 90 White",
        "description": "Very good condition.",
        "category_id": "1904",
        "price_pence": 4250,
        "size": "9",
        "condition_band": ConditionBand.VERY_GOOD,
        "brand": "Nike",
        "image_paths": ("a.jpg",),
    }
    base.update(overrides)
    return ListingDraft.model_validate(base)


class _StubClient:
    def __init__(self, *, delete_ok: bool = True) -> None:
        self.uploaded: list[VintedListingPayload] = []
        self.deleted: list[str] = []
        self._delete_ok = delete_ok

    def upload(self, payload: VintedListingPayload) -> str:
        self.uploaded.append(payload)
        return "V123"

    def delete(self, external_id: str) -> bool:
        self.deleted.append(external_id)
        return self._delete_ok


# ---------------------------------------------------------------- mapping


def test_pence_become_major_units_at_the_boundary() -> None:
    """Pence stay pence everywhere inside; conversion happens once, here."""
    payload = to_vinted_payload(_draft(price_pence=4250))
    assert payload is not None
    assert payload.price_major == "42.50"


def test_a_round_price_keeps_two_decimals() -> None:
    payload = to_vinted_payload(_draft(price_pence=4000))
    assert payload is not None
    assert payload.price_major == "40.00"


def test_a_sub_pound_price_is_formatted_correctly() -> None:
    payload = to_vinted_payload(_draft(price_pence=5))
    assert payload is not None
    assert payload.price_major == "0.05"


def test_condition_maps_to_the_numeric_status_id() -> None:
    """Keyed on ids, not labels, because Vinted's labels are locale-dependent."""
    payload = to_vinted_payload(_draft(condition_band=ConditionBand.NEW_WITH_TAGS))
    assert payload is not None
    assert payload.status_id == 6


def test_the_band_map_is_the_inverse_of_the_read_side() -> None:
    assert {band: status for status, band in VINTED_STATUS_TO_BAND.items()} == (
        BAND_TO_VINTED_STATUS
    )


def test_a_draft_without_a_condition_is_unmappable() -> None:
    assert to_vinted_payload(_draft(condition_band=None)) is None


def test_a_draft_without_photos_is_unmappable() -> None:
    """Vinted rejects it, and finding that out from the API wastes a request against
    a rate limit that exists to keep the account alive."""
    assert to_vinted_payload(_draft(image_paths=())) is None


def test_a_title_valid_for_ebay_is_truncated_for_vinted() -> None:
    """`ListingDraft` caps titles at 80 -- eBay's limit. Vinted's is 60, so a draft
    that is perfectly valid to publish on one venue must still be shortened for the
    other. Per-venue limits belong in the venue adapter, not in the shared model."""
    payload = to_vinted_payload(_draft(title="x" * 80, description="y" * 5000))
    assert payload is not None
    assert len(payload.title) == 60
    assert len(payload.description) <= 3000


# ---------------------------------------------------------------- the venue


def test_publishing_returns_the_venue_id() -> None:
    client = _StubClient()
    assert VintedSellVenue(client).publish(_draft()) == "V123"
    assert len(client.uploaded) == 1


def test_an_unmappable_draft_is_not_uploaded() -> None:
    client = _StubClient()
    assert VintedSellVenue(client).publish(_draft(condition_band=None)) is None
    assert client.uploaded == []


def test_delist_returns_the_venue_answer_rather_than_raising() -> None:
    """The caller must record confirmed-down separately from tried-and-failed, and a
    raised exception loses that distinction where it matters most."""
    assert VintedSellVenue(_StubClient(delete_ok=False)).delist("V1") is False
    assert VintedSellVenue(_StubClient()).delist("V1") is True


# ---------------------------------------------------------------- registration


def test_a_published_listing_is_visible_to_the_double_sale_check(
    session: Session,
) -> None:
    """The whole reason registration is the first step of publishing. A listing live
    on a venue but absent here is invisible to the hazard check."""
    item = Inventory(cost_pence=1200, qty=1, state="listed", acquired_at=NOW)
    session.add(item)
    session.flush()
    register_own_listing(session, OwnListingRef(item.id, "ebay", "E1", 4000, NOW))
    register_own_listing(session, OwnListingRef(item.id, "vinted", "V1", 4000, NOW))
    mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW)
    assert len(hazards(session)) == 1


def test_the_full_loop_clears_the_hazard(session: Session) -> None:
    item = Inventory(cost_pence=1200, qty=1, state="listed", acquired_at=NOW)
    session.add(item)
    session.flush()
    register_own_listing(session, OwnListingRef(item.id, "ebay", "E1", 4000, NOW))
    vinted_row = register_own_listing(session, OwnListingRef(item.id, "vinted", "V1", 4000, NOW))
    mark_sold(session, venue="ebay", external_id="E1", sold_at=NOW)
    outcome = request_delists(session, inventory_id=item.id, exclude_venue="ebay", now=NOW)
    assert outcome.requested == (vinted_row,)

    assert VintedSellVenue(_StubClient()).delist("V1")
    record_delisted(session, vinted_row, now=NOW)
    assert hazards(session) == ()
