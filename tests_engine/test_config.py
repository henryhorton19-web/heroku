from __future__ import annotations

import os
from unittest.mock import patch

from engine.config import EngineSettings, get_engine_settings


def test_engine_settings_defaults() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = EngineSettings()
        assert settings.enabled is False


def test_get_engine_settings_cached() -> None:
    settings = get_engine_settings()
    assert isinstance(settings, EngineSettings)
