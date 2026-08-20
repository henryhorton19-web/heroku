"""Standalone configuration for the engine package.

Settings are loaded from environment variables with the ``ENGINE_`` prefix.
All features default to disabled so that the existing application layer is
unaffected.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "EngineSettings",
    "get_engine_settings",
]

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class EngineSettings(BaseSettings):
    """Engine-wide configuration.

    All fields default to ``False`` or ``None`` so that the engine is
    completely inert unless explicitly enabled.
    """

    model_config = SettingsConfigDict(
        env_prefix="ENGINE_",
        env_file=".env",
        extra="ignore",
    )

    # Master switch
    enabled: bool = False

    # Monitor
    monitor_interval_seconds: int = 5
    monitor_keyword: str = ""
    monitor_max_price_pence: int | None = None

    # Proxy
    proxy_pool_path: Path | None = None

    # TLS
    tls_preset: str = "chrome120"

    # AutoCop (future)
    autocop_enabled: bool = False

    # Cross-lister (future)
    crosslister_enabled: bool = False

    # CAPTCHA (future)
    capsolver_api_key: str | None = None
    captcha_2captcha_api_key: str | None = None

    # AdsPower (future)
    adspower_api_url: str | None = None


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_ENGINE_SETTINGS: EngineSettings | None = None


def get_engine_settings() -> EngineSettings:
    """Return the singleton ``EngineSettings`` instance."""
    global _ENGINE_SETTINGS  # noqa: PLW0603  -- deliberate module-level cache
    if _ENGINE_SETTINGS is None:
        _ENGINE_SETTINGS = EngineSettings()
    return _ENGINE_SETTINGS
