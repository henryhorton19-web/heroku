"""The append-only comps cache.

This module has one job and one prohibition. It appends raw payloads, and it never
updates, deletes, or deduplicates.

SoldComps exposes roughly ninety days of history. Anything older than that is gone
from the API permanently, so the only way this project ever holds a longer series is
by having written it down at the time. That makes this table the single most
valuable object in the database and the only one that cannot be reconstructed --
which is why the write path is deliberately narrow and there is no delete path at
all.

Storing the raw payload rather than only the parsed rows is the same instinct: when
the parser turns out to be wrong, the original bytes are still there to re-parse.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from arb.db import CompsCache
from arb.models import utcnow

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.orm import Session

    from arb.models import CompQuery

__all__ = ["append_payload", "fresh_payloads", "payloads_for"]


def append_payload(
    session: Session,
    *,
    query: CompQuery,
    source: str,
    payload: object,
    fetched_at: datetime | None = None,
) -> CompsCache:
    """Append one raw response. Never overwrites an existing row.

    The same query fetched twice produces two rows on purpose -- that repetition is
    what turns a cache into a time series.
    """
    row = CompsCache(
        query_hash=query.query_hash,
        source=source,
        payload=json.dumps(payload, sort_keys=True, separators=(",", ":")),
        fetched_at=fetched_at or utcnow(),
    )
    session.add(row)
    session.flush()
    return row


def payloads_for(session: Session, query: CompQuery, *, limit: int = 50) -> Sequence[CompsCache]:
    """Every cached response for a query, newest first."""
    stmt = (
        select(CompsCache)
        .where(CompsCache.query_hash == query.query_hash)
        .order_by(CompsCache.fetched_at.desc(), CompsCache.id.desc())
        .limit(limit)
    )
    return session.scalars(stmt).all()


def fresh_payloads(
    session: Session, query: CompQuery, *, not_before: datetime
) -> Sequence[CompsCache]:
    """Cached responses fetched at or after `not_before`.

    This is what makes a 100-request monthly quota workable: a repeated query inside
    the freshness window is answered from disk and costs nothing.
    """
    stmt = (
        select(CompsCache)
        .where(CompsCache.query_hash == query.query_hash, CompsCache.fetched_at >= not_before)
        .order_by(CompsCache.fetched_at.desc(), CompsCache.id.desc())
    )
    return session.scalars(stmt).all()
