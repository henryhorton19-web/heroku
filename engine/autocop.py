"""AutoCop – automated checkout engine for Vinted / Adyen payment flow.

The module implements a staged purchase attempt:
1. Enforce spend cap.
2. Fetch available payment methods.
3. Create a tokenized payment session.
4. Submit the payment payload (or simulate in dry-run mode).

All I/O is async and uses ``TlsSession`` for TLS impersonation and
``ProxyPool`` for proxy rotation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from engine.config import get_engine_settings
from engine.proxy import ProxyPool
from engine.tls import TlsSession

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AutoCopError",
    "PurchaseAttemptResult",
    "attempt_checkout",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AutoCopError(Exception):
    """Raised when the checkout process cannot proceed."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PurchaseAttemptResult:
    """Outcome of a single purchase attempt.

    Attributes
    ----------
    success:
        ``True`` if the purchase was completed (or simulated in dry-run).
    listing_id:
        The listing identifier.
    price_pence:
        Price paid, in integer pence.
    transaction_id:
        Backend transaction id if available, ``None`` otherwise.
    error:
        Error description if ``success is False``, else ``None``.
    attempted_at:
        UTC timestamp of the attempt.
    """

    success: bool
    listing_id: str
    price_pence: int
    transaction_id: str | None = None
    error: str | None = None
    attempted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Checkout engine
# ---------------------------------------------------------------------------

_PAYMENT_METHODS_URL = "https://www.vinted.fr/api/v2/payment_methods"
_SESSIONS_URL = "https://www.vinted.fr/api/v2/sessions"
_CHECKOUT_URL = "https://www.vinted.fr/api/v2/checkout"


async def attempt_checkout(  # noqa: PLR0912
    listing_id: str,
    title: str,
    price_pence: int,
    tls_session: TlsSession,
    proxy_pool: ProxyPool,
    *,
    solver: Any = None,
    dry_run: bool = True,
) -> PurchaseAttemptResult:
    """Attempt to purchase *listing_id* at *price_pence*.

    Parameters
    ----------
    listing_id:
        The listing identifier (external_id) to buy.
    title:
        Human-readable listing title (for logging).
    price_pence:
        Price in integer pence.
    tls_session:
        TLS impersonation session.
    proxy_pool:
        Proxy pool for the request.
    solver:
        Optional ``CaptchaSolver`` instance for CAPTCHA handling.
    dry_run:
        If ``True``, simulate the payment step without actually charging.

    Returns
    -------
    ``PurchaseAttemptResult`` with the outcome.

    Raises
    ------
    AutoCopError
        If the spend cap is exceeded or a required configuration is missing.
    """
    settings = get_engine_settings()

    if not settings.autocop_enabled:
        raise AutoCopError("AutoCop is not enabled. Set ENGINE_AUTOCOP_ENABLED=true.")

    if price_pence > settings.autocop_max_spend_pence:
        raise AutoCopError(
            f"Price {price_pence}p exceeds max spend {settings.autocop_max_spend_pence}p."
        )

    if dry_run:
        return PurchaseAttemptResult(
            success=True,
            listing_id=listing_id,
            price_pence=price_pence,
            transaction_id=f"dry-run-{listing_id}",
            error=None,
        )

    proxy = proxy_pool.get_proxy()

    # Step 2: fetch available payment methods
    try:
        pm_resp = tls_session.request(
            "GET",
            _PAYMENT_METHODS_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
            captcha_solver=solver,
        )
    except Exception as exc:
        proxy_pool.mark_failed(proxy)
        return PurchaseAttemptResult(
            success=False,
            listing_id=listing_id,
            price_pence=price_pence,
            error=f"Payment methods request failed: {exc}",
        )

    if pm_resp.status_code != 200:
        proxy_pool.mark_failed(proxy)
        return PurchaseAttemptResult(
            success=False,
            listing_id=listing_id,
            price_pence=price_pence,
            error=f"Payment methods endpoint returned {pm_resp.status_code}",
        )

    # Step 3: create payment session
    session_payload = {
        "listing_id": listing_id,
        "price_pence": price_pence,
        "currency": "GBP",
    }

    try:
        session_resp = tls_session.request(
            "POST",
            _SESSIONS_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=session_payload,
            captcha_solver=solver,
        )
    except Exception as exc:
        proxy_pool.mark_failed(proxy)
        return PurchaseAttemptResult(
            success=False,
            listing_id=listing_id,
            price_pence=price_pence,
            error=f"Session creation request failed: {exc}",
        )

    if session_resp.status_code != 201:
        return PurchaseAttemptResult(
            success=False,
            listing_id=listing_id,
            price_pence=price_pence,
            error=f"Session endpoint returned {session_resp.status_code}",
        )

    try:
        session_data: dict[str, Any] = session_resp.json()
    except Exception as exc:
        return PurchaseAttemptResult(
            success=False,
            listing_id=listing_id,
            price_pence=price_pence,
            error=f"Session response parse error: {exc}",
        )

    session_id: str | None = session_data.get("id")
    if not session_id:
        return PurchaseAttemptResult(
            success=False,
            listing_id=listing_id,
            price_pence=price_pence,
            error="No session id in response",
        )

    # Step 4: submit payment (or simulate)
    checkout_payload = {
        "session_id": session_id,
        "listing_id": listing_id,
        "price_pence": price_pence,
    }

    if dry_run:
        # Simulate without charging
        return PurchaseAttemptResult(
            success=True,
            listing_id=listing_id,
            price_pence=price_pence,
            transaction_id=f"dry-run-{listing_id}",
            error=None,
        )

    # Real checkout
    try:
        checkout_resp = tls_session.request(
            "POST",
            _CHECKOUT_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            data=checkout_payload,
            captcha_solver=solver,
        )
    except Exception as exc:
        return PurchaseAttemptResult(
            success=False,
            listing_id=listing_id,
            price_pence=price_pence,
            error=f"Checkout request failed: {exc}",
        )

    if checkout_resp.status_code == 200:
        try:
            checkout_data: dict[str, Any] = checkout_resp.json()
            transaction_id: str = str(checkout_data.get("transaction_id", "")) or None  # type: ignore[assignment]
        except Exception:
            transaction_id = None

        return PurchaseAttemptResult(
            success=True,
            listing_id=listing_id,
            price_pence=price_pence,
            transaction_id=transaction_id or f"tx-{listing_id}",
        )

    return PurchaseAttemptResult(
        success=False,
        listing_id=listing_id,
        price_pence=price_pence,
        error=f"Checkout failed with status {checkout_resp.status_code}: {checkout_resp.text[:500]}",
    )
