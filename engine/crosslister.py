"""Multi-channel cross-lister and auto-delist synchronisation.

Provides async functions to publish listings to multiple venues and to immediately
delist an item on a given venue when a sale is detected elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from engine.config import get_engine_settings
from engine.proxy import ProxyPool
from engine.tls import TlsSession

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CrossListerError",
    "DelistResult",
    "delist_item",
    "publish_listing",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CrossListerError(Exception):
    """Raised when a cross-listing or delist operation fails."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DelistResult:
    """Outcome of a delist attempt.

    Attributes
    ----------
    success:
        ``True`` if the delist request was accepted.
    inventory_id:
        The inventory identifier.
    venue:
        The venue name (e.g. ``"vinted"``, ``"ebay"``).
    external_id:
        The listing's external identifier on that venue.
    error:
        Error description if ``success is False``, else ``None``.
    """

    success: bool
    inventory_id: str
    venue: str
    external_id: str
    error: str | None = None


# ---------------------------------------------------------------------------
# Venue-specific delist endpoints
# ---------------------------------------------------------------------------

_VENUE_DELIST_ENDPOINTS: dict[str, str] = {
    "vinted": "https://www.vinted.fr/api/v2/items/{external_id}/delete",
    "ebay": "https://api.ebay.com/sell/inventory/v1/offer/{external_id}/withdraw",
    "depop": "https://api.depop.com/v1/items/{external_id}/deactivate",
    "poshmark": "https://api.poshmark.com/v1/listings/{external_id}/delete",
    "mercari": "https://api.mercari.com/v1/items/{external_id}/delete",
}


async def delist_item(
    inventory_id: str,
    venue: str,
    external_id: str,
    tls_session: TlsSession,
    proxy_pool: ProxyPool,
) -> DelistResult:
    """Immediately delist an item on the given venue.

    Parameters
    ----------
    inventory_id:
        The internal inventory identifier.
    venue:
        The venue name (must be a key in ``_VENUE_DELIST_ENDPOINTS``).
    external_id:
        The listing's external identifier on that venue.
    tls_session:
        TLS impersonation session.
    proxy_pool:
        Proxy pool for the request.

    Returns
    -------
    ``DelistResult`` with the outcome.

    Raises
    ------
    CrossListerError
        If the venue is unknown or the request fails.
    """
    endpoint_template = _VENUE_DELIST_ENDPOINTS.get(venue)
    if endpoint_template is None:
        raise CrossListerError(f"Unknown venue: {venue!r}")

    url = endpoint_template.format(external_id=external_id)
    proxy = proxy_pool.get_proxy()

    try:
        resp = tls_session.request(
            "POST",
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
    except Exception as exc:
        proxy_pool.mark_failed(proxy)
        return DelistResult(
            success=False,
            inventory_id=inventory_id,
            venue=venue,
            external_id=external_id,
            error=f"Request failed: {exc}",
        )

    if resp.status_code in (200, 204):
        return DelistResult(
            success=True,
            inventory_id=inventory_id,
            venue=venue,
            external_id=external_id,
        )

    proxy_pool.mark_failed(proxy)
    return DelistResult(
        success=False,
        inventory_id=inventory_id,
        venue=venue,
        external_id=external_id,
        error=f"Delist returned status {resp.status_code}",
    )


async def publish_listing(
    item_data: dict[str, Any],
    target_venues: list[str],
) -> dict[str, str]:
    """Publish a listing to multiple target venues.

    .. note::
        This is a stub implementation.  Real venue-specific adapters will be
        added in a later phase.

    Parameters
    ----------
    item_data:
        A dictionary containing listing details (title, description, price, images, etc.).
    target_venues:
        List of venue names to publish to.

    Returns
    -------
    A mapping from venue name to external listing id (or empty string on failure).
    """
    # Stub: return empty results for now
    return {venue: "" for venue in target_venues}
