"""Shared fixtures. Tests never touch a live API or a real database file."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.orm import Session, sessionmaker

from arb.config import Settings
from arb.store import make_engine, upgrade_to_head

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy import Engine

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_ambient_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from a real .env and from ARB_* in the developer's shell.

    Autouse and unconditional: a test that silently picked up live credentials could
    hit a real API, and the build plan's whole testing posture is that it never does.
    """
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("ARB_"):
            monkeypatch.delenv(name, raising=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def settings(db_path: Path, tmp_path: Path) -> Settings:
    """Settings with no credentials, exercising the bare-scaffold path."""
    return Settings(
        db_path=db_path,
        data_dir=tmp_path / "data",
        ebay_rest_config=tmp_path / "absent.json",
    )


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    """A migrated database. Built by running Alembic, not create_all, so the tests
    exercise the same path a real run does."""
    upgrade_to_head(settings.db_url)
    eng = make_engine(settings.db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A session that never auto-commits and always unwinds.

    Deliberately not `session_scope`: several tests provoke an IntegrityError, which
    leaves the session needing a rollback before it can be closed. Rolling back in
    `finally` keeps those tests from poisoning teardown.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    sess = factory()
    try:
        yield sess
    finally:
        sess.rollback()
        sess.close()
