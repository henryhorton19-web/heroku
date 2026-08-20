"""Vinted as a `SellVenue`. The second sell side, and the first real test of the
cross-venue safety net.

Vinted is the natural second venue for two reasons that have nothing to do with
volume. It is already a `BuyVenue`, so the session and rate limiting exist. And
**Vinted-native sold prices are only reachable if you sell there** — the comp side is
currently eBay-only, so listing here eventually improves valuation for Vinted-sourced
stock, which is all of it.

**Publishing here registers the listing in `own_listings` before anything else.** The
row is what `crossvenue.hazards()` reads, and a listing that exists on a venue but not
in that table is invisible to the double-sale check — which is the failure the whole
subsystem exists to prevent. Registration is therefore not an afterthought of
publishing; it is the first step of it.

**Draft mapping is pure and separate from the network call**, exactly as
`sourcing/vinted.to_listing` is. Everything that can go wrong in translation is
testable without a session.

**What is deliberately not here: automated re-listing to refresh visibility.** Vinted's
feed rewards recency and the temptation is obvious. `docs/SCOPE.md` excludes reposting
to defeat duplicate-listing detection, and that exclusion is not negotiable by a
convenient framing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, Protocol

from arb.db import OwnListings
from arb.models import ConditionBand

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from arb.models import ListingDraft

__all__ = [
    "BAND_TO_VINTED_STATUS",
    "OwnListingRef",
    "VintedListingPayload",
    "VintedSellVenue",
    "register_own_listing",
    "to_vinted_payload",
]

BAND_TO_VINTED_STATUS: dict[ConditionBand, int] = {
    ConditionBand.NEW_WITH_TAGS: 6,
    ConditionBand.NEW_WITHOUT_TAGS: 1,
    ConditionBand.VERY_GOOD: 2,
    ConditionBand.GOOD: 3,
    ConditionBand.SATISFACTORY: 4,
}
"""The inverse of `models.VINTED_STATUS_TO_BAND`. Keyed on band and producing the
numeric id, because Vinted's labels are locale-dependent and the ids are not -- the
same reason the read side never matches on titles."""

MAX_TITLE = 60
MAX_DESCRIPTION = 3000


class VintedListingPayload(NamedTuple):
    """What a Vinted upload needs. A data object, so the mapping stays testable."""

    title: str
    description: str
    price_major: str
    """Vinted's API takes major units as a decimal string. Converted once, here, at
    the boundary -- pence stay pence everywhere inside."""

    status_id: int
    brand: str
    size: str
    photo_paths: tuple[str, ...]


def to_vinted_payload(draft: ListingDraft) -> VintedListingPayload | None:
    """Map a draft to a Vinted upload. `None` when it cannot be represented.

    Returns `None` rather than substituting a default for an unmapped condition band.
    A wrongly-banded listing is compared against the wrong comps by every buyer and by
    us, and nothing downstream can detect it -- the same reasoning that makes
    `condition_from_label` return `None` on the read side.
    """
    if draft.condition_band is None:
        return None
    status_id = BAND_TO_VINTED_STATUS.get(draft.condition_band)
    if status_id is None:
        return None
    if not draft.image_paths:
        # Vinted rejects a listing with no photos, and finding that out from the API
        # after building a payload wastes a request against a rate limit that exists
        # to keep the account alive.
        return None

    price = draft.price_pence
    return VintedListingPayload(
        title=draft.title[:MAX_TITLE],
        description=draft.description[:MAX_DESCRIPTION],
        price_major=f"{price // 100}.{price % 100:02d}",
        status_id=status_id,
        brand=draft.brand,
        size=draft.size,
        photo_paths=draft.image_paths,
    )


class OwnListingRef(NamedTuple):
    """Where one of our listings lives. Grouped rather than passed as five
    arguments, the same pattern and reason as `ScoreContext`."""

    inventory_id: int
    venue: str
    external_id: str
    ask_pence: int
    listed_at: datetime


def register_own_listing(session: Session, ref: OwnListingRef) -> int:
    """Record one of our listings so the double-sale check can see it.

    Called as the *first* step of publishing, not the last. A listing live on a venue
    but absent from `own_listings` is invisible to `crossvenue.hazards()`, so a crash
    between publishing and registering would leave exactly the item most likely to be
    sold twice untracked. Registering first means the worst case is a row for a
    listing that failed to publish, which is harmless and self-correcting.
    """
    row = OwnListings(
        inventory_id=ref.inventory_id,
        venue=ref.venue,
        external_id=ref.external_id,
        ask_pence=ref.ask_pence,
        listed_at=ref.listed_at,
    )
    session.add(row)
    session.flush()
    return row.id


class _UploadClient(Protocol):
    """The slice of a Vinted write client this adapter needs.

    Narrow on purpose: it is the whole surface to stub in a test, and the whole
    surface to reimplement if the wrapper changes. The read side does the same.
    """

    def upload(self, payload: VintedListingPayload) -> str: ...

    def delete(self, external_id: str) -> bool: ...


class VintedSellVenue:
    """A `SellVenue` over Vinted's write endpoints.

    Automated access is against Vinted's terms; the realistic exposure is the trading
    account. Requests are paced by the caller's rate limit, and nothing here retries
    aggressively -- a write that failed is surfaced rather than hammered.
    """

    def __init__(self, client: _UploadClient) -> None:
        self._client = client

    @property
    def name(self) -> str:
        return "vinted"

    def publish(self, draft: ListingDraft) -> str | None:
        """Upload a draft. Returns the venue's listing id, or `None` if unmappable."""
        payload = to_vinted_payload(draft)
        if payload is None:
            return None
        return self._client.upload(payload)

    def delist(self, external_id: str) -> bool:
        """Take a listing down. The call `crossvenue` drives on a sale elsewhere.

        Returns the venue's answer rather than raising on a refusal, because the
        caller must record the difference between confirmed-down and tried-and-failed
        -- and a raised exception loses that distinction at the point it matters.
        """
        return self._client.delete(external_id)
