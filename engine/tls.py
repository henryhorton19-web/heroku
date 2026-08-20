"""TLS/JA3 impersonation client wrapping ``curl_cffi.requests.Session``.

Browser impersonation presets are provided for Chrome 120 and Firefox 122.
All requests go through a single ``TlsSession`` instance that manages
connection reuse and error handling.
"""

from __future__ import annotations

from typing import Any

from curl_cffi import requests as curl_requests
from curl_cffi.requests import Response

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ImpersonationPreset",
    "TlsSession",
]

# ---------------------------------------------------------------------------
# Impersonation presets
# ---------------------------------------------------------------------------

ImpersonationPreset = str
"""One of ``"chrome120"``, ``"firefox122"``."""

_CHROME120 = "chrome120"
_FIREFOX122 = "firefox122"

_VALID_PRESETS: frozenset[str] = frozenset({_CHROME120, _FIREFOX122})


def _validate_preset(preset: str) -> str:
    if preset not in _VALID_PRESETS:
        msg = f"Unknown impersonation preset: {preset!r}. Valid: {sorted(_VALID_PRESETS)}"
        raise ValueError(msg)
    return preset


# ---------------------------------------------------------------------------
# TlsSession
# ---------------------------------------------------------------------------


class TlsSession:
    """A reusable TLS session that impersonates a browser fingerprint.

    Usage::

        session = TlsSession(preset="chrome120")
        resp = session.request("GET", "https://api.example.com/items")
        print(resp.status_code, resp.text[:200])
    """

    def __init__(self, preset: ImpersonationPreset = "chrome120") -> None:
        _validate_preset(preset)
        self._preset = preset
        self._session: curl_requests.Session[Any] = curl_requests.Session()

        self._session.impersonate = preset

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: Any = None,
        params: dict[str, str] | None = None,
    ) -> Response:
        """Execute an HTTP request with the impersonated fingerprint."""
        resp: Response = self._session.request(
            method=method.upper(),  # type: ignore[arg-type]
            url=url,
            headers=headers or {},
            data=data,
            params=params or {},
        )
        return resp

    @property
    def preset(self) -> str:
        """The impersonation preset in use."""
        return self._preset

    def close(self) -> None:
        """Close the underlying session and free resources."""
        self._session.close()
