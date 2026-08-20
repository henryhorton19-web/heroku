"""Vinted mapping. Tested against stand-ins matching the wrapper's dataclasses,
so no session, no network, no wrapper import."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from arb.models import ConditionBand, ListingFilter, Venue
from arb.sourcing.vinted import VintedBuyVenue, condition_from_label, to_listing


@dataclass
class _Price:
    amount: str | None
    currency_code: str = "GBP"


@dataclass
class _User:
    id: int


@dataclass
class _Item:
    id: int = 123
    title: str = "Nike Air Max 90 White"
    price: object = None
    total_item_price: object = None
    brand_title: str | None = "Nike"
    size_title: str | None = "M"
    status: str = "Very good"
    url: str = "https://www.vinted.co.uk/items/123"
    favourite_count: int = 12
    view_count: int = 340
    user: object = None

    def __post_init__(self) -> None:
        if self.price is None:
            self.price = _Price("12.00")
        if self.total_item_price is None:
            self.total_item_price = _Price("13.85")
        if self.user is None:
            self.user = _User(999)


def test_maps_the_core_fields() -> None:
    listing = to_listing(_Item())
    assert listing is not None
    assert listing.venue is Venue.VINTED
    assert listing.external_id == "123"
    assert listing.price_pence == 1200
    assert listing.attrs.brand_norm == "nike"
    assert listing.attrs.size_norm == "M"


def test_total_price_is_captured_separately_from_headline() -> None:
    """Vinted's headline price excludes buyer protection. Scoring on the headline
    overstates every margin by roughly the fee."""
    listing = to_listing(_Item())
    assert listing is not None
    assert listing.total_pence == 1385
    assert listing.total_pence > listing.price_pence


def test_forward_capture_fields_are_populated() -> None:
    """These are point-in-time and cannot be reconstructed once the listing changes."""
    listing = to_listing(_Item())
    assert listing is not None
    assert listing.seller_id == "999"
    assert listing.favourites == 12
    assert listing.views == 340


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("New with tags", ConditionBand.NEW_WITH_TAGS),
        ("very good", ConditionBand.VERY_GOOD),
        ("  Satisfactory  ", ConditionBand.SATISFACTORY),
    ],
)
def test_condition_labels_map_case_insensitively(label: str, expected: ConditionBand) -> None:
    assert condition_from_label(label) is expected


@pytest.mark.parametrize("label", ["Reasonable", "", None, "Gebraucht"])
def test_unknown_condition_labels_map_to_none_not_a_guess(label: str | None) -> None:
    """A wrongly banded item is valued against the wrong comps and nothing
    downstream can detect it."""
    assert condition_from_label(label) is None


def test_plain_string_prices_are_accepted() -> None:
    """The wrapper types price as `Price | str`; both occur."""
    listing = to_listing(_Item(price="9.99", total_item_price="11.20"))
    assert listing is not None
    assert listing.price_pence == 999


def test_unpriceable_items_are_dropped() -> None:
    assert to_listing(_Item(price=_Price(None))) is None


def test_untitled_items_are_dropped() -> None:
    assert to_listing(_Item(title="")) is None


def test_missing_size_is_allowed() -> None:
    listing = to_listing(_Item(size_title=None))
    assert listing is not None
    assert listing.attrs.size_norm is None


def test_first_and_last_seen_start_equal() -> None:
    listing = to_listing(_Item())
    assert listing is not None
    assert listing.first_seen == listing.last_seen


class _StubClient:
    def __init__(self, items: list[_Item]) -> None:
        self._items = items
        self.calls: list[dict[str, object]] = []

    def search(
        self,
        *,
        query: str | None,
        page: int,
        per_page: int,
        price_from: float | None,
        price_to: float | None,
    ) -> object:
        self.calls.append(
            {"query": query, "page": page, "per_page": per_page, "from": price_from, "to": price_to}
        )

        class _Response:
            items = self._items

        return _Response()


def test_venue_maps_a_search_response() -> None:
    venue = VintedBuyVenue(_StubClient([_Item(id=1), _Item(id=2)]))
    listings = venue.search(ListingFilter(query="nike", limit=50))
    assert [listing.external_id for listing in listings] == ["1", "2"]
    assert venue.name == "vinted"


def test_price_filters_are_converted_to_major_units() -> None:
    client = _StubClient([])
    VintedBuyVenue(client).search(
        ListingFilter(query="nike", min_price_pence=500, max_price_pence=2500)
    )
    assert client.calls[0]["from"] == 5.0
    assert client.calls[0]["to"] == 25.0


def test_unmappable_items_are_skipped_not_fatal() -> None:
    venue = VintedBuyVenue(_StubClient([_Item(id=1), _Item(id=2, price=_Price(None))]))
    assert len(venue.search(ListingFilter(query="nike"))) == 1
