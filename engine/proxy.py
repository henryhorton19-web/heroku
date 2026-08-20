"""Proxy pool manager with round-robin rotation and failure quarantine.

Proxies are loaded from a file (one URL per line) or from the environment
variable ``ENGINE_PROXY_POOL_PATH``.  Failed proxies are temporarily
quarantined and can be re-enabled after a configurable cooldown.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from pathlib import Path

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ProxyPool",
    "ProxyPoolError",
]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ProxyPoolError(Exception):
    """Raised when the proxy pool is empty or cannot be loaded."""


# ---------------------------------------------------------------------------
# ProxyPool
# ---------------------------------------------------------------------------


class ProxyPool:
    """A round-robin proxy pool with failure quarantine.

    Usage::

        pool = ProxyPool.from_env()
        proxy = pool.get_proxy()
        # ... use proxy ...
        pool.mark_failed(proxy)   # if the request failed
    """

    def __init__(self, proxies: Sequence[str], *, quarantine_seconds: int = 60) -> None:
        if not proxies:
            raise ProxyPoolError("Proxy pool must contain at least one proxy URL.")
        self._proxies: list[str] = list(proxies)
        self._index: int = 0
        self._quarantine_seconds = quarantine_seconds
        self._quarantined: dict[str, float] = {}  # proxy -> expiry timestamp

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> ProxyPool:
        """Load proxies from ``ENGINE_PROXY_POOL_PATH`` env var or a default file.

        The file must contain one proxy URL per line.  Lines starting with
        ``#`` are ignored.
        """
        path_str = os.environ.get("ENGINE_PROXY_POOL_PATH")
        if path_str:
            path = Path(path_str)
        else:
            path = Path("proxies.txt")

        if not path.is_file():
            raise ProxyPoolError(
                f"Proxy file not found: {path}. "
                "Set ENGINE_PROXY_POOL_PATH or create a proxies.txt file."
            )

        proxies: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                proxies.append(stripped)

        return cls(proxies)

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def get_proxy(self) -> str:
        """Return the next available proxy URL (round-robin).

        Skips proxies that are currently quarantined.  If all proxies are
        quarantined, raises ``ProxyPoolError``.
        """
        if not self._proxies:
            raise ProxyPoolError("Proxy pool is empty.")

        now = time.monotonic()
        # Clean expired quarantines
        expired = [p for p, t in self._quarantined.items() if t <= now]
        for p in expired:
            del self._quarantined[p]

        start = self._index
        for _ in range(len(self._proxies)):
            candidate = self._proxies[self._index]
            self._index = (self._index + 1) % len(self._proxies)
            if candidate not in self._quarantined:
                return candidate
            # If we wrapped around, break to avoid infinite loop
            if self._index == start:
                break

        raise ProxyPoolError("All proxies are currently quarantined.")

    def mark_failed(self, proxy: str) -> None:
        """Quarantine *proxy* for ``quarantine_seconds``."""
        if proxy in self._proxies:
            self._quarantined[proxy] = time.monotonic() + self._quarantine_seconds

    @property
    def available_count(self) -> int:
        """Number of proxies not currently quarantined."""
        now = time.monotonic()
        return sum(
            1 for p in self._proxies if p not in self._quarantined or self._quarantined[p] <= now
        )

    @property
    def total_count(self) -> int:
        """Total number of proxies in the pool."""
        return len(self._proxies)
