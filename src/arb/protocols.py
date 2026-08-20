"""Venue protocols. The only forward-compatibility concession in Phase 1.

Four roles, split so a venue implements exactly what it does. Vinted is a
`BuyVenue` and a `SellVenue`; eBay is a `CompSource` and a `SellVenue`.

`CompSource` is separate from `SellVenue` rather than folded into it, because
comp sourcing and listing are independently substitutable -- SoldComps and Apify
are comp sources that are not sell venues at all. Keeping them apart is what lets
the comps overflow provider be swapped without touching publishing.

Nothing here knows about HTTP, credentials, or rate limits. Adapters own that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arb.models import (
        CompQuery,
        Listing,
        ListingDraft,
        ListingFilter,
        SoldObservation,
    )

__all__ = ["BuyVenue", "CompSource", "FeeModel", "SellVenue"]


@runtime_checkable
class FeeModel(Protocol):
    """A venue's cost of selling, at a version we can audit later.

    `qty` is present from day one so that wholesale and bundle economics is
    `qty=N` rather than a refactor. `version` is the content hash of the fee YAML
    and is stamped onto every opportunity, so that when the fee table turns out to
    be wrong we can find exactly which historical scores it poisoned.
    """

    @property
    def version(self) -> str: ...

    def fees_pence(self, price_pence: int, qty: int = 1) -> int: ...


@runtime_checkable
class CompSource(Protocol):
    """A source of completed sales."""

    @property
    def name(self) -> str: ...

    def sold_comps(self, query: CompQuery) -> Sequence[SoldObservation]: ...


@runtime_checkable
class BuyVenue(Protocol):
    """A venue we source stock from."""

    @property
    def name(self) -> str: ...

    def search(self, listing_filter: ListingFilter) -> Sequence[Listing]: ...


@runtime_checkable
class SellVenue(Protocol):
    """A venue we sell on."""

    @property
    def name(self) -> str: ...

    def fee_model(self) -> FeeModel: ...

    def create_listing(self, draft: ListingDraft) -> str: ...

    def reprice(self, external_id: str, price_pence: int) -> None: ...
