"""Verticals and the synthetic seed.

**Verticals is the niche finder**, and it needs no new collection. `category_id`,
`country` and `favourites` have been captured on every listing since the first scan
for exactly this: which corners of the market are worth trading is a question about
data already on disk, not a feature requiring instrumentation.

The aggregate that matters is **not** "where is the margin". It is margin *against
contest*. A category with 60% margins and forty watchers per listing is a category you
lose races in; one with 25% margins and two watchers is one you win. Thin-contest
niches at lower margin beat thick-contest niches at higher, which is the same
conclusion the buy side reaches per-listing, aggregated.

**The seed generates trades that could never be mistaken for real ones.** Every row is
written with `synthetic=True`, and `provenance.gather` excludes those rows from every
count it takes. Seeding therefore cannot close a placeholder. That property is what
makes it safe to build a dashboard against generated data at all: the alternative is a
dashboard nobody can develop until the first real sale, or a dashboard whose demo data
quietly becomes its production data.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import func, select

from arb.db import Inventory, Listings, Opportunities

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

__all__ = ["Vertical", "seed_synthetic_trades", "verticals"]


class Vertical(NamedTuple):
    """One slice of the market, as the data already on disk describes it."""

    category_id: str
    country: str | None
    listings: int
    median_net_pence: int | None
    """`None` when nothing in the slice was ever priced. Reported rather than shown
    as zero -- an unpriced niche and a zero-margin one are different findings."""

    avg_contest: float | None
    """Mean favourites per listing. The denominator of the whole exercise: margin
    without contest is half a picture."""


def verticals(session: Session, *, limit: int = 15, min_listings: int = 3) -> list[Vertical]:
    """Aggregate what has been seen, by category and country.

    `min_listings` exists because a category with one listing has no median worth
    reading. It is a display threshold, not a judgement -- the rows are still there.
    """
    stmt = (
        select(
            Listings.category_id,
            Listings.country,
            func.count(func.distinct(Listings.id)),
            func.avg(Listings.favourites),
        )
        .where(Listings.category_id.is_not(None))
        .group_by(Listings.category_id, Listings.country)
        .having(func.count(func.distinct(Listings.id)) >= min_listings)
        .order_by(func.count(func.distinct(Listings.id)).desc())
        .limit(limit)
    )

    found: list[Vertical] = []
    for category_id, country, count, avg_favourites in session.execute(stmt):
        nets = list(
            session.scalars(
                select(Opportunities.net_pence)
                .join(Listings, Opportunities.listing_id == Listings.id)
                .where(Listings.category_id == category_id)
                .order_by(Opportunities.net_pence)
            ).all()
        )
        median = nets[len(nets) // 2] if nets else None
        found.append(
            Vertical(
                category_id=str(category_id),
                country=str(country) if country else None,
                listings=int(count),
                median_net_pence=int(median) if median is not None else None,
                avg_contest=float(avg_favourites) if avg_favourites is not None else None,
            )
        )
    return found


def _spread(index: int, salt: str, low: int, high: int) -> int:
    """A stable pseudo-random integer in [low, high) derived from an index.

    Hashed rather than drawn from `random`, because what is wanted here is not
    randomness but *reproducibility*: row 7 must be the same row on every machine and
    every run, so a demo can be reasoned about and a test can assert on it. A seeded
    RNG would also do that, but it carries sequence state -- generating 20 rows and
    generating 40 would disagree about the first 20 -- while a hash of the index does
    not. It is also honest about what it is: this data is derived, not sampled.
    """
    digest = hashlib.sha256(f"{salt}:{index}".encode()).digest()
    return low + int.from_bytes(digest[:8], "big") % max(high - low, 1)


def seed_synthetic_trades(session: Session, *, now: datetime, count: int = 40) -> int:
    """Generate plausible completed trades so the books and dashboard have shape.

    Every row is `synthetic=True`. Nothing here is a measurement, and the register is
    written so that no amount of seeding can make it look like one.

    Deterministic per index, so a demo is reproducible and a test can assert on it.
    The distribution is loosely realistic -- most trades small and quick, a long tail
    of stock that sits -- because a dashboard tested only against tidy data hides
    exactly the layout problems that real data causes.
    """
    created = 0
    for index in range(count):
        cost = _spread(index, "cost", 500, 4_000)
        # Cubed to skew: most stock clears quickly, a few pieces sit for months.
        # A flat distribution would hide the ageing view, which is the one thing on
        # the dashboard that only matters when the tail is long.
        days_held = 2 + (_spread(index, "days", 0, 100) ** 3) // 10_000
        acquired = now - timedelta(days=days_held + _spread(index, "age", 0, 40))
        sold = index % 5 != 0
        gross = cost * _spread(index, "gross", 16, 34) // 10 if sold else None
        settled = sold and index % 3 == 0
        session.add(
            Inventory(
                cost_pence=cost,
                qty=1,
                state="sold" if sold else ("listed" if index % 2 else "in_transit"),
                acquired_at=acquired,
                listed_at=acquired + timedelta(days=2) if sold else None,
                sold_at=acquired + timedelta(days=days_held) if sold else None,
                gross_pence=gross,
                actual_fees_pence=(gross * 13) // 100 if settled and gross else None,
                actual_ship_pence=320 if settled else None,
                synthetic=True,
            )
        )
        created += 1
    session.flush()
    return created
