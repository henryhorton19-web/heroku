"""TLS/JA3 impersonation client wrapping ``curl_cffi.requests.Session``.

Browser impersonation presets are provided for Chrome 120 and Firefox 122.
All requests go through a single ``TlsSession`` instance that manages
connection reuse and error handling.
"""

from __future__ import annotations

import re
from typing import Any

from curl_cffi import requests as curl_requests
from curl_cffi.requests import Response

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ImpersonationPreset",
    "TlsSession",
    "detect_captcha",
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
# CAPTCHA detection helper
# ---------------------------------------------------------------------------


def detect_captcha(response: Response) -> tuple[str, str] | None:
    """Detect whether *response* contains a CAPTCHA challenge.

    Returns ``(captcha_type, site_key)`` where *captcha_type* is one of
    ``"turnstile"``, ``"hcaptcha"``, ``"recaptcha"``, or ``None`` if none
    is detected.

    The detection is heuristics-based and may need tuning for changes in
    the challenge page structure.
    """
    text = response.text
    if text is None:
        return None

    # Turnstile
    m = re.search(
        r'<div[^>]*class="[^"]*cf-turnstile[^"]*"[^>]*data-sitekey="([^"]+)"',
        text,
    )
    if m:
        return ("turnstile", m.group(1))

    # hCaptcha
    m = re.search(
        r'<div[^>]*class="[^"]*h-captcha[^"]*"[^>]*data-sitekey="([^"]+)"',
        text,
    )
    if m:
        return ("hcaptcha", m.group(1))

    # reCAPTCHA
    m = re.search(
        r'<div[^>]*class="[^"]*g-recaptcha[^"]*"[^>]*data-sitekey="([^"]+)"',
        text,
    )
    if m:
        return ("recaptcha", m.group(1))

    return None


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
        self._session: curl_requests.Session = curl_requests.Session(impersonate=preset)

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
        captcha_solver: Any = None,
    ) -> Response:
        """Execute an HTTP request with the impersonated fingerprint.

        If ``captcha_solver`` is provided and the response contains a CAPTCHA
        challenge, the solver will be used to obtain a token, which is then
        injected into the request headers and the request retried (up to one
        retry).  The retried response is returned.

        The ``captcha_solver`` parameter is typed as ``Any`` to avoid a direct
        import of the ``CaptchaSolver`` class from ``engine.captcha`` (circular
        dependency is not an issue here, but the type annotation is kept
        optional for API cleanliness).
        """
        resp: Response = self._session.request(
            method=method.upper(),
            url=url,
            headers=headers or {},
            data=data,
            params=params or {},
        )

        if captcha_solver is not None and resp.status_code in (403, 429):
            detected = detect_captcha(resp)
            if detected is not None:
                captcha_type, site_key = detected
                # Solve – note: captcha_solver methods are async, but this
                # method is sync.  We use asyncio.run() to bridge.
                import asyncio  # noqa: PLC0415 – import inside method to avoid top-level import

                try:
                    if captcha_type == "turnstile":
                        result = asyncio.run(captcha_solver.solve_turnstile(site_key, url))
                    elif captcha_type == "hcaptcha":
                        result = asyncio.run(captcha_solver.solve_hcaptcha(site_key, url))
                    elif captcha_type == "recaptcha":
                        result = asyncio.run(captcha_solver.solve_recaptcha(site_key, url))
                    else:
                        return resp
                except Exception:
                    return resp  # fall through
                # Retry with token
                retry_headers = dict(headers or {})
                retry_headers["X-Captcha-Token"] = result.token
                resp = self._session.request(
                    method=method.upper(),
                    url=url,
                    headers=retry_headers,
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
