"""Unit tests for engine REST API bridge endpoints."""

from __future__ import annotations

import pytest
from arb.web.app import create_app

from engine.config import get_engine_settings
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    return TestClient(app)


class TestEngineAPI:
    def test_status_when_disabled_returns_503(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = False
        try:
            res = client.get("/api/v1/engine/status")
            assert res.status_code == 503
            assert "disabled" in res.json()["detail"].lower()
        finally:
            settings.enabled = orig

    def test_status_when_enabled_returns_200(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = True
        try:
            res = client.get("/api/v1/engine/status")
            assert res.status_code == 200
            data = res.json()
            assert data["enabled"] is True
            assert "tls_preset" in data

        finally:
            settings.enabled = orig

    def test_toggle_enables_engine(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        try:
            res = client.post("/api/v1/engine/toggle", json={"enabled": True})
            assert res.status_code == 200
            assert res.json()["enabled"] is True
            assert settings.enabled is True
        finally:
            settings.enabled = orig

    def test_toggle_disables_engine(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        try:
            res = client.post("/api/v1/engine/toggle", json={"enabled": False})
            assert res.status_code == 200
            assert res.json()["enabled"] is False
            assert settings.enabled is False
        finally:
            settings.enabled = orig

    def test_proxies_when_disabled_returns_503(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = False
        try:
            res = client.get("/api/v1/engine/proxies")
            assert res.status_code == 503
        finally:
            settings.enabled = orig

    def test_proxies_when_enabled_returns_200(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = True
        try:
            res = client.get("/api/v1/engine/proxies")
            assert res.status_code == 200
            data = res.json()
            assert "total" in data
            assert "available" in data
        finally:
            settings.enabled = orig

    def test_captcha_when_disabled_returns_503(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = False
        try:
            res = client.get("/api/v1/engine/captcha")
            assert res.status_code == 503
        finally:
            settings.enabled = orig

    def test_captcha_when_enabled_returns_200(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = True
        try:
            res = client.get("/api/v1/engine/captcha")
            assert res.status_code == 200
            data = res.json()
            assert "capsolver_configured" in data
        finally:
            settings.enabled = orig

    def test_autocop_when_disabled_returns_503(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = False
        try:
            res = client.get("/api/v1/engine/autocop")
            assert res.status_code == 503
        finally:
            settings.enabled = orig

    def test_autocop_when_enabled_returns_200(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = True
        try:
            res = client.get("/api/v1/engine/autocop")
            assert res.status_code == 200
            data = res.json()
            assert "armed" in data
            assert "max_spend_pence" in data
        finally:
            settings.enabled = orig

    def test_crosslister_when_disabled_returns_503(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = False
        try:
            res = client.get("/api/v1/engine/crosslister")
            assert res.status_code == 503
        finally:
            settings.enabled = orig

    def test_crosslister_when_enabled_returns_200(self, client: TestClient) -> None:
        settings = get_engine_settings()
        orig = settings.enabled
        settings.enabled = True
        try:
            res = client.get("/api/v1/engine/crosslister")
            assert res.status_code == 200
            data = res.json()
            assert "venues" in data
        finally:
            settings.enabled = orig
