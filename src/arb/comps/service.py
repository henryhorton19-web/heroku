"""Comp acquisition, cache first.

The free tier is 100 requests a month. That number is the design constraint for this
whole module: a scan over 200 listings cannot be 200 API calls, so every lookup
checks the append-only cache before it reaches for the network, and identical queries
within a single run collapse onto the first fetch.

Two behaviours that matter more than they look:

**Every fetch is written to the cache before it is parsed.** If the parser is wrong,
the raw bytes are still on disk to re-parse. If the parser is right, the row is still
the only record of what the market looked like on that day once the source's 90-day
window rolls past it.

**Quota exhaustion stops the run, it does not fail it.** A `quota_exceeded` response
means the month's allowance is gone and `Retry-After` may be days, so retrying is
pointless and continuing would produce a buy list quietly missing everything after
the cutoff. `CompsService` records that it happened, serves whatever is already
cached, and the caller reports it — a short buy list you know is short beats a short
buy list you think is complete.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from arb.comps.cache import append_payload, fresh_payloads
from arb.comps.soldcomps import QuotaExceededError
from arb.models import utcnow

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import timedelta

    from sqlalchemy.orm import Session

    from arb.models import CompQuery, SoldObservation

__all__ = ["CachedCompSource", "CompsResult", "CompsService"]


class CachedCompSource(Protocol):
    """A comp source that exposes its raw payload as well as parsed observations.

    Raw access is what makes the append-only cache possible. A source that only
    returned parsed rows would leave nothing to re-parse when the parser is wrong.
    """

    @property
    def name(self) -> str: ...

    def raw_search(self, keyword: str, *, page: int = 1) -> object: ...

    def parse(self, payload: object, query: CompQuery) -> Sequence[SoldObservation]: ...


@dataclass
class CompsResult:
    """Running tally of how a scan's comps were obtained.

    Reported by `arb scan` so a thin buy list is attributable: cache-heavy is normal,
    fetch-heavy means the freshness window is too short, and `quota_exhausted` means
    the list is incomplete regardless of how it looks.
    """

    cache_hits: int = 0
    fetches: int = 0
    quota_exhausted: bool = False


class CompsService:
    """Cache-first comp lookup over a `CachedCompSource`."""

    def __init__(
        self,
        source: CachedCompSource,
        session: Session,
        *,
        freshness: timedelta,
    ) -> None:
        self._source = source
        self._session = session
        self._freshness = freshness
        self.stats = CompsResult()

    def comps_for(self, query: CompQuery) -> list[SoldObservation]:
        """Observations for a query, from cache when fresh enough, else fetched.

        Returns an empty list rather than raising when the quota is gone, so one
        exhausted budget does not abort a scan that could still price everything it
        has already cached.
        """
        cutoff = utcnow() - self._freshness
        cached = fresh_payloads(self._session, query, not_before=cutoff)
        if cached:
            self.stats.cache_hits += 1
            return list(self._source.parse(_decode(cached[0].payload), query))

        if self.stats.quota_exhausted:
            return []

        try:
            payload = self._source.raw_search(query.search_keyword)
        except QuotaExceededError:
            self.stats.quota_exhausted = True
            return []

        self.stats.fetches += 1
        # Written before parsing: the raw bytes are the durable artefact, the parse
        # is just this version's interpretation of them.
        append_payload(self._session, query=query, source=self._source.name, payload=payload)
        return list(self._source.parse(payload, query))


def _decode(raw: str) -> object:
    return json.loads(raw)
