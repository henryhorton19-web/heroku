"""CAPTCHA solving pipeline using CapSolver or 2Captcha.

The ``CaptchaSolver`` initializes API keys from ``EngineSettings`` (via
environment variables ``ENGINE_CAPSOLVER_API_KEY`` and
``ENGINE_2CAPTCHA_API_KEY``).  It provides three async methods:

* ``solve_turnstile``
* ``solve_hcaptcha``
* ``solve_recaptcha``

Each method tries the first configured provider (CapSolver), then falls back
to 2Captcha.  A ``CaptchaError`` is raised if neither key is set or if all
providers fail.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import httpx

from engine.config import EngineSettings, get_engine_settings

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "CaptchaError",
    "CaptchaResult",
    "CaptchaSolver",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CaptchaError(Exception):
    """Raised when CAPTCHA solving fails or no providers are configured."""


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CaptchaResult:
    """Result of a CAPTCHA solve attempt.

    Attributes
    ----------
    token:
        The solution token (text).
    provider:
        Which provider solved it (``"capsolver"`` or ``"2captcha"``).
    """

    token: str
    provider: str


# ---------------------------------------------------------------------------
# CaptchaSolver
# ---------------------------------------------------------------------------


class CaptchaSolver:
    """CAPTCHA solver that tries CapSolver first, then 2Captcha.

    Usage::

        solver = CaptchaSolver()
        result = await solver.solve_turnstile(
            site_key="0x4AAAAAAA...",
            page_url="https://www.vinted.fr/items/12345",
        )
        print(result.token)
    """

    def __init__(self, settings: EngineSettings | None = None) -> None:
        if settings is None:
            settings = get_engine_settings()
        self._capsolver_key: str | None = settings.capsolver_api_key
        self._twocaptcha_key: str | None = settings.captcha_2captcha_api_key

    # ------------------------------------------------------------------
    # Public solve methods
    # ------------------------------------------------------------------

    async def solve_turnstile(self, site_key: str, page_url: str) -> CaptchaResult:
        """Solve a Cloudflare Turnstile challenge.

        Parameters
        ----------
        site_key:
            The sitekey embedded in the page.
        page_url:
            The URL where the CAPTCHA appears.

        Returns
        -------
        ``CaptchaResult`` with the solution token.

        Raises
        ------
        CaptchaError
            If no provider is configured or all attempts fail.
        """
        return await self._solve(
            task_type_capsolver="AntiTurnstileTaskProxyLess",
            task_type_2captcha="TurnstileTaskProxyless",
            site_key=site_key,
            page_url=page_url,
        )

    async def solve_hcaptcha(self, site_key: str, page_url: str) -> CaptchaResult:
        """Solve an hCaptcha challenge."""
        return await self._solve(
            task_type_capsolver="HCaptchaTaskProxyless",
            task_type_2captcha="HCaptchaTaskProxyless",
            site_key=site_key,
            page_url=page_url,
        )

    async def solve_recaptcha(self, site_key: str, page_url: str) -> CaptchaResult:
        """Solve a reCAPTCHA v2/v3 challenge.

        .. note::
            For reCAPTCHA v3 an additional ``action`` parameter and
            ``min_score`` may be required; this method uses defaults
            that work for most scenarios.
        """
        # Try capSolver first
        if self._capsolver_key:
            try:
                token = await self._solve_capsolver(
                    task_type="ReCaptchaV2TaskProxyless",
                    site_key=site_key,
                    page_url=page_url,
                    task_payload={},
                )
                return CaptchaResult(token=token, provider="capsolver")
            except CaptchaError:
                pass

        if self._twocaptcha_key:
            try:
                token = await self._solve_2captcha(
                    method="userrecaptcha",
                    site_key=site_key,
                    page_url=page_url,
                )
                return CaptchaResult(token=token, provider="2captcha")
            except CaptchaError:
                pass

        raise CaptchaError(
            "No CAPTCHA provider configured. Set ENGINE_CAPSOLVER_API_KEY or "
            "ENGINE_2CAPTCHA_API_KEY."
        )

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    async def _solve(
        self,
        *,
        task_type_capsolver: str,
        task_type_2captcha: str,
        site_key: str,
        page_url: str,
    ) -> CaptchaResult:
        if self._capsolver_key:
            try:
                token = await self._solve_capsolver(
                    task_type=task_type_capsolver,
                    site_key=site_key,
                    page_url=page_url,
                    task_payload={},
                )
                return CaptchaResult(token=token, provider="capsolver")
            except CaptchaError:
                pass

        if self._twocaptcha_key:
            try:
                token = await self._solve_2captcha(
                    method=task_type_2captcha,
                    site_key=site_key,
                    page_url=page_url,
                )
                return CaptchaResult(token=token, provider="2captcha")
            except CaptchaError:
                pass

        raise CaptchaError(
            "No CAPTCHA provider configured. Set ENGINE_CAPSOLVER_API_KEY or "
            "ENGINE_2CAPTCHA_API_KEY."
        )

    # ------------------------------------------------------------------
    # Provider-specific solvers
    # ------------------------------------------------------------------

    async def _solve_capsolver(
        self,
        task_type: str,
        site_key: str,
        page_url: str,
        task_payload: dict[str, Any],
    ) -> str:
        assert self._capsolver_key is not None  # guarded by caller
        async with httpx.AsyncClient(timeout=60) as client:
            # Step 1: create task
            create_resp = await client.post(
                "https://api.capsolver.com/createTask",
                json={
                    "clientKey": self._capsolver_key,
                    "task": {
                        "type": task_type,
                        "websiteURL": page_url,
                        "websiteKey": site_key,
                        **task_payload,
                    },
                },
            )
            create_data = create_resp.json()
            if create_data.get("errorId") not in (0, None):
                msg = f"CapSolver createTask error: {create_data.get('errorDescription', 'unknown')}"
                raise CaptchaError(msg)

            task_id = create_data["taskId"]

            # Step 2: poll for result
            for _ in range(30):
                await asyncio.sleep(2)
                poll_resp = await client.post(
                    "https://api.capsolver.com/getTaskResult",
                    json={"clientKey": self._capsolver_key, "taskId": task_id},
                )
                poll_data = poll_resp.json()
                if poll_data.get("status") == "ready":
                    return poll_data["solution"]["token"]
                if poll_data.get("status") == "failed":
                    msg = f"CapSolver task failed: {poll_data.get('errorDescription', 'unknown')}"
                    raise CaptchaError(msg)

            raise CaptchaError("CapSolver task timed out after 60 seconds")

    async def _solve_2captcha(
        self,
        method: str,
        site_key: str,
        page_url: str,
    ) -> str:
        assert self._twocaptcha_key is not None  # guarded by caller
        base = "https://2captcha.com"
        async with httpx.AsyncClient(timeout=60) as client:
            # Step 1: in
            in_resp = await client.get(
                f"{base}/in.php",
                params={
                    "key": self._twocaptcha_key,
                    "method": method,
                    "googlekey": site_key,
                    "pageurl": page_url,
                },
            )
            in_text = in_resp.text.strip()
            if not in_text.startswith("OK|"):
                msg = f"2Captcha in.php error: {in_text}"
                raise CaptchaError(msg)
            captcha_id = in_text[3:]

            # Step 2: poll for result
            for _ in range(30):
                await asyncio.sleep(5)
                res_resp = await client.get(
                    f"{base}/res.php",
                    params={
                        "key": self._twocaptcha_key,
                        "action": "get",
                        "id": captcha_id,
                    },
                )
                res_text = res_resp.text.strip()
                if res_text == "CAPCHA_NOT_READY":
                    continue
                if res_text.startswith("OK|"):
                    return res_text[3:]
                msg = f"2Captcha res.php error: {res_text}"
                raise CaptchaError(msg)

            raise CaptchaError("2Captcha task timed out after 150 seconds")
