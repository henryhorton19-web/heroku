from __future__ import annotations

from unittest.mock import MagicMock, patch

from engine.cli import app as engine_app
from typer.testing import CliRunner

runner = CliRunner()


def test_engine_monitor_disabled() -> None:
    with patch("engine.cli.get_engine_settings") as mock_settings:
        mock_settings.return_value.enabled = False
        result = runner.invoke(engine_app, ["--keyword", "nike"])
        assert result.exit_code == 1


def test_engine_monitor_enabled() -> None:
    with (
        patch("engine.cli.get_engine_settings") as mock_settings,
        patch("engine.cli.ProxyPool.from_env"),
        patch("engine.cli.run_monitor_loop", new_callable=MagicMock),
        patch("engine.cli.asyncio.run"),
    ):
        mock_settings.return_value.enabled = True
        mock_settings.return_value.tls_preset = "chrome120"
        result = runner.invoke(engine_app, ["--keyword", "nike", "--max-price", "20.0"])
        assert result.exit_code == 0
