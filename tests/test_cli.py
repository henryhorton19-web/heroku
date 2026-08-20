"""CLI smoke tests. Every command runs against a temporary database."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from arb import __version__
from arb.cli import app
from arb.config import get_settings
from tests.conftest import FIXTURES

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Point settings at a temp DB and clear the cache so no test sees a real .env."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARB_DB_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("ARB_EBAY_REST_CONFIG", str(tmp_path / "absent.json"))
    monkeypatch.delenv("ARB_SOLDCOMPS_API_KEY", raising=False)
    monkeypatch.delenv("ARB_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ARB_APIFY_TOKEN", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "load-refdata" in result.output
    assert "doctor" in result.output


def test_db_upgrade_then_current() -> None:
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    result = runner.invoke(app, ["db", "current"])
    assert result.exit_code == 0


def test_db_upgrade_is_idempotent() -> None:
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0
    assert runner.invoke(app, ["db", "upgrade"]).exit_code == 0


def test_doctor_runs_with_no_credentials() -> None:
    """The scaffold must be usable before any API key exists."""
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "not set" in result.output
    assert "refdata     0 rows" in result.output


def test_doctor_reports_loaded_refdata() -> None:
    runner.invoke(app, ["db", "upgrade"])
    runner.invoke(app, ["load-refdata", "--data-dir", str(FIXTURES / "vinted_ref")])
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "refdata     0 rows" not in result.output
    assert "brands)" in result.output


def test_load_refdata_reports_counts() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["load-refdata", "--data-dir", str(FIXTURES / "vinted_ref")])
    assert result.exit_code == 0
    assert "brand" in result.output
    assert "catalog" in result.output


def test_load_refdata_missing_directory_exits_two() -> None:
    runner.invoke(app, ["db", "upgrade"])
    result = runner.invoke(app, ["load-refdata", "--data-dir", "/nonexistent/path"])
    assert result.exit_code == 2


def test_load_refdata_empty_directory_exits_one(tmp_path: Path) -> None:
    runner.invoke(app, ["db", "upgrade"])
    empty = tmp_path / "empty"
    empty.mkdir()
    result = runner.invoke(app, ["load-refdata", "--data-dir", str(empty)])
    assert result.exit_code == 1


def test_doctor_exits_one_when_the_database_is_unmigrated() -> None:
    """`doctor` reports rather than fixes, but an unusable database is fatal --
    every other command depends on it."""
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "UNUSABLE" in result.output
