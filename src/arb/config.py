"""Configuration. Every credential is optional so the scaffold runs with no secrets.

Nothing here is committed. `.env`, `ebay_rest.json` and `arb.db` are gitignored and
have `.example` counterparts; the repo is public, so that is not a soft rule.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Settings", "get_settings"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ARB_",
        extra="ignore",
        frozen=True,
    )

    db_path: Path = Field(default=Path("arb.db"))
    data_dir: Path = Field(default=Path("data"))

    # Credentials. All optional: absent means the relevant step is unavailable,
    # not that the tool fails to start.
    ebay_rest_config: Path = Field(default=Path("ebay_rest.json"))
    soldcomps_api_key: SecretStr | None = None
    apify_token: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    vinted_base_url: str = "https://www.vinted.co.uk"

    # Vinted asks not to be scraped. Politeness here is both the terms-of-service
    # posture and the thing that keeps the account alive; see CONSTRAINTS in SPEC.md.
    vinted_requests_per_second: float = Field(default=1.5, gt=0.0, le=2.0)
    vinted_max_retries: int = Field(default=5, ge=0, le=10)

    comps_freshness_days: int = Field(default=7, ge=1, le=90)
    """How long a cached comps payload stays usable. Seven days trades a little
    staleness for roughly a 10x reduction in requests against a 100/month tier."""

    # Precision over recall: below this comp count we refuse to return an estimate
    # rather than return a confident-looking wrong one.
    min_comp_n: int = Field(default=3, ge=1)

    @property
    def db_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path}"

    @field_validator("db_path", "data_dir", "ebay_rest_config")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings. Cached so the `.env` file is read once.

    Tests that need different settings construct `Settings(...)` directly rather
    than mutating this, and call `get_settings.cache_clear()` if they must.
    """
    return Settings()
