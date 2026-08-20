"""Unit tests for ``engine.adspower``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from engine.adspower import AdsPowerClient, AdsPowerError, BrowserProfile


class TestAdsPowerClient:
    """Tests for AdsPowerClient."""

    @pytest.mark.asyncio
    async def test_create_profile_success(self) -> None:
        """create_profile returns a BrowserProfile on success."""
        client = AdsPowerClient.__new__(AdsPowerClient)
        client._base_url = "http://localhost:50325"
        client._group_id = None

        async def fake_post(url, json, **kwargs):  # type: ignore[no-untyped-def]
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "code": 0,
                    "msg": "",
                    "data": {"id": "profile-1", "status": "Active"},
                },
            )

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = fake_post  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            profile = await client.create_profile("test-profile", proxy_url="http://user:pass@host:8080")
            assert isinstance(profile, BrowserProfile)
            assert profile.profile_id == "profile-1"
            assert profile.name == "test-profile"
            assert profile.proxy == "http://user:pass@host:8080"
            assert profile.status == "Active"

    @pytest.mark.asyncio
    async def test_create_profile_failure(self) -> None:
        """create_profile raises AdsPowerError on API error."""
        client = AdsPowerClient.__new__(AdsPowerClient)
        client._base_url = "http://localhost:50325"
        client._group_id = None

        async def fake_post(url, json, **kwargs):  # type: ignore[no-untyped-def]
            return MagicMock(
                status_code=200,
                json=lambda: {"code": 1, "msg": "Invalid group"},
            )

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = fake_post  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdsPowerError, match="Invalid group"):
                await client.create_profile("test")

    @pytest.mark.asyncio
    async def test_start_browser_success(self) -> None:
        """start_browser returns a WebSocket URL."""
        client = AdsPowerClient.__new__(AdsPowerClient)
        client._base_url = "http://localhost:50325"

        async def fake_get(url, params, **kwargs):  # type: ignore[no-untyped-def]
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "code": 0,
                    "data": {"ws": {"url": "ws://127.0.0.1:12345"}},
                },
            )

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = fake_get  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            ws_url = await client.start_browser("profile-1")
            assert ws_url == "ws://127.0.0.1:12345"

    @pytest.mark.asyncio
    async def test_start_browser_failure(self) -> None:
        """start_browser raises AdsPowerError on API error."""
        client = AdsPowerClient.__new__(AdsPowerClient)
        client._base_url = "http://localhost:50325"

        async def fake_get(url, params, **kwargs):  # type: ignore[no-untyped-def]
            return MagicMock(
                status_code=200,
                json=lambda: {"code": 1, "msg": "Profile not found"},
            )

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = fake_get  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdsPowerError, match="Profile not found"):
                await client.start_browser("nonexistent")

    @pytest.mark.asyncio
    async def test_stop_browser_success(self) -> None:
        """stop_browser returns True on success."""
        client = AdsPowerClient.__new__(AdsPowerClient)
        client._base_url = "http://localhost:50325"

        async def fake_get(url, params, **kwargs):  # type: ignore[no-untyped-def]
            return MagicMock(
                status_code=200,
                json=lambda: {"code": 0},
            )

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = fake_get  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            result = await client.stop_browser("profile-1")
            assert result is True

    @pytest.mark.asyncio
    async def test_stop_browser_failure(self) -> None:
        """stop_browser raises AdsPowerError on API error."""
        client = AdsPowerClient.__new__(AdsPowerClient)
        client._base_url = "http://localhost:50325"

        async def fake_get(url, params, **kwargs):  # type: ignore[no-untyped-def]
            return MagicMock(
                status_code=200,
                json=lambda: {"code": 1, "msg": "Already stopped"},
            )

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = fake_get  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            with pytest.raises(AdsPowerError, match="Already stopped"):
                await client.stop_browser("profile-1")
