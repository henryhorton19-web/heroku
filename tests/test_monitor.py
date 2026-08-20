"""Monitors.

The failure this module exists to prevent: **a monitor that has stopped working looks
exactly like a quiet market.** Both produce silence. These tests pin the two things
that distinguish them — a heartbeat written on the failure path, and staleness
reported as a condition rather than left to be inferred from absence.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from arb.models import Attributes, Listing, Opportunity, Valuation, Venue
from arb.monitor import (
    STALE_AFTER,
    MonitorReport,
    RunRecord,
    RunStatus,
    alert_body,
    known_external_ids,
    monitor_health,
    new_candidates,
    record_run,
)
from arb.repo import upsert_listing
from arb.sourcing.rank import ScanResult, ScoredCandidate
from arb.sourcing.scanner import RejectedListing, ScanOutcome

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

T0 = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
MONITOR = "nike-air-max"


def _listing(external_id: str) -> Listing:
    return Listing(
        venue=Venue.VINTED,
        external_id=external_id,
        price_pence=1200,
        attrs=Attributes(brand_norm="nike", title_norm="nike air max 90", size_norm="9"),
        first_seen=T0,
        last_seen=T0,
    )


def _scored(external_id: str) -> ScoredCandidate:
    return ScoredCandidate(
        _listing(external_id),
        Opportunity(
            listing_id=0,
            valuation=Valuation(
                est_p25_pence=4000,
                est_p60_pence=5000,
                comp_n=7,
                est_confidence=0.8,
                match_confidence=0.8,
            ),
            fees_pence=600,
            ship_in_pence=0,
            ship_out_pence=320,
            net_pence=1880,
            roi=1.5,
            capital_velocity=0.05,
            fee_table_version="ebay_uk@test",
            scored_at=T0,
        ),
    )


def _outcome(ranked: tuple[str, ...] = (), rejected: tuple[str, ...] = ()) -> ScanOutcome:
    return ScanOutcome(
        result=ScanResult(
            ranked=tuple(_scored(i) for i in ranked),
            suppressed_unknown_velocity=0,
            suppressed_below_floor=0,
        ),
        rejected_quality=tuple(RejectedListing(_listing(i), "quality:damage") for i in rejected),
        unpriceable=(),
    )


# ---------------------------------------------------------------- the seen set


def test_the_listings_table_is_the_seen_set(session: Session) -> None:
    """Read from `listings` rather than a second store: `upsert_listing` already
    maintains exactly this, and two seen-sets would drift."""
    upsert_listing(session, _listing("a"))
    assert known_external_ids(session, Venue.VINTED) == {"a"}


def test_the_seen_set_is_per_venue(session: Session) -> None:
    upsert_listing(session, _listing("a"))
    assert known_external_ids(session, Venue.EBAY) == set()


def test_an_empty_database_has_seen_nothing(session: Session) -> None:
    assert known_external_ids(session, Venue.VINTED) == set()


# ---------------------------------------------------------------- alerting


def test_only_newly_seen_opportunities_alert() -> None:
    """The load-bearing rule. Alerting on everything ranked re-sends yesterday's
    standing inventory every poll; repeated notifications get muted, and then the
    monitor is off without anyone deciding to turn it off."""
    report = new_candidates(
        _outcome(ranked=("old", "new")), known={"old"}, monitor=MONITOR, listings_seen=2
    )
    assert [c.listing.external_id for c in report.alerts] == ["new"]


def test_an_opportunity_already_seen_does_not_alert_again() -> None:
    report = new_candidates(_outcome(ranked=("a",)), known={"a"}, monitor=MONITOR, listings_seen=1)
    assert not report.has_alerts


def test_new_listings_counts_rejects_too() -> None:
    """A rejected listing is still one we have now considered. Leaving it out of the
    seen set means re-fetching comps for it on the next poll."""
    report = new_candidates(
        _outcome(ranked=("a",), rejected=("b",)), known=set(), monitor=MONITOR, listings_seen=2
    )
    assert set(report.new_listings) == {"a", "b"}
    assert [c.listing.external_id for c in report.alerts] == ["a"]


def test_a_quiet_poll_produces_no_alerts() -> None:
    report = new_candidates(_outcome(), known=set(), monitor=MONITOR, listings_seen=0)
    assert not report.has_alerts
    assert report.new_listings == ()


def test_the_alert_body_is_plain_text_and_bounded() -> None:
    """It has to survive Slack, Telegram and SMS alike, and an alert listing four
    hundred items is one nobody reads."""
    report = new_candidates(
        _outcome(ranked=tuple(str(i) for i in range(25))),
        known=set(),
        monitor=MONITOR,
        listings_seen=25,
    )
    body = alert_body(report)
    assert "25 new opportunities" in body
    assert "and 15 more" in body


# ---------------------------------------------------------------- the heartbeat


def _ok(minutes: int = 0, seen: int = 10) -> RunRecord:
    return RunRecord(
        monitor=MONITOR,
        started_at=T0 + timedelta(minutes=minutes),
        finished_at=T0 + timedelta(minutes=minutes),
        status=RunStatus.OK,
        report=MonitorReport(MONITOR, seen, (), ()),
    )


def _failed(minutes: int = 0, error: str = "boom") -> RunRecord:
    return RunRecord(
        monitor=MONITOR,
        started_at=T0 + timedelta(minutes=minutes),
        finished_at=T0 + timedelta(minutes=minutes),
        status=RunStatus.FAILED,
        error=error,
    )


def test_a_monitor_that_never_ran_is_stale(session: Session) -> None:
    """Not 'healthy with no alerts'. Never having run is the same silence as a quiet
    market, and it must not read as fine."""
    health = monitor_health(session, MONITOR, now=T0)
    assert health.stale
    assert health.last_success is None


def test_a_recent_success_is_not_stale(session: Session) -> None:
    record_run(session, _ok())
    assert not monitor_health(session, MONITOR, now=T0 + timedelta(minutes=5)).stale


def test_an_old_success_goes_stale(session: Session) -> None:
    record_run(session, _ok())
    later = T0 + STALE_AFTER + timedelta(minutes=1)
    assert monitor_health(session, MONITOR, now=later).stale


def test_failures_are_recorded_not_skipped(session: Session) -> None:
    """A crashed run that leaves no trace is indistinguishable from one that never
    started, which is the whole failure this table prevents."""
    record_run(session, _failed(error="session expired"))
    health = monitor_health(session, MONITOR, now=T0)
    assert health.last_status is RunStatus.FAILED
    assert health.stale


def test_consecutive_failures_are_counted(session: Session) -> None:
    for i in range(3):
        record_run(session, _failed(minutes=i))
    assert monitor_health(session, MONITOR, now=T0).consecutive_failures == 3


def test_a_success_resets_the_failure_streak(session: Session) -> None:
    record_run(session, _failed())
    record_run(session, _ok(minutes=1, seen=5))
    health = monitor_health(session, MONITOR, now=T0 + timedelta(minutes=2))
    assert health.consecutive_failures == 0
    assert not health.stale


def test_failures_after_a_success_still_report_the_last_success(session: Session) -> None:
    record_run(session, _ok(seen=5))
    record_run(session, _failed(minutes=1))
    health = monitor_health(session, MONITOR, now=T0 + timedelta(minutes=2))
    assert health.last_success == T0
    assert health.consecutive_failures == 1
    assert not health.stale


def test_monitors_do_not_share_health(session: Session) -> None:
    record_run(session, _ok(seen=5))
    assert monitor_health(session, "other", now=T0).stale
