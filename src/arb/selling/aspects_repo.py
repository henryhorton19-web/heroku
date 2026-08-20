"""Persistence for the taxonomy aspect cache.

Kept out of `repo.py` because the buy-side write path and the sell-side cache have
nothing to do with each other, and `repo.py`'s docstring is about the decision write
path specifically.

The cache is **upsert, not append**, which is the opposite of `comps_cache` and worth
saying why. Comps are a market observation at a point in time: once SoldComps' 90-day
window rolls past, the row is the only record that day existed, so it can never be
overwritten. Taxonomy enums are eBay's current published rules — re-fetchable at any
time, and an old copy is not history, it is a stale rule that will hold your listing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from arb.db import TaxonomyAspects
from arb.selling.taxonomy import parse_aspects

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

    from arb.selling.taxonomy import CategoryAspects

__all__ = ["cached_aspects", "cached_categories", "store_aspects"]


def store_aspects(
    session: Session,
    payload: object,
    *,
    marketplace_id: str,
    category_id: str,
    fetched_at: datetime,
) -> None:
    """Cache one category's aspect payload, replacing any earlier copy."""
    tree_id = None
    tree_version = None
    if isinstance(payload, dict):
        tree_id = payload.get("categoryTreeId")
        tree_version = payload.get("categoryTreeVersion")
    values = {
        "marketplace_id": marketplace_id,
        "category_id": category_id,
        "category_tree_id": str(tree_id) if tree_id is not None else None,
        "category_tree_version": str(tree_version) if tree_version is not None else None,
        "payload": json.dumps(payload, separators=(",", ":")),
        "fetched_at": fetched_at,
    }
    stmt = sqlite_insert(TaxonomyAspects).values(**values)
    session.execute(
        stmt.on_conflict_do_update(
            index_elements=["marketplace_id", "category_id"],
            set_={
                key: stmt.excluded[key]
                for key in values
                if key not in {"marketplace_id", "category_id"}
            },
        )
    )
    session.flush()


def cached_aspects(
    session: Session, *, marketplace_id: str, category_id: str
) -> CategoryAspects | None:
    """Load and parse a cached category. `None` on a miss.

    The `None` propagates all the way to a publish refusal. That is deliberate: an
    empty aspect set would validate everything, so a cache miss silently disabling
    the gate is the one outcome worse than refusing to publish.
    """
    raw = session.scalar(
        select(TaxonomyAspects.payload).where(
            TaxonomyAspects.marketplace_id == marketplace_id,
            TaxonomyAspects.category_id == category_id,
        )
    )
    if raw is None:
        return None
    return parse_aspects(json.loads(raw), category_id=category_id)


def cached_categories(session: Session, *, marketplace_id: str) -> list[tuple[str, str | None]]:
    """Every cached category and its tree version, for `arb taxonomy list`."""
    rows = session.execute(
        select(TaxonomyAspects.category_id, TaxonomyAspects.category_tree_version)
        .where(TaxonomyAspects.marketplace_id == marketplace_id)
        .order_by(TaxonomyAspects.category_id)
    ).all()
    return [(str(category), version) for category, version in rows]
