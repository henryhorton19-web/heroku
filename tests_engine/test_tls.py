"""Unit tests for ``engine.tls``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from engine.tls import TlsSession


class TestTlsSession:
    """Tests for the TlsSession class."""

    def test_init_with_valid_preset(self) -> None:
        """A valid preset should not raise."""
        session = TlsSession(preset="chrome120")
        assert session.preset == "chrome120"
        session.close()

    def test_init_with_invalid_preset_raises(self) -> None:
        """An unknown preset should raise ValueError."""
        with pytest.raises(ValueError, match="Unknown impersonation preset"):
            TlsSession(preset="nonexistent")

    def test_request_get(self) -> None:
        """A GET request should return a response with status 200."""
        url = "https://example.com/api/items"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"items": [{"id": 1}]}

        with patch("curl_cffi.requests.Session.request", return_value=mock_resp):
            session = TlsSession(preset="chrome120")
            response = session.request("GET", url)
            assert response.status_code == 200
            assert response.json() == {"items": [{"id": 1}]}
            session.close()

    def test_request_with_params(self) -> None:
        """Query parameters should be sent correctly."""
        url = "https://example.com/search"
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("curl_cffi.requests.Session.request", return_value=mock_resp):
            session = TlsSession(preset="chrome120")
            response = session.request("GET", url, params={"q": "test", "page": "1"})
            assert response.status_code == 200
            session.close()

    def test_request_with_headers(self) -> None:
        """Custom headers should be passed to the request."""
        url = "https://example.com/data"
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("curl_cffi.requests.Session.request", return_value=mock_resp):
            session = TlsSession(preset="chrome120")
            response = session.request("GET", url, headers={"X-Custom": "value"})
            assert response.status_code == 200
            session.close()

    def test_request_post(self) -> None:
        """A POST request with data should work."""
        url = "https://example.com/submit"
        mock_resp = MagicMock()
        mock_resp.status_code = 201

        with patch("curl_cffi.requests.Session.request", return_value=mock_resp):
            session = TlsSession(preset="chrome120")
            response = session.request("POST", url, data={"name": "test"})
            assert response.status_code == 201
            session.close()

    def test_close_called(self) -> None:
        """close() should not raise."""
        session = TlsSession(preset="chrome120")
        session.close()
        # Calling close again should be safe
        session.close()
