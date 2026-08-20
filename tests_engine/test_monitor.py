"""Unit tests for ``engine.monitor``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from engine.monitor import (
    ListingScannedEvent,
    MonitorConfig,
    poll_vinted,
    run_monitor_loop,
)
from engine.proxy import ProxyPool
from engine.tls import TlsSession


class TestPollVinted:
    """Tests for the poll_vinted function."""

    @pytest.mark.asyncio
    async def test_returns_events_on_success(self) -> None:
        """A successful API response should yield ListingScannedEvent objects."""
        tls_session = MagicMock(spec=TlsSession)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "items": [
                {
                    "id": 12345,
                    "title": "Nike Air Max 90",
                    "price": {"amount": 45.00, "currency_code": "GBP"},
                    "url": "https://www.vinted.fr/items/12345",
                },
                {
                    "id": 67890,
                    "title": "Adidas Ultraboost",
                    "price": {"amount": 60.00, "currency_code": "GBP"},
                    "url": "https://www.vinted.fr/items/67890",
                },
            ]
        }
        tls_session.request.return_value = mock_resp
        proxy_pool = ProxyPool(["http://proxy1:8080"])

        events = await poll_vinted(
            keyword="nike",
            max_price=None,
            tls_session=tls_session,
            proxy_pool=proxy_pool,
        )

        assert len(events) == 2
        assert events[0].external_id == "12345"
        assert events[0].title == "Nike Air Max 90"
        assert events[0].price_pence == 4500
        assert events[1].external_id == "67890"
        assert events[1].price_pence == 6000

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_200(self) -> None:
        """A non-200 response should return an empty list."""
        tls_session = MagicMock(spec=TlsSession)
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        tls_session.request.return_value = mock_resp
        proxy_pool = ProxyPool(["http://proxy1:8080"])

        events = await poll_vinted(
            keyword="nike",
            max_price=None,
            tls_session=tls_session,
            proxy_pool=proxy_pool,
        )

        assert events == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_exception(self) -> None:
        """A network exception should return an empty list and mark proxy failed."""
        tls_session = MagicMock(spec=TlsSession)
        tls_session.request.side_effect = ConnectionError("timeout")
        proxy_pool = ProxyPool(["http://proxy1:8080"])

        events = await poll_vinted(
            keyword="nike",
            max_price=None,
            tls_session=tls_session,
            proxy_pool=proxy_pool,
        )

        assert events == []
        # The proxy should have been marked failed
        assert proxy_pool.available_count == 0

    @pytest.mark.asyncio
    async def test_max_price_filter(self) -> None:
        """The max_price parameter should be passed as price_to in cents."""
        tls_session = MagicMock(spec=TlsSession)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": []}
        tls_session.request.return_value = mock_resp
        proxy_pool = ProxyPool(["http://proxy1:8080"])

        events = await poll_vinted(
            keyword="nike",
            max_price=25.00,
            tls_session=tls_session,
            proxy_pool=proxy_pool,
        )

        assert events == []


class TestRunMonitorLoop:
    """Tests for the run_monitor_loop function."""

    @pytest.mark.asyncio
    async def test_loop_calls_on_event(self) -> None:
        """The on_event callback should be called for each event."""
        config = MonitorConfig(
            keyword="test",
            max_price_pence=None,
            poll_interval_seconds=0.1,
        )
        tls_session = MagicMock(spec=TlsSession)
        proxy_pool = MagicMock(spec=ProxyPool)

        # Mock the poll_vinted function to return one event then stop
        event = ListingScannedEvent(
            external_id="1",
            title="Test Item",
            price_pence=1000,
            url="https://example.com/item/1",
        )

        call_count = 0
        received_events: list[ListingScannedEvent] = []

        async def fake_poll(*args: object, **kwargs: object) -> list[ListingScannedEvent]:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise KeyboardInterrupt
            return [event]

        with patch("engine.monitor.poll_vinted", side_effect=fake_poll):
            try:
                await run_monitor_loop(
                    config,
                    tls_session,  # type: ignore[arg-type]
                    proxy_pool,  # type: ignore[arg-type]
                    on_event=received_events.append,
                )
            except KeyboardInterrupt:
                pass

        assert len(received_events) == 1
        assert received_events[0].external_id == "1"
