"""Unit tests for ``src.arb.web.api_engine``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from engine.config import EngineSettings, get_engine_settings
from src.arb.web.app import app


@pytest.fixture(autouse=True)
def _reset_settings() -> None:
    """Reset engine settings to defaults before each test."""
    settings = get_engine_settings()
    settings.enabled = False
    settings.autocop_enabled = False
    settings.capsolver_api_key = None
    settings.captcha_2captcha_api_key = None


client = TestClient(app)


class TestEngineAPI:
    """Tests for engine API endpoints."""

    def test_status_when_disabled_returns_503(self) -> None:
        """GET /api/v1/engine/status returns 503 when engine is disabled."""
        response = client.get("/api/v1/engine/status")
        assert response.status_code == 503
        assert "disabled" in response.json()["detail"].lower()

    def test_status_when_enabled_returns_200(self) -> None:
        """GET /api/v1/engine/status returns 200 when engine is enabled."""
        settings = get_engine_settings()
        settings.enabled = True
        response = client.get("/api/v1/engine/status")
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True
        assert data["tls_preset"] == "chrome120"

    def test_toggle_enables_engine(self) -> None:
        """POST /api/v1/engine/toggle enables the engine."""
        response = client.post("/api/v1/engine/toggle", json={"enabled": True})
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is True

    def test_toggle_disables_engine(self) -> None:
        """POST /api/v1/engine/toggle disables the engine."""
        settings = get_engine_settings()
        settings.enabled = True
        response = client.post("/api/v1/engine/toggle", json={"enabled": False})
        assert response.status_code == 200
        data = response.json()
        assert data["enabled"] is False

    def test_proxies_when_disabled_returns_503(self) -> None:
        """GET /api/v1/engine/proxies returns 503 when disabled."""
        response = client.get("/api/v1/engine/proxies")
        assert response.status_code == 503

    def test_proxies_when_enabled_returns_200(self) -> None:
        """GET /api/v1/engine/proxies returns 200 when enabled."""
        settings = get_engine_settings()
        settings.enabled = True
        with patch("engine.proxy.ProxyPool.from_env") as mock_from_env:
            mock_pool = MagicMock()
            mock_pool.total_count = 5
            mock_pool.available_count = 3
            mock_from_env.return_value = mock_pool
            response = client.get("/api/v1/engine/proxies")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert data["available"] == 3

    def test_captcha_when_disabled_returns_503(self) -> None:
        """GET /api/v1/engine/captcha returns 503 when disabled."""
        response = client.get("/api/v1/engine/captcha")
        assert response.status_code == 503

    def test_captcha_when_enabled_returns_200(self) -> None:
        """GET /api/v1/engine/captcha returns 200 when enabled."""
        settings = get_engine_settings()
        settings.enabled = True
        response = client.get("/api/v1/engine/captcha")
        assert response.status_code == 200
        data = response.json()
        assert data["capsolver_configured"] is False
        assert data["twocaptcha_configured"] is False

    def test_autocop_when_disabled_returns_503(self) -> None:
        """GET /api/v1/engine/autocop returns 503 when disabled."""
        response = client.get("/api/v1/engine/autocop")
        assert response.status_code == 503

    def test_autocop_when_enabled_returns_200(self) -> None:
        """GET /api/v1/engine/autocop returns 200 when enabled."""
        settings = get_engine_settings()
        settings.enabled = True
        settings.autocop_enabled = True
        response = client.get("/api/v1/engine/autocop")
        assert response.status_code == 200
        data = response.json()
        assert data["armed"] is True
        assert data["max_spend_pence"] == 5000

    def test_crosslister_when_disabled_returns_503(self) -> None:
        """GET /api/v1/engine/crosslister returns 503 when disabled."""
        response = client.get("/api/v1/engine/crosslister")
        assert response.status_code == 503

    def test_crosslister_when_enabled_returns_200(self) -> None:
        """GET /api/v1/engine/crosslister returns 200 when enabled."""
        settings = get_engine_settings()
        settings.enabled = True
        response = client.get("/api/v1/engine/crosslister")
        assert response.status_code == 200
        data = response.json()
        assert "vinted" in data["venues"]
        assert data["active_delist_queue"] == 0
