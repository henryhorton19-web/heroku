"""Persistence tests, including the migration-drift gate.

The drift gate is the important one: models and migrations diverging is the failure
mode where everything passes locally and the deployed schema is wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import insert, inspect, select, text
from sqlalchemy.exc import IntegrityError, StatementError

from arb.db import Base, CompsCache, Decisions, Listings, Opportunities

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)

EXPECTED_TABLES = {
    "comps_cache",
    "decisions",
    "inventory",
    "listings",
    "opportunities",
    "sold_obs",
    "vinted_ref",
}


def test_migration_creates_every_table(engine: Engine) -> None:
    present = set(inspect(engine).get_table_names())
    assert present >= EXPECTED_TABLES


def test_models_and_migrations_do_not_drift(engine: Engine) -> None:
    """Compare the migrated database against the ORM metadata.

    A non-empty diff means someone changed a model without writing a migration.
    This is the check that makes `alembic` the single source of truth rather than a
    thing that is usually up to date.
    """
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "target_metadata": Base.metadata}
        )
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], f"schema drift between models and migrations: {diff}"


def test_foreign_keys_are_enforced(session: Session) -> None:
    """SQLite ignores foreign keys unless told otherwise, which would make every
    REFERENCES in the schema decorative. The connect-time pragma is what fixes it,
    so it needs a test."""
    session.add(
        Decisions(
            opportunity_id=999_999,
            mode="manual",
            outcome="skipped",
            skip_reason="orphan",
            decided_at=T0,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_listing_venue_external_id_is_unique(session: Session) -> None:
    for _ in range(2):
        session.add(
            Listings(
                venue="vinted",
                external_id="dup-1",
                price_pence=1000,
                first_seen=T0,
                last_seen=T0,
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_same_external_id_on_different_venues_is_allowed(session: Session) -> None:
    session.add_all(
        [
            Listings(
                venue="vinted", external_id="shared", price_pence=1, first_seen=T0, last_seen=T0
            ),
            Listings(
                venue="ebay", external_id="shared", price_pence=1, first_seen=T0, last_seen=T0
            ),
        ]
    )
    session.flush()
    assert session.scalars(select(Listings)).all()


def test_timestamps_round_trip_as_utc(session: Session) -> None:
    aware_offset = datetime(2026, 8, 1, 13, 0, tzinfo=timezone(timedelta(hours=1)))
    session.add(
        CompsCache(query_hash="h", source="soldcomps", payload="{}", fetched_at=aware_offset)
    )
    session.flush()
    session.expire_all()
    row = session.scalars(select(CompsCache)).one()
    assert row.fetched_at.tzinfo is not None
    assert row.fetched_at == T0
    assert row.fetched_at.utcoffset() == timedelta(0)


def test_naive_timestamps_are_rejected(session: Session) -> None:
    """A naive datetime is ambiguous, and the ambiguity would only surface as a
    wrong days_to_sell months later."""
    session.add(
        CompsCache(
            query_hash="h",
            source="soldcomps",
            payload="{}",
            fetched_at=T0.replace(tzinfo=None),
        )
    )
    with pytest.raises(StatementError, match="naive datetime rejected") as excinfo:
        session.flush()
    assert isinstance(excinfo.value.orig, ValueError)


def test_comps_cache_accepts_repeated_hashes(session: Session) -> None:
    """The cache is append-only: the same query fetched twice is two rows, and that
    is what accumulates into a trend series longer than the API's own window."""
    for day in range(3):
        session.add(
            CompsCache(
                query_hash="same",
                source="soldcomps",
                payload=f'{{"day":{day}}}',
                fetched_at=T0 + timedelta(days=day),
            )
        )
    session.flush()
    rows = session.scalars(select(CompsCache).where(CompsCache.query_hash == "same")).all()
    assert len(rows) == 3


def test_opportunity_requires_fee_table_version(session: Session) -> None:
    """No score may be unattributable to the fee assumptions that produced it."""
    stmt = insert(Opportunities).values(
        est_p25_pence=1000,
        est_p60_pence=1500,
        comp_n=5,
        est_confidence=0.8,
        match_confidence=0.9,
        fees_pence=100,
        ship_in_pence=200,
        ship_out_pence=300,
        net_pence=400,
        roi=0.4,
        fee_table_version=None,
        scored_at=T0,
    )
    with pytest.raises(IntegrityError):
        session.execute(stmt)


def test_qty_defaults_to_one(session: Session) -> None:
    """The bundle seam. `qty` exists now so wholesale economics is qty=N later."""
    session.add(
        Opportunities(
            listing_id=None,
            est_p25_pence=1000,
            est_p60_pence=1500,
            comp_n=5,
            est_confidence=0.8,
            match_confidence=0.9,
            fees_pence=100,
            ship_in_pence=200,
            ship_out_pence=300,
            net_pence=400,
            roi=0.4,
            fee_table_version="abc123",
            scored_at=T0,
        )
    )
    session.flush()
    session.expire_all()
    assert session.scalars(select(Opportunities)).one().qty == 1


def test_naive_stored_text_is_read_back_as_utc(session: Session) -> None:
    """Defensive path. Our own writes are always aware, but a row inserted by hand
    or by a future raw-SQL migration would carry no offset; reading it as anything
    other than UTC would silently shift every derived days_to_sell."""
    session.execute(
        text(
            "INSERT INTO comps_cache (query_hash, source, payload, fetched_at) "
            "VALUES ('h', 'manual', '{}', '2026-08-01T12:00:00')"
        )
    )
    row = session.scalars(select(CompsCache)).one()
    assert row.fetched_at == T0
