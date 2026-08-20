"""Unit tests for ``engine.crosslister``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from engine.crosslister import CrossListerError, DelistResult, delist_item
from engine.proxy import ProxyPool
from engine.tls import TlsSession


class MockTlsSession(TlsSession):
    """Fake TlsSession for testing without curl_cffi."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def request(self, *args: object, **kwargs: object) -> MagicMock:
        return MagicMock()


class TestDelistItem:
    """Tests for delist_item."""

    @pytest.mark.asyncio
    async def test_unknown_venue_raises(self) -> None:
        """An unknown venue raises CrossListerError."""
        with pytest.raises(CrossListerError, match="Unknown venue"):
            await delist_item(
                "inv-1",
                "unknown-venue",
                "ext-1",
                MockTlsSession(),
                ProxyPool(["http://p:8080"]),
            )

    @pytest.mark.asyncio
    async def test_successful_delist(self) -> None:
        """A 200 response returns success."""
        session = MockTlsSession()
        session.request = MagicMock(return_value=MagicMock(status_code=200))

        result = await delist_item(
            "inv-1",
            "vinted",
            "ext-1",
            session,
            ProxyPool(["http://p:8080"]),
        )
        assert isinstance(result, DelistResult)
        assert result.success is True
        assert result.inventory_id == "inv-1"
        assert result.venue == "vinted"
        assert result.external_id == "ext-1"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_failed_delist(self) -> None:
        """A non-200 response returns failure."""
        session = MockTlsSession()
        session.request = MagicMock(return_value=MagicMock(status_code=500))

        result = await delist_item(
            "inv-1",
            "ebay",
            "ext-1",
            session,
            ProxyPool(["http://p:8080"]),
        )
        assert result.success is False
        assert "500" in (result.error or "")

    @pytest.mark.asyncio
    async def test_request_exception_returns_failure(self) -> None:
        """A network exception returns failure."""
        session = MockTlsSession()
        session.request = MagicMock(side_effect=ConnectionError("timeout"))

        result = await delist_item(
            "inv-1",
            "depop",
            "ext-1",
            session,
            ProxyPool(["http://p:8080"]),
        )
        assert result.success is False
        assert "timeout" in (result.error or "")
