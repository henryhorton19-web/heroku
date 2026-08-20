"""Monitors: a scheduler and a seen-set diff wrapped around `scan()`.

Nothing inside `scan` changes to support this, which was the point of keeping it a
pure function in W1. A monitor is a loop, a set difference, and a notifier — the
judgement all still lives in `quality`, `contest`, `valuation` and `rank`.

**The failure this module is built around: a monitor that has stopped working looks
exactly like a quiet market.** Both produce silence. A crashed scheduler, an expired
session, a changed endpoint and a genuinely empty market are indistinguishable from
the outside, and the difference only surfaces weeks later when you notice you have
bought nothing. So every run writes a `monitor_runs` row **whether it succeeds or
fails**, and `monitor_health` reports staleness as a first-class condition rather
than leaving absence to be inferred.

**Alerting is on newly-*seen* listings, not on every ranked one.** The listings table
is the seen set: `upsert_listing` preserves `first_seen` on conflict, so an item we
have already considered stays considered. Without that, every poll re-alerts on the
same standing inventory and the notifications become noise inside a day — at which
point they get muted, and the monitor is off without anyone deciding to turn it off.

**Rate limiting is the caller's job and it is not optional.** Vinted's terms forbid
automated access; the exposure is the trading account. `Settings.vinted_requests_per_second`
defaults to 1.5 and is capped at 2.0, and a monitor is precisely the thing that would
otherwise poll as fast as the network allows.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import desc, select

from arb.db import Listings, MonitorRuns

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.orm import Session

    from arb.models import Listing, Venue
    from arb.sourcing.rank import ScoredCandidate
    from arb.sourcing.scanner import ScanOutcome

__all__ = [
    "ALERT_ITEM_CAP",
    "STALE_AFTER",
    "MonitorHealth",
    "MonitorReport",
    "RunRecord",
    "RunStatus",
    "known_external_ids",
    "monitor_health",
    "new_candidates",
    "record_run",
]

ALERT_ITEM_CAP = 10
"""Items listed in full in an alert before it summarises. An alert listing four
hundred items is one nobody reads, which is the same as no alert."""

STALE_AFTER = timedelta(hours=6)
"""How long without a successful run before a monitor is presumed broken. Six hours
is a judgement, not a measurement: long enough to survive a transient outage, short
enough that a dead monitor is noticed the same day rather than the same month."""


class RunStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"


class MonitorReport(NamedTuple):
    """What one monitor pass found."""

    monitor: str
    listings_seen: int
    new_listings: tuple[str, ...]
    alerts: tuple[ScoredCandidate, ...]
    """Ranked opportunities that are *also* newly seen. The intersection matters: a
    good opportunity we alerted on yesterday is not news today."""

    @property
    def has_alerts(self) -> bool:
        return bool(self.alerts)


class MonitorHealth(NamedTuple):
    monitor: str
    last_success: datetime | None
    last_status: RunStatus | None
    consecutive_failures: int
    stale: bool
    """True when nothing has succeeded within `STALE_AFTER`. Reported as a condition
    in its own right: silence from a broken monitor and silence from a quiet market
    are the same observation, and only this distinguishes them."""


def known_external_ids(session: Session, venue: Venue) -> set[str]:
    """Every listing id already recorded for a venue -- the seen set.

    Read from `listings` rather than a dedicated table because `upsert_listing`
    already maintains exactly this, preserving `first_seen` on conflict. A second
    seen-set store would be a second thing to keep in sync, and the two would drift.
    """
    rows = session.scalars(select(Listings.external_id).where(Listings.venue == venue.value)).all()
    return {str(row) for row in rows}


def new_candidates(
    outcome: ScanOutcome, known: set[str], *, monitor: str, listings_seen: int
) -> MonitorReport:
    """Intersect this scan's ranked opportunities with what we had not seen. Pure.

    An opportunity is alert-worthy only if it is *both* ranked and new. Alerting on
    everything ranked re-sends yesterday's standing inventory on every poll, and
    notifications that repeat get muted -- which turns the monitor off without anyone
    deciding to turn it off.
    """
    alerts = tuple(c for c in outcome.ranked if c.listing.external_id not in known)
    seen_now = {c.listing.external_id for c in outcome.ranked}
    seen_now |= {r.listing.external_id for r in outcome.rejected_quality}
    seen_now |= {r.listing.external_id for r in outcome.rejected_contest}
    seen_now |= {r.listing.external_id for r in outcome.unpriceable}
    return MonitorReport(
        monitor=monitor,
        listings_seen=listings_seen,
        new_listings=tuple(sorted(seen_now - known)),
        alerts=alerts,
    )


class RunRecord(NamedTuple):
    """One run's outcome. Grouped rather than passed as seven arguments, the same
    pattern and the same reason as `ScoreContext`."""

    monitor: str
    started_at: datetime
    finished_at: datetime
    status: RunStatus
    report: MonitorReport | None = None
    error: str | None = None


def record_run(session: Session, run: RunRecord) -> int:
    """Write the heartbeat. Called on the failure path too, deliberately."""
    monitor, status = run.monitor, run.status
    report, error = run.report, run.error
    row = MonitorRuns(
        monitor=monitor,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=status.value,
        listings_seen=report.listings_seen if report else 0,
        new_listings=len(report.new_listings) if report else 0,
        ranked=len(report.alerts) if report else 0,
        error=error,
    )
    session.add(row)
    session.flush()
    return row.id


def monitor_health(session: Session, monitor: str, *, now: datetime) -> MonitorHealth:
    """Whether a monitor is actually running. `now` is passed in, never read."""
    runs = session.scalars(
        select(MonitorRuns)
        .where(MonitorRuns.monitor == monitor)
        .order_by(desc(MonitorRuns.started_at))
        .limit(50)
    ).all()
    if not runs:
        return MonitorHealth(
            monitor=monitor,
            last_success=None,
            last_status=None,
            consecutive_failures=0,
            stale=True,
        )

    last_success = next((r.started_at for r in runs if r.status == RunStatus.OK.value), None)
    failures = 0
    for run in runs:
        if run.status == RunStatus.OK.value:
            break
        failures += 1
    return MonitorHealth(
        monitor=monitor,
        last_success=last_success,
        last_status=RunStatus(runs[0].status),
        consecutive_failures=failures,
        stale=last_success is None or (now - last_success) > STALE_AFTER,
    )


def alert_body(report: MonitorReport, listings: Sequence[Listing] | None = None) -> str:
    """Render an alert. Plain text: it has to survive Slack, Telegram and SMS alike."""
    del listings
    lines = [f"{len(report.alerts)} new opportunit{'y' if len(report.alerts) == 1 else 'ies'}"]
    for candidate in report.alerts[:ALERT_ITEM_CAP]:
        opportunity = candidate.opportunity
        velocity = opportunity.capital_velocity
        lines.append(
            f"{candidate.listing.attrs.title_norm[:44]} "
            f"net {opportunity.net_pence / 100:.2f} "
            f"roi {opportunity.roi:.0%} "
            f"vel {velocity:.4f}"
            if velocity is not None
            else ""
        )
    if len(report.alerts) > ALERT_ITEM_CAP:
        lines.append(f"... and {len(report.alerts) - ALERT_ITEM_CAP} more")
    return "\n".join(line for line in lines if line)
