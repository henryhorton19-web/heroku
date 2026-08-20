"""SoldComps adapter. Contract verified against sold-comps.com/docs on 20 Aug 2026.

The two behaviours worth reading before changing anything here:

**A 429 is two different errors.** The body's `code` says which. `rate_limited` is
transient and should be retried after `Retry-After`. `quota_exceeded` means the
monthly allowance is gone and `Retry-After` can be *days* -- retrying that is how a
polite client turns into a stuck one. They are separate exception types so a caller
cannot accidentally treat them alike.

**`bestOfferAccepted` marks a price as an upper bound.** eBay never discloses the
accepted offer, so those rows carry the *listed* price. They are ingested with
`price_is_upper_bound=True` and excluded from valuation by default, because leaving
them in biases every estimate upward.

Non-GBP rows are dropped rather than converted. A currency conversion using whatever
rate happened to be handy is a wrong number that looks right.

The response has no listing-start date, so `listed_at` is always `None` and
`days_to_sell` cannot be derived here. That is a property of the source, not an
omission -- see SPEC.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ConfigDict, Field

from arb.models import SoldObservation
from arb.money import GBP, parse_pence
from arb.norm import norm_brand, norm_size, norm_text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arb.models import CompQuery

__all__ = [
    "BASE_URL",
    "MAX_COUNT",
    "QuotaExceededError",
    "RateLimitedError",
    "SoldCompsClient",
    "SoldCompsError",
    "parse_response",
]

BASE_URL = "https://api.sold-comps.com"
MAX_COUNT = 200
"""Documented ceiling per page. The build plan said 240; the API says 200."""

UK_SITE = "ebay.co.uk"


class SoldCompsError(RuntimeError):
    """Any SoldComps failure."""


class RateLimitedError(SoldCompsError):
    """Per-minute limit. Transient -- back off for `retry_after` and retry."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class QuotaExceededError(SoldCompsError):
    """Monthly allowance exhausted. Do not retry: the reset can be days away."""

    def __init__(self, reset_at: str | None) -> None:
        super().__init__(f"monthly quota exhausted, resets {reset_at or 'unknown'}")
        self.reset_at = reset_at


class _Wire(BaseModel):
    """Base for wire shapes. Fields are snake_case here and aliased to the API's
    camelCase, so the codebase keeps one naming convention and the upstream names
    live in exactly one place."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class _Item(_Wire):
    item_id: str | None = Field(default=None, alias="itemId")
    title: str | None = None
    condition: str | None = None
    condition_id: int | None = Field(default=None, alias="conditionId")
    category_id: str | None = Field(default=None, alias="categoryId")
    ended_at: str | None = Field(default=None, alias="endedAt")
    sold_price: str | None = Field(default=None, alias="soldPrice")
    sold_currency: str | None = Field(default=None, alias="soldCurrency")
    shipping_price: str | None = Field(default=None, alias="shippingPrice")
    best_offer_accepted: bool = Field(default=False, alias="bestOfferAccepted")
    item_location: str | None = Field(default=None, alias="itemLocation")


class _Page(_Wire):
    has_next_page: bool = Field(default=False, alias="hasNextPage")
    items: list[_Item] = []


def parse_response(
    payload: object, *, brand_norm: str, size_norm: str | None = None
) -> list[SoldObservation]:
    """Turn one raw response into observations, dropping rows we cannot price.

    A row is dropped when it has no title, no parseable price, or a non-GBP
    currency. Dropping is deliberate: a comp we cannot trust is worse than one fewer
    comp, because the valuation floor already refuses thin sets.
    """
    page = _Page.model_validate(payload)
    out: list[SoldObservation] = []
    for item in page.items:
        if not item.title or item.sold_currency not in (GBP, None):
            continue
        price = parse_pence(item.sold_price)
        if price is None:
            continue
        out.append(
            SoldObservation(
                brand_norm=norm_brand(brand_norm),
                title_norm=norm_text(item.title),
                size_norm=norm_size(size_norm) if size_norm else None,
                condition_band=None,
                category_id=item.category_id,
                country=item.item_location,
                price_pence=price,
                ship_pence=parse_pence(item.shipping_price),
                listed_at=None,
                sold_at=_parse_ended_at(item.ended_at),
                price_is_upper_bound=item.best_offer_accepted,
            )
        )
    return out


def _parse_ended_at(raw: str | None) -> datetime | None:
    """`endedAt` is a bare date -- eBay never exposes a time of day. Anchored to
    midnight UTC so downstream arithmetic has something aware to work with."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return None


class SoldCompsClient:
    """A `CompSource` over SoldComps. Structural match, no inheritance."""

    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        site: str = UK_SITE,
        count: int = MAX_COUNT,
    ) -> None:
        if not 1 <= count <= MAX_COUNT:
            msg = f"count must be 1..{MAX_COUNT}"
            raise ValueError(msg)
        self._key = api_key
        self._site = site
        self._count = count
        self._client = client or httpx.Client(base_url=BASE_URL, timeout=30.0)

    @property
    def name(self) -> str:
        return "soldcomps"

    def raw_search(self, keyword: str, *, page: int = 1) -> object:
        """One page of raw JSON, for the append-only cache to store verbatim."""
        response = self._client.get(
            "/v1/scrape",
            params={
                "keyword": keyword,
                "ebaySite": self._site,
                "page": page,
                "count": self._count,
                "sortOrder": "endedRecently",
                # Strips eBay's loosened "matching fewer words" results. Precision
                # over recall: a loose match in the comp set moves the median.
                "exactMatch": "true",
            },
            headers={"Authorization": f"Bearer {self._key}"},
        )
        self._raise_for_status(response)
        return response.json()

    def parse(self, payload: object, query: CompQuery) -> Sequence[SoldObservation]:
        """Parse a payload -- fresh or replayed from the cache -- into observations.

        Separate from `sold_comps` so a cached payload can be re-parsed without
        spending a request, which is the whole basis of the cache-first path.
        """
        return parse_response(payload, brand_norm=query.brand_norm, size_norm=query.size_norm)

    def sold_comps(self, query: CompQuery) -> Sequence[SoldObservation]:
        return self.parse(self.raw_search(query.search_keyword), query)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            body = _safe_json(response)
            if body.get("code") == "quota_exceeded":
                reset = body.get("reset_at")
                raise QuotaExceededError(reset if isinstance(reset, str) else None)
            retry = body.get("retry_after")
            header = response.headers.get("Retry-After")
            raise RateLimitedError(
                int(retry) if isinstance(retry, int) else int(header) if header else 60
            )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            msg = f"soldcomps returned {response.status_code}"
            raise SoldCompsError(msg)


def _safe_json(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
