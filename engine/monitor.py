"""Low-latency monitor worker for Vinted listing polling.

The monitor uses a ``TlsSession`` for TLS impersonation and a ``ProxyPool``
for proxy rotation.  It emits structured ``ListingScannedEvent`` objects for
each new listing found.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from engine.proxy import ProxyPool
from engine.tls import TlsSession

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ListingScannedEvent",
    "MonitorConfig",
    "poll_vinted",
]

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ListingScannedEvent:
    """Emitted when a new listing is discovered during a poll cycle.

    Attributes
    ----------
    external_id:
        The listing's unique identifier on the venue.
    title:
        Listing title.
    price_pence:
        Price in integer pence (GBP).
    url:
        Full URL to the listing.
    scanned_at:
        UTC timestamp of when the listing was first seen.
    raw:
        The raw JSON payload for debugging / re-parsing.
    """

    external_id: str
    title: str
    price_pence: int
    url: str
    scanned_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MonitorConfig:
    """Configuration for a single monitor worker.

    Attributes
    ----------
    keyword:
        Search keyword.
    max_price_pence:
        Maximum price in pence, or ``None`` for no limit.
    poll_interval_seconds:
        Seconds between poll cycles.
    """

    keyword: str
    max_price_pence: int | None = None
    poll_interval_seconds: float = 5.0


# ---------------------------------------------------------------------------
# Polling logic
# ---------------------------------------------------------------------------

_VINTED_SEARCH_URL = "https://www.vinted.fr/api/v2/catalog/items"


async def poll_vinted(
    keyword: str,
    max_price: float | None,
    tls_session: TlsSession,
    proxy_pool: ProxyPool,
) -> list[ListingScannedEvent]:
    """Execute a single poll against the Vinted catalog search endpoint.

    Parameters
    ----------
    keyword:
        Search term.
    max_price:
        Maximum price in GBP (float).  ``None`` means no limit.
    tls_session:
        A ``TlsSession`` instance for TLS impersonation.
    proxy_pool:
        A ``ProxyPool`` instance for proxy rotation.

    Returns
    -------
    A list of ``ListingScannedEvent`` objects, one per item in the response.
    Returns an empty list on any error (logged but not raised).
    """
    proxy = proxy_pool.get_proxy()
    params: dict[str, str] = {
        "search_text": keyword,
        "per_page": "20",
        "page": "1",
    }
    if max_price is not None:
        # Convert float GBP to integer pence for the API (Vinted expects cents)
        max_price_cents = int(round(max_price * 100))
        params["price_to"] = str(max_price_cents)

    try:
        response = tls_session.request(
            "GET",
            _VINTED_SEARCH_URL,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
    except Exception:
        proxy_pool.mark_failed(proxy)
        return []

    if response.status_code != 200:
        proxy_pool.mark_failed(proxy)
        return []

    try:
        data = response.json()  # type: ignore[no-untyped-call]
    except (json.JSONDecodeError, ValueError):
        return []

    items = data.get("items", []) if isinstance(data, dict) else []
    events: list[ListingScannedEvent] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        external_id = str(item.get("id", ""))
        if not external_id:
            continue
        title = str(item.get("title", ""))
        # Price comes as a dict with "amount" (float) and "currency_code"
        price_dict = item.get("price", {})
        if isinstance(price_dict, dict):
            amount = price_dict.get("amount", 0)
        else:
            amount = price_dict
        price_pence = int(round(float(amount) * 100))
        url = str(item.get("url", ""))
        events.append(
            ListingScannedEvent(
                external_id=external_id,
                title=title,
                price_pence=price_pence,
                url=url,
                raw=item,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Continuous polling loop
# ---------------------------------------------------------------------------


async def run_monitor_loop(
    config: MonitorConfig,
    tls_session: TlsSession,
    proxy_pool: ProxyPool,
    *,
    on_event: Callable[[ListingScannedEvent], None] | None = None,
) -> None:
    """Run an infinite polling loop for the given *config*.

    Parameters
    ----------
    config:
        Monitor configuration.
    tls_session:
        TLS session to use.
    proxy_pool:
        Proxy pool to use.
    on_event:
        Optional callback invoked with each ``ListingScannedEvent``.
        If ``None``, events are printed to stdout.
    """
    while True:
        events = await poll_vinted(
            keyword=config.keyword,
            max_price=(
                config.max_price_pence / 100.0 if config.max_price_pence is not None else None
            ),
            tls_session=tls_session,
            proxy_pool=proxy_pool,
        )
        for event in events:
            if on_event is not None:
                on_event(event)
            else:
                print(
                    f"[{event.scanned_at.isoformat()}] {event.title} "
                    f"– £{event.price_pence / 100:.2f} – {event.url}"
                )
        await asyncio.sleep(config.poll_interval_seconds)
