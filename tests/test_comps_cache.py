"""The append-only cache. Its value is entirely in what it refuses to lose."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import arb.comps.cache as cache_module
from arb.comps.cache import append_payload, fresh_payloads, payloads_for
from arb.models import CompQuery

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

T0 = datetime(2026, 8, 1, tzinfo=UTC)
Q = CompQuery(brand_norm="nike", title_norm="air max 90", size_norm="M")


def test_repeat_fetches_accumulate_rather_than_overwrite(session: Session) -> None:
    """This repetition is the point: it is what turns a cache into a time series
    longer than the API's own ninety-day window."""
    for day in range(4):
        append_payload(
            session,
            query=Q,
            source="soldcomps",
            payload={"day": day},
            fetched_at=T0 + timedelta(days=day),
        )
    assert len(payloads_for(session, Q)) == 4


def test_payloads_come_back_newest_first(session: Session) -> None:
    for day in (0, 3, 1):
        append_payload(
            session, query=Q, source="s", payload={"d": day}, fetched_at=T0 + timedelta(days=day)
        )
    rows = payloads_for(session, Q)
    assert [r.fetched_at for r in rows] == sorted((r.fetched_at for r in rows), reverse=True)


def test_freshness_window_limits_what_is_reused(session: Session) -> None:
    """A 100-request monthly quota only works if a repeated query inside the window
    is answered from disk."""
    append_payload(session, query=Q, source="s", payload={"old": 1}, fetched_at=T0)
    append_payload(
        session, query=Q, source="s", payload={"new": 1}, fetched_at=T0 + timedelta(days=5)
    )
    assert len(fresh_payloads(session, Q, not_before=T0 + timedelta(days=1))) == 1


def test_different_queries_do_not_collide(session: Session) -> None:
    other = CompQuery(brand_norm="adidas", title_norm="samba og", size_norm="M")
    append_payload(session, query=Q, source="s", payload={"a": 1})
    append_payload(session, query=other, source="s", payload={"b": 1})
    assert len(payloads_for(session, Q)) == 1
    assert len(payloads_for(session, other)) == 1


def test_payload_is_stored_verbatim_and_canonically(session: Session) -> None:
    """Raw bytes are kept so a wrong parser can be re-run against the original."""
    row = append_payload(session, query=Q, source="s", payload={"b": 2, "a": 1})
    assert row.payload == '{"a":1,"b":2}'


def test_module_exposes_no_delete_path() -> None:
    """Deliberate. This table is the one thing in the database that cannot be
    rebuilt, so there is no supported way to shrink it."""
    assert not [n for n in dir(cache_module) if "delete" in n or "purge" in n or "prune" in n]
