"""Unit tests for ``engine.proxy``."""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from engine.proxy import ProxyPool, ProxyPoolError


class TestProxyPool:
    """Tests for the ProxyPool class."""

    def test_init_with_empty_list_raises(self) -> None:
        """An empty proxy list should raise ProxyPoolError."""
        with pytest.raises(ProxyPoolError, match="at least one proxy"):
            ProxyPool([])

    def test_get_proxy_round_robin(self) -> None:
        """Proxies should be returned in round-robin order."""
        pool = ProxyPool(["http://proxy1:8080", "http://proxy2:8080"])
        assert pool.get_proxy() == "http://proxy1:8080"
        assert pool.get_proxy() == "http://proxy2:8080"
        assert pool.get_proxy() == "http://proxy1:8080"  # wraps around

    def test_mark_failed_quarantines(self) -> None:
        """A failed proxy should be quarantined and skipped."""
        pool = ProxyPool(
            ["http://proxy1:8080", "http://proxy2:8080"],
            quarantine_seconds=60,
        )
        pool.mark_failed("http://proxy1:8080")
        # Only proxy2 should be available
        assert pool.get_proxy() == "http://proxy2:8080"
        assert pool.get_proxy() == "http://proxy2:8080"

    def test_all_quarantined_raises(self) -> None:
        """If all proxies are quarantined, get_proxy should raise."""
        pool = ProxyPool(
            ["http://proxy1:8080"],
            quarantine_seconds=60,
        )
        pool.mark_failed("http://proxy1:8080")
        with pytest.raises(ProxyPoolError, match="All proxies are currently quarantined"):
            pool.get_proxy()

    def test_quarantine_expires(self) -> None:
        """After the quarantine period, the proxy should become available again."""
        pool = ProxyPool(
            ["http://proxy1:8080"],
            quarantine_seconds=1,
        )
        pool.mark_failed("http://proxy1:8080")
        time.sleep(1.05)

        # Should now be available
        assert pool.get_proxy() == "http://proxy1:8080"

    def test_available_count(self) -> None:
        """available_count should reflect quarantined proxies."""
        pool = ProxyPool(
            ["http://proxy1:8080", "http://proxy2:8080"],
            quarantine_seconds=60,
        )
        assert pool.available_count == 2
        pool.mark_failed("http://proxy1:8080")
        assert pool.available_count == 1

    def test_total_count(self) -> None:
        """total_count should return the full pool size."""
        pool = ProxyPool(["http://proxy1:8080", "http://proxy2:8080"])
        assert pool.total_count == 2

    def test_from_env_missing_file(self) -> None:
        """If the proxy file does not exist, from_env should raise."""
        with patch.dict(os.environ, {"ENGINE_PROXY_POOL_PATH": "/nonexistent/proxies.txt"}):
            with pytest.raises(ProxyPoolError, match="Proxy file not found"):
                ProxyPool.from_env()

    def test_from_env_with_file(self, tmp_path: Path) -> None:
        """from_env should load proxies from the specified file."""
        proxy_file = tmp_path / "proxies.txt"
        proxy_file.write_text("http://proxy1:8080\n# comment\nhttp://proxy2:8080\n")
        with patch.dict(os.environ, {"ENGINE_PROXY_POOL_PATH": str(proxy_file)}):
            pool = ProxyPool.from_env()
        assert pool.total_count == 2
        assert pool.get_proxy() == "http://proxy1:8080"
        assert pool.get_proxy() == "http://proxy2:8080"
