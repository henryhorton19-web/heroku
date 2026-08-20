"""Unit tests for ``engine.autocop``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from engine.autocop import AutoCopError, PurchaseAttemptResult, attempt_checkout
from engine.config import get_engine_settings
from engine.proxy import ProxyPool
from engine.tls import TlsSession


class MockTlsSession(TlsSession):
    """Fake TlsSession for testing without curl_cffi."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        # skip curl_cffi init
        pass

    def request(self, *args: object, **kwargs: object) -> MagicMock:
        return MagicMock()


class TestAttemptCheckout:
    """Tests for attempt_checkout."""

    @pytest.mark.asyncio
    async def test_enabled_false_raises(self) -> None:
        """When autocop_enabled is False, attempt_checkout raises."""
        settings = get_engine_settings()
        # Force enabled = False
        orig_enabled = settings.autocop_enabled
        settings.autocop_enabled = False
        try:
            with pytest.raises(AutoCopError, match="not enabled"):
                await attempt_checkout(
                    "1", "test", 1000, MockTlsSession(), ProxyPool(["http://p:8080"])
                )
        finally:
            settings.autocop_enabled = orig_enabled

    @pytest.mark.asyncio
    async def test_price_exceeds_cap_raises(self) -> None:
        """When price exceeds max spend, raises."""
        settings = get_engine_settings()
        orig_enabled = settings.autocop_enabled
        orig_spend = settings.autocop_max_spend_pence
        settings.autocop_enabled = True
        settings.autocop_max_spend_pence = 1000
        try:
            with pytest.raises(AutoCopError, match="exceeds max spend"):
                await attempt_checkout(
                    "1", "test", 2000, MockTlsSession(), ProxyPool(["http://p:8080"])
                )
        finally:
            settings.autocop_enabled = orig_enabled
            settings.autocop_max_spend_pence = orig_spend

    @pytest.mark.asyncio
    async def test_dry_run_success(self) -> None:
        """dry_run returns success without real requests."""
        settings = get_engine_settings()
        orig_enabled = settings.autocop_enabled
        orig_spend = settings.autocop_max_spend_pence
        settings.autocop_enabled = True
        settings.autocop_max_spend_pence = 5000
        try:
            result = await attempt_checkout(
                "42",
                "test-item",
                3000,
                MockTlsSession(),
                ProxyPool(["http://p:8080"]),
                dry_run=True,
            )
            assert isinstance(result, PurchaseAttemptResult)
            assert result.success is True
            assert result.listing_id == "42"
            assert result.price_pence == 3000
            assert result.transaction_id is not None
            assert "dry-run" in result.transaction_id
        finally:
            settings.autocop_enabled = orig_enabled
            settings.autocop_max_spend_pence = orig_spend

    @pytest.mark.asyncio
    async def test_payment_methods_failure_returns_error(self) -> None:
        """When the payment methods endpoint fails, returns failed result."""
        settings = get_engine_settings()
        orig_enabled = settings.autocop_enabled
        orig_spend = settings.autocop_max_spend_pence
        settings.autocop_enabled = True
        settings.autocop_max_spend_pence = 5000

        session = MockTlsSession()
        # Make the first request raise
        session.request = MagicMock(side_effect=ConnectionError("fail"))

        try:
            result = await attempt_checkout(
                "42",
                "test-item",
                3000,
                session,
                ProxyPool(["http://p:8080"]),
                dry_run=False,
            )
            assert result.success is False
            assert "Payment methods" in (result.error or "")
        finally:
            settings.autocop_enabled = orig_enabled
            settings.autocop_max_spend_pence = orig_spend
