"""Unit tests for ``engine.captcha``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from engine.captcha import CaptchaError, CaptchaResult, CaptchaSolver


class TestCaptchaSolver:
    """Tests for CaptchaSolver."""

    def test_init_no_keys_raises_on_solve(self) -> None:
        """When no keys are set, solve methods raise CaptchaError."""
        solver = CaptchaSolver()
        with pytest.raises(CaptchaError, match="No CAPTCHA provider configured"):
            solver.solve_turnstile("sitekey", "https://example.com")

    @pytest.mark.asyncio
    async def test_solve_capsolver_success(self) -> None:
        """CapSolver path returns a token."""
        solver = CaptchaSolver.__new__(CaptchaSolver)
        solver._capsolver_key = "capsolver-key-1"
        solver._twocaptcha_key = None

        # Mock httpx.AsyncClient
        async def fake_post(url, json, **kwargs):  # type: ignore[no-untyped-def]
            if "createTask" in url:
                return MagicMock(
                    status_code=200,
                    json=lambda: {"errorId": 0, "taskId": "task-1"},
                )
            if "getTaskResult" in url:
                return MagicMock(
                    status_code=200,
                    json=lambda: {"status": "ready", "solution": {"token": "cap-token-123"}},
                )
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = fake_post  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            result = await solver.solve_turnstile("sitekey", "https://example.com")
            assert isinstance(result, CaptchaResult)
            assert result.token == "cap-token-123"
            assert result.provider == "capsolver"

    @pytest.mark.asyncio
    async def test_solve_2captcha_fallback(self) -> None:
        """2Captcha fallback works when CapSolver is missing."""
        solver = CaptchaSolver.__new__(CaptchaSolver)
        solver._capsolver_key = None
        solver._twocaptcha_key = "2captcha-key-1"

        # Use a real AsyncClient but mock its methods
        async def fake_get(url, params, **kwargs):  # type: ignore[no-untyped-def]
            if "in.php" in url:
                mock_resp = MagicMock()
                mock_resp.text = "OK|captcha-id-1"
                return mock_resp
            if "res.php" in url:
                mock_resp = MagicMock()
                mock_resp.text = "OK|2captcha-token-456"
                return mock_resp
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.get = fake_get  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            result = await solver.solve_turnstile("sitekey", "https://example.com")
            assert isinstance(result, CaptchaResult)
            assert result.token == "2captcha-token-456"
            assert result.provider == "2captcha"

    @pytest.mark.asyncio
    async def test_recaptcha_uses_capsolver(self) -> None:
        """solve_recaptcha uses Capsolver first."""
        solver = CaptchaSolver.__new__(CaptchaSolver)
        solver._capsolver_key = "capsolver-key"
        solver._twocaptcha_key = None

        async def fake_post(url, json, **kwargs):  # type: ignore[no-untyped-def]
            if "createTask" in url:
                return MagicMock(
                    status_code=200,
                    json=lambda: {"errorId": 0, "taskId": "task-recaptcha"},
                )
            if "getTaskResult" in url:
                return MagicMock(
                    status_code=200,
                    json=lambda: {"status": "ready", "solution": {"token": "recaptcha-token"}},
                )
            raise AssertionError(f"Unexpected URL: {url}")

        with patch.object(httpx, "AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__.return_value = mock_client
            mock_client.post = fake_post  # type: ignore[assignment]
            mock_client_cls.return_value = mock_client

            result = await solver.solve_recaptcha("sitekey", "https://example.com")
            assert result.token == "recaptcha-token"
            assert result.provider == "capsolver"
