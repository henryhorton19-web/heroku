"""Engine, session, and migration plumbing.

Schema changes go through Alembic, never through `create_all`. The one exception is
tests, which build a throwaway in-memory schema from metadata directly; a dedicated
test asserts the two cannot drift apart.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

__all__ = ["MIGRATIONS_PATH", "alembic_config", "make_engine", "session_scope", "upgrade_to_head"]

MIGRATIONS_PATH = Path(__file__).resolve().parent / "migrations"


def make_engine(db_url: str, *, echo: bool = False) -> Engine:
    return create_engine(db_url, echo=echo, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transactional scope. Commits on success, rolls back on any exception."""
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def alembic_config(db_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_PATH))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def upgrade_to_head(db_url: str) -> None:
    command.upgrade(alembic_config(db_url), "head")
