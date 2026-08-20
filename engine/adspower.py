"""AdsPower Local REST API client for browser profile management.

Provides async methods to create, start, and stop browser profiles via AdsPower's
local API (default http://local.adspower.net:50325).  All methods raise
``AdsPowerError`` on failure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from engine.config import EngineSettings, get_engine_settings

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "AdsPowerClient",
    "AdsPowerError",
    "BrowserProfile",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class AdsPowerError(Exception):
    """Raised when an AdsPower API call fails."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BrowserProfile:
    """A browser profile managed by AdsPower.

    Attributes
    ----------
    profile_id:
        The AdsPower profile identifier.
    name:
        Human-readable profile name.
    proxy:
        Proxy URL bound to the profile, or ``None``.
    status:
        Current status string (e.g. ``"Active"``, ``"Inactive"``).
    """

    profile_id: str
    name: str
    proxy: str | None
    status: str


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AdsPowerClient:
    """Async client for AdsPower's Local REST API.

    Usage::

        client = AdsPowerClient()
        profile = await client.create_profile("my-profile", proxy_url="http://p:8080")
        ws_endpoint = await client.start_browser(profile.profile_id)
        await client.stop_browser(profile.profile_id)
    """

    def __init__(self, settings: EngineSettings | None = None) -> None:
        if settings is None:
            settings = get_engine_settings()
        self._base_url: str = settings.adspower_api_url or "http://local.adspower.net:50325"
        self._group_id: str | None = settings.adspower_group_id

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def create_profile(
        self,
        name: str,
        proxy_url: str | None = None,
    ) -> BrowserProfile:
        """Create a new browser profile.

        Parameters
        ----------
        name:
            Human-readable profile name.
        proxy_url:
            Optional proxy URL (e.g. ``"http://user:pass@host:port"``).

        Returns
        -------
        ``BrowserProfile`` with the created profile details.

        Raises
        ------
        AdsPowerError
            If the API call fails or returns an error code.
        """
        payload: dict[str, Any] = {
            "name": name,
            "group_id": self._group_id or "",
            "domain_name": "",
            "user_proxy_config": {},
        }
        if proxy_url:
            payload["user_proxy_config"] = self._parse_proxy(proxy_url)

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._base_url}/api/v1/user/create",
                json=payload,
            )
            data = resp.json()
            if data.get("code") != 0:
                msg = f"AdsPower create_profile error: {data.get('msg', 'unknown')}"
                raise AdsPowerError(msg)

            profile_data = data.get("data", {})
            return BrowserProfile(
                profile_id=str(profile_data.get("id", "")),
                name=name,
                proxy=proxy_url,
                status=profile_data.get("status", "Active"),
            )

    async def start_browser(self, profile_id: str) -> str:
        """Start a browser profile and return the WebSocket debugging URL.

        Parameters
        ----------
        profile_id:
            The AdsPower profile identifier.

        Returns
        -------
        The WebSocket / CDP debugging endpoint URL (e.g.
        ``"ws://127.0.0.1:xxxxx"``).

        Raises
        ------
        AdsPowerError
            If the API call fails.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/browser/start",
                params={"user_id": profile_id},
            )
            data = resp.json()
            if data.get("code") != 0:
                msg = f"AdsPower start_browser error: {data.get('msg', 'unknown')}"
                raise AdsPowerError(msg)

            ws_url: str = data.get("data", {}).get("ws", {}).get("url", "")
            if not ws_url:
                raise AdsPowerError("start_browser returned no WebSocket URL")
            return ws_url

    async def stop_browser(self, profile_id: str) -> bool:
        """Stop a browser profile.

        Parameters
        ----------
        profile_id:
            The AdsPower profile identifier.

        Returns
        -------
        ``True`` if the profile was stopped successfully.

        Raises
        ------
        AdsPowerError
            If the API call fails.
        """
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/browser/stop",
                params={"user_id": profile_id},
            )
            data = resp.json()
            if data.get("code") != 0:
                msg = f"AdsPower stop_browser error: {data.get('msg', 'unknown')}"
                raise AdsPowerError(msg)
            return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_proxy(proxy_url: str) -> dict[str, str]:
        """Parse a proxy URL into AdsPower's ``user_proxy_config`` format.

        Supports ``http://user:pass@host:port`` and ``socks5://...``.
        """
        # Simple parsing: assume format scheme://user:pass@host:port
        # For production use a proper URL parser.
        parts = proxy_url.split("://", 1)
        if len(parts) != 2:
            return {}
        scheme = parts[0]
        rest = parts[1]
        if "@" in rest:
            user_pass, host_port = rest.split("@", 1)
            user, password = user_pass.split(":", 1) if ":" in user_pass else (user_pass, "")
        else:
            user = ""
            password = ""
            host_port = rest
        host, port = host_port.split(":", 1) if ":" in host_port else (host_port, "80")
        return {
            "proxy_type": scheme,
            "proxy_host": host,
            "proxy_port": port,
            "proxy_user": user,
            "proxy_password": password,
        }
