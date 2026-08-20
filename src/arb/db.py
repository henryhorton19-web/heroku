"""Persistence. Mirrors the Part B.4 schema; deviations are listed in SPEC.md.

Two conventions worth stating once:

* Timestamps are stored as ISO-8601 UTC **text**, as the spec's DDL specifies, but
  are typed `datetime` in Python via `UtcDateTime`. That keeps SQLite's lexical
  ordering usable in indexes while making naive-vs-aware confusion impossible at
  the boundary.
* Money is `INTEGER` pence. There is no float column in this schema and there
  should never be one, except the genuinely dimensionless ratios (`roi`,
  confidences, `capital_velocity`).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
    event,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

if TYPE_CHECKING:
    from sqlalchemy import Dialect
    from sqlalchemy.pool import ConnectionPoolEntry

__all__ = [
    "Base",
    "CompsCache",
    "Decisions",
    "Inventory",
    "Listings",
    "Opportunities",
    "SoldObs",
    "TaxonomyAspects",
    "UtcDateTime",
    "VintedRef",
]


class UtcDateTime(TypeDecorator[datetime]):
    """Aware UTC datetime stored as ISO-8601 text.

    Rejects naive datetimes on write rather than silently assuming a timezone,
    which is the usual way timestamp corruption enters a trading ledger.
    """

    impl = String
    cache_ok = True

    @override
    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> str | None:
        del dialect
        if value is None:
            return None
        if value.tzinfo is None:
            msg = "naive datetime rejected; pass an aware UTC datetime"
            raise ValueError(msg)
        return value.astimezone(UTC).isoformat(timespec="seconds")

    @override
    def process_result_value(self, value: str | None, dialect: Dialect) -> datetime | None:
        del dialect
        if value is None:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)


class Base(DeclarativeBase):
    pass


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection: object, connection_record: ConnectionPoolEntry) -> None:
    """Enforce foreign keys and pick durable-but-fast journalling.

    SQLite disables foreign key enforcement by default, which would make every
    `REFERENCES` in the schema decorative. WAL plus `synchronous=NORMAL` is the
    right trade for a single-writer local tool.

    Narrowing on `sqlite3.Connection` rather than annotating the DBAPI protocol
    keeps this type-safe without a cast, and scopes the pragmas to SQLite so the
    listener is inert against any other engine in the process.
    """
    del connection_record
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


class CompsCache(Base):
    """Append-only raw payload store. Never update, never delete, never dedupe.

    SoldComps exposes roughly a 90-day window. This table is the only mechanism by
    which we ever hold a longer series than that, so it is the single most valuable
    object in the database and the one thing that cannot be rebuilt.
    """

    __tablename__ = "comps_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    query_hash: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (Index("idx_comps", "query_hash", "fetched_at"),)


class SoldObs(Base):
    """Parsed sold observations, each traceable back to the raw payload it came from."""

    __tablename__ = "sold_obs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String)
    """The venue's item id. Present so a disappeared active listing can be
    corroborated against a completed sale: a listing vanishing from search means it
    sold *or* was ended unsold, and only this join can tell them apart."""

    brand_norm: Mapped[str | None] = mapped_column(String)
    title_norm: Mapped[str | None] = mapped_column(String)
    size_norm: Mapped[str | None] = mapped_column(String)
    colour_norm: Mapped[str | None] = mapped_column(String)
    condition_band: Mapped[str | None] = mapped_column(String)
    category_id: Mapped[str | None] = mapped_column(String)
    country: Mapped[str | None] = mapped_column(String)
    price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    ship_pence: Mapped[int | None] = mapped_column(Integer)
    listed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    sold_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    days_to_sell: Mapped[int | None] = mapped_column(Integer)
    price_is_upper_bound: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    source_row: Mapped[int | None] = mapped_column(ForeignKey("comps_cache.id"))

    __table_args__ = (
        Index("idx_block", "brand_norm", "size_norm", "condition_band"),
        Index("idx_sold_external", "external_id"),
    )


class Listings(Base):
    """Live listings. `first_seen`/`last_seen`/`disappeared_at` make the row temporal,
    which is what later turns this table into wardrobe tracking without a migration."""

    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String, nullable=False)
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str | None] = mapped_column(String)
    price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    total_pence: Mapped[int | None] = mapped_column(Integer)
    brand_norm: Mapped[str | None] = mapped_column(String)
    title_norm: Mapped[str | None] = mapped_column(String)
    size_norm: Mapped[str | None] = mapped_column(String)
    colour_norm: Mapped[str | None] = mapped_column(String)
    condition_band: Mapped[str | None] = mapped_column(String)
    category_id: Mapped[str | None] = mapped_column(String)
    country: Mapped[str | None] = mapped_column(String)
    seller_id: Mapped[str | None] = mapped_column(String)
    venue_created_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    """When the *venue* says the listing was created, not when we first saw it.

    eBay Browse exposes this as `itemCreationDate`; Vinted's search response does
    not. It is the only honest start point for a time-on-market measurement --
    `first_seen` is when our scanner happened to look, which for a listing that
    existed before we started scanning is arbitrarily late."""

    favourites: Mapped[int | None] = mapped_column(Integer)
    views: Mapped[int | None] = mapped_column(Integer)
    first_seen: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    disappeared_at: Mapped[datetime | None] = mapped_column(UtcDateTime)

    __table_args__ = (UniqueConstraint("venue", "external_id", name="uq_listings_venue_external"),)


class Opportunities(Base):
    """A scored listing. `fee_table_version` is non-null so that no score is ever
    unattributable to the fee assumptions that produced it."""

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(ForeignKey("listings.id"))
    est_p25_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    est_p60_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    comp_n: Mapped[int] = mapped_column(Integer, nullable=False)
    est_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    fees_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    ship_in_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    ship_out_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    net_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    roi: Mapped[float] = mapped_column(Float, nullable=False)
    days_to_sell_p50: Mapped[float | None] = mapped_column(Float)
    capital_velocity: Mapped[float | None] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    fee_table_version: Mapped[str] = mapped_column(String, nullable=False)
    scored_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (Index("idx_rank", "capital_velocity", "scored_at"),)


class Decisions(Base):
    """Every buy and every skip, manual or automated.

    The application layer requires `skip_reason` on a skip; see `models.Decision`.
    The DB permits null so that a partially-written row is still recoverable rather
    than lost, but nothing in this codebase writes one.
    """

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"))
    mode: Mapped[str] = mapped_column(String, nullable=False)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String)
    decided_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    spend_pence: Mapped[int | None] = mapped_column(Integer)


class Inventory(Base):
    """Owned stock and its realised economics. `actual_fees_pence` comes from the eBay
    Sell Fulfillment API and is what `arb reconcile` uses to correct the fee table."""

    __tablename__ = "inventory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    decision_id: Mapped[int | None] = mapped_column(ForeignKey("decisions.id"))
    state: Mapped[str] = mapped_column(
        String, nullable=False, default="scouted", server_default="scouted"
    )
    """Lifecycle state. The timestamps below already imply it, but an implied state
    cannot be queried, counted or aged. As a column, "what is stuck in transit" is
    `WHERE state = 'in_transit' AND acquired_at < ...` rather than a join over three
    nullable dates."""

    cost_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    acquired_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)
    listed_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    sold_at: Mapped[datetime | None] = mapped_column(UtcDateTime)
    gross_pence: Mapped[int | None] = mapped_column(Integer)
    actual_fees_pence: Mapped[int | None] = mapped_column(Integer)
    actual_ship_pence: Mapped[int | None] = mapped_column(Integer)
    realised_net_pence: Mapped[int | None] = mapped_column(Integer)
    sa103_category: Mapped[str | None] = mapped_column(String)

    __table_args__ = (Index("idx_inventory_state", "state", "acquired_at"),)


class TaxonomyAspects(Base):
    """Cached eBay Taxonomy aspect enums, one row per category.

    Raw payload stored and parsed on read, mirroring `comps_cache`: a parser fix
    then costs nothing, where storing the parsed form would need a refetch of every
    category. Unlike `comps_cache` this one *is* refreshable -- eBay's enums are
    public and re-fetchable at any time -- so it carries a unique key per category
    and is upserted rather than appended.

    `category_tree_version` is stored because eBay bumps it when enums change; a
    listing validated under an old version is a listing validated against rules that
    no longer apply.
    """

    __tablename__ = "taxonomy_aspects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    marketplace_id: Mapped[str] = mapped_column(String, nullable=False)
    category_id: Mapped[str] = mapped_column(String, nullable=False)
    category_tree_id: Mapped[str | None] = mapped_column(String)
    category_tree_version: Mapped[str | None] = mapped_column(String)
    payload: Mapped[str] = mapped_column(String, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("marketplace_id", "category_id", name="uq_taxonomy_marketplace_category"),
    )


class VintedRef(Base):
    """Vinted reference tables: brands, catalogs, colours, sizes, statuses.

    Not in the Part B.4 DDL, but Step 0 requires loading these and they need a home.

    **Advisory only.** The public seed carries ~2.5k brands and is missing several
    high-value UK resale names, so this table is safe as a normalisation lookup and
    unsafe as a brand allowlist. Filtering on membership here would silently drop
    exactly the stock worth buying.
    """

    __tablename__ = "vinted_ref"

    kind: Mapped[str] = mapped_column(String, primary_key=True)
    external_id: Mapped[str] = mapped_column(String, primary_key=True)
    code: Mapped[str | None] = mapped_column(String)
    title: Mapped[str] = mapped_column(String, nullable=False)
    title_norm: Mapped[str] = mapped_column(String, nullable=False)
    parent_id: Mapped[str | None] = mapped_column(String)
    item_count: Mapped[int | None] = mapped_column(Integer)
    loaded_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False)

    __table_args__ = (Index("idx_vinted_ref_norm", "kind", "title_norm"),)
