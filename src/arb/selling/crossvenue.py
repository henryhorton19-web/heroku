"""Cross-venue reconciliation: never sell the same item twice.

This lands **before** any second sell adapter, deliberately. Listing one item on two
venues is a few hours of adapter work and creates a failure that costs a refund, a
defect on the account, and sometimes the account itself. Building the safety net after
the thing it catches is how you find out it was needed.

**De-listing is a distributed operation and it will partially fail.** The sale lands on
eBay, the Vinted pull times out, and now a sold item is still buyable. Nothing about
that is exotic — it is the ordinary behaviour of two systems that fail independently —
so the design assumes it rather than hoping.

Three consequences shape this module:

*Intent is recorded before the call.* `delist_requested_at` is written when we decide a
listing must come down; `delisted_at` only when a venue confirms. A row with the first
and not the second is an open hazard, and `unresolved_delists` finds exactly those. The
opposite order — call, then record — loses the intent entirely on a crash, and the
hazard becomes invisible.

*A failed de-list is not retried silently forever.* It is surfaced. An automated retry
that never escalates is indistinguishable from a fix, right up until the second sale.

*Reconciliation is a query, not an event.* `hazards()` answers "what is live that
should not be" from state alone, so it is correct after a crash, a missed webhook, or a
process that was simply not running. Anything driven only by events inherits every gap
in event delivery.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import select

from arb.db import OwnListings

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.orm import Session

__all__ = [
    "DelistOutcome",
    "Hazard",
    "HazardKind",
    "hazards",
    "mark_sold",
    "record_delist_failure",
    "record_delisted",
    "request_delists",
    "unresolved_delists",
]


class HazardKind(StrEnum):
    """Ways the same item can be sold twice. Each needs a different response."""

    LIVE_AFTER_SALE = "live_after_sale"
    """Sold on one venue, still listed on another and not yet asked to come down.
    The dangerous one: nothing is in flight, so nothing will fix it on its own."""

    DELIST_FAILED = "delist_failed"
    """We asked, the venue refused or errored. Still buyable. Needs a human or a
    retry, and must not be mistaken for done."""

    DELIST_PENDING = "delist_pending"
    """Asked, not yet confirmed. Benign for minutes, a hazard for hours -- the
    caller decides using `requested_at`."""

    SOLD_TWICE = "sold_twice"
    """Already happened. Not preventable from here; reported so it is not
    discovered from a buyer's message."""


class Hazard(NamedTuple):
    inventory_id: int
    venue: str
    external_id: str
    kind: HazardKind
    requested_at: datetime | None = None
    detail: str | None = None


class DelistOutcome(NamedTuple):
    requested: tuple[int, ...]
    """`own_listings` row ids now marked for de-listing. The caller drives the venue
    APIs; this module records intent and confirmations and makes no network calls."""

    already_down: int


def mark_sold(session: Session, *, venue: str, external_id: str, sold_at: datetime) -> int | None:
    """Record that one of our listings sold. Returns the inventory id, or None.

    Returning `None` for an unknown listing rather than raising: a sale notification
    for something we do not track is worth reporting, not worth aborting a webhook
    handler over.
    """
    row = session.scalar(
        select(OwnListings).where(
            OwnListings.venue == venue, OwnListings.external_id == external_id
        )
    )
    if row is None:
        return None
    row.sold_at = sold_at
    session.flush()
    return row.inventory_id


def request_delists(
    session: Session, *, inventory_id: int, exclude_venue: str, now: datetime
) -> DelistOutcome:
    """Mark every *other* live listing for this item as needing to come down.

    Called immediately on a sale, before any venue API is touched. If the process dies
    between this and the calls, the intent survives and `unresolved_delists` finds the
    work. Doing it the other way round loses the intent on a crash, and the hazard
    becomes invisible rather than pending.
    """
    rows = session.scalars(
        select(OwnListings).where(
            OwnListings.inventory_id == inventory_id,
            OwnListings.venue != exclude_venue,
        )
    ).all()

    requested: list[int] = []
    already_down = 0
    for row in rows:
        if row.delisted_at is not None:
            already_down += 1
            continue
        if row.delist_requested_at is None:
            row.delist_requested_at = now
        requested.append(row.id)
    session.flush()
    return DelistOutcome(requested=tuple(requested), already_down=already_down)


def record_delisted(session: Session, row_id: int, *, now: datetime) -> None:
    """Confirm a venue has taken a listing down. Only a venue's answer sets this."""
    row = session.get(OwnListings, row_id)
    if row is None:
        return
    row.delisted_at = now
    row.delist_error = None
    session.flush()


def record_delist_failure(session: Session, row_id: int, *, error: str) -> None:
    """Record that a de-list attempt failed. `delisted_at` stays null, deliberately.

    A failure that cleared the error and left no other trace would look identical to
    a listing nobody has tried yet, and the difference matters: one is untouched, the
    other is actively resisting.
    """
    row = session.get(OwnListings, row_id)
    if row is None:
        return
    row.delist_error = error[:500]
    session.flush()


def unresolved_delists(session: Session) -> Sequence[OwnListings]:
    """Listings asked to come down that have not confirmed. The work queue."""
    return session.scalars(
        select(OwnListings).where(
            OwnListings.delist_requested_at.is_not(None),
            OwnListings.delisted_at.is_(None),
        )
    ).all()


def _kind_for(row: OwnListings) -> HazardKind:
    """Classify one still-live listing on an item that has sold elsewhere."""
    if row.delist_error is not None:
        return HazardKind.DELIST_FAILED
    if row.delist_requested_at is not None:
        return HazardKind.DELIST_PENDING
    return HazardKind.LIVE_AFTER_SALE


def _hazards_for_item(inventory_id: int, listings: list[OwnListings]) -> list[Hazard]:
    """Every hazard attached to one inventory item."""
    sold = [r for r in listings if r.sold_at is not None]
    if not sold:
        return []

    found = [
        Hazard(
            inventory_id=inventory_id,
            venue=row.venue,
            external_id=row.external_id,
            kind=HazardKind.SOLD_TWICE,
        )
        for row in sold[1:]
    ]
    found.extend(
        Hazard(
            inventory_id=inventory_id,
            venue=row.venue,
            external_id=row.external_id,
            kind=_kind_for(row),
            requested_at=row.delist_requested_at,
            detail=row.delist_error,
        )
        for row in listings
        if row.sold_at is None and row.delisted_at is None
    )
    return found


def hazards(session: Session) -> tuple[Hazard, ...]:
    """Everything currently at risk of a double sale. A query over state, not events.

    Being a query is the point: it is correct after a crash, after a missed webhook,
    and after a period when nothing was running at all. Anything driven purely by
    events inherits every gap in event delivery, and the gaps are exactly when this
    matters.
    """
    by_item: dict[int, list[OwnListings]] = {}
    for row in session.scalars(select(OwnListings)).all():
        by_item.setdefault(row.inventory_id, []).append(row)

    found: list[Hazard] = []
    for inventory_id, listings in sorted(by_item.items()):
        found.extend(_hazards_for_item(inventory_id, listings))
    return tuple(found)
