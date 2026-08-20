"""Vinted `BuyVenue` adapter. Shapes taken from the wrapper's own dataclasses.

The mapping (`to_listing`) is a **pure function** kept separate from the network
call, so the whole translation layer is testable against recorded payloads without
respx or a session. Everything that can go wrong in mapping — missing prices,
unfamiliar condition labels, absent sizes — is exercised that way.

Three things this adapter is careful about:

**`total_item_price`, not `price`.** Vinted's headline price excludes buyer
protection. `total_item_price` is what actually leaves your account, and scoring
against the headline overstates every margin by roughly the fee.

**Condition arrives as a localised *string*, not the numeric status id.** The search
response carries `status: str` ("Very good"), where the reference tables key on ints.
Labels are matched case- and accent-insensitively against the UK English set, and an
unrecognised label maps to `None` rather than a guess — an item silently banded as
the wrong condition would be compared against the wrong comps.

**Forward capture is populated here or never.** `favourite_count`, `view_count`,
`user.id` and the seen timestamps are point-in-time values that cannot be
reconstructed once the listing changes or disappears.

Automated access is against Vinted's terms. The realistic exposure is the account, so
requests are paced and the rate limit is capped in `Settings`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from vinted import Vinted

from arb.models import Attributes, ConditionBand, Listing, Venue, utcnow
from arb.money import parse_pence
from arb.norm import norm_brand, norm_size, norm_text

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from arb.models import ListingFilter

__all__ = ["UK_CONDITION_LABELS", "VintedBuyVenue", "build_client", "to_listing"]

UK_CONDITION_LABELS: dict[str, ConditionBand] = {
    "new with tags": ConditionBand.NEW_WITH_TAGS,
    "brand new with tags": ConditionBand.NEW_WITH_TAGS,
    "new without tags": ConditionBand.NEW_WITHOUT_TAGS,
    "brand new without tags": ConditionBand.NEW_WITHOUT_TAGS,
    "very good": ConditionBand.VERY_GOOD,
    "good": ConditionBand.GOOD,
    "satisfactory": ConditionBand.SATISFACTORY,
}
"""UK English condition labels. Keys are `norm_text` output, so matching is
case- and accent-insensitive. Anything unlisted maps to None."""


def condition_from_label(raw: str | None) -> ConditionBand | None:
    """Map a Vinted condition label to a band, or `None` if unrecognised.

    Returning `None` rather than defaulting is deliberate: a wrongly banded item is
    valued against the wrong comps, and nothing downstream can detect it.
    """
    if not raw:
        return None
    return UK_CONDITION_LABELS.get(norm_text(raw))


def _amount(value: object) -> str | None:
    """Pull a decimal string out of a `Price`/`CurrencyAmount` or a bare string.

    The wrapper types these as `Price | str`, so both forms occur in the wild.
    """
    if isinstance(value, str):
        return value
    amount = getattr(value, "amount", None)
    return amount if isinstance(amount, str) else None


def to_listing(item: object, *, now: datetime | None = None) -> Listing | None:
    """Map one wrapper `Item` to a `Listing`. `None` when it cannot be priced.

    Duck-typed rather than importing the wrapper's dataclass, which keeps this
    function testable with plain stand-ins and keeps the dependency at the edge.
    """
    seen = now or utcnow()
    external_id = getattr(item, "id", None)
    title = getattr(item, "title", None)
    if external_id is None or not title:
        return None

    price_pence = parse_pence(_amount(getattr(item, "price", None)))
    if price_pence is None:
        return None
    total_pence = parse_pence(_amount(getattr(item, "total_item_price", None)))

    user = getattr(item, "user", None)
    seller_id = getattr(user, "id", None)
    brand = getattr(item, "brand_title", None) or ""
    size = getattr(item, "size_title", None)

    return Listing(
        venue=Venue.VINTED,
        external_id=str(external_id),
        url=getattr(item, "url", None),
        price_pence=price_pence,
        total_pence=total_pence,
        attrs=Attributes(
            brand_norm=norm_brand(brand),
            title_norm=norm_text(title),
            size_norm=norm_size(size) if size else None,
            condition_band=condition_from_label(getattr(item, "status", None)),
        ),
        seller_id=str(seller_id) if seller_id is not None else None,
        favourites=getattr(item, "favourite_count", None),
        views=getattr(item, "view_count", None),
        first_seen=seen,
        last_seen=seen,
    )


class _SearchClient(Protocol):
    """The slice of the wrapper this adapter uses. Narrow on purpose: it is the whole
    surface that has to be stubbed in a test or swapped if the wrapper changes."""

    def search(
        self,
        *,
        query: str | None,
        page: int,
        per_page: int,
        price_from: float | None,
        price_to: float | None,
    ) -> object: ...


class VintedBuyVenue:
    """A `BuyVenue` over the Vinted search endpoint."""

    def __init__(self, client: _SearchClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "vinted"

    def search(self, listing_filter: ListingFilter) -> Sequence[Listing]:
        response = self._client.search(
            query=listing_filter.query,
            page=1,
            per_page=listing_filter.limit,
            price_from=_major(listing_filter.min_price_pence),
            price_to=_major(listing_filter.max_price_pence),
        )
        items = getattr(response, "items", None) or []
        seen = utcnow()
        mapped = (to_listing(item, now=seen) for item in items)
        return [listing for listing in mapped if listing is not None]


def _major(pence: int | None) -> float | None:
    """Vinted's price filters take major units. Only used for a coarse server-side
    filter, never for anything that reaches the ledger."""
    return None if pence is None else pence / 100


def build_client(base_url: str) -> _SearchClient:
    """Construct the wrapper's client.

    The only place in the codebase that touches `vinted` directly. Everything else
    goes through `_SearchClient`, so the wrapper can be swapped -- and, more usefully,
    so every test runs against a stand-in with no session and no network.

    The wrapper ships no type information, so the assignment below is where an
    untyped object becomes a typed one. Narrowing it explicitly rather than returning
    it straight keeps that conversion visible instead of leaking `Any` upward.
    """
    client: _SearchClient = Vinted(base_url)
    return client
