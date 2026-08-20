"""AutoBuy rails. Everything that must be true before money moves automatically.

This module authorises purchases and does not make them. That separation is the point:
the decision to spend is pure, fully testable, and reviewable in one file, while the
part that touches a checkout is somewhere else and does nothing until this says yes.

**Every rail fails closed.** A missing fact, an expired token, an unreadable state row
— all of them refuse. That is the opposite of the usual default, and it is deliberate:
the cost of a wrongly-refused purchase is a missed item, and the cost of a wrongly-
allowed one is money gone at machine speed while nobody is watching.

The rails, and why each exists:

**Fees must be measured.** ROADMAP's one hard ordering rule: do not enable purchase
execution while **P1** is open. Automated spending against invented fee rates repeats
a mistake at machine speed and is one `arb reconcile-fees` run away from being fixed.
This is enforced here rather than documented, by consulting the same placeholder
register `arb provenance` prints.

**Armed, not enabled.** `armed_until` is an expiry rather than a flag. AutoBuy needs
periodic affirmative action to keep running, so walking away from the machine stops
it. A boolean would stay true forever, which is exactly the state you do not want to
discover a fortnight later.

**Three spend caps, not one.** Per run bounds a single bad batch; per day bounds a bad
afternoon; outstanding bounds how much capital can be tied up in unsold stock at once.
They fail differently and a single cap cannot express all three — a per-run cap alone
permits twenty runs an hour.

**Idempotency.** A retry after a crash must not become a second purchase. The key is
derived from venue and listing id, and the uniqueness is enforced by the database
rather than by a code path a retry might skip.

**Confidence floor.** Automation should buy only what it understands well. A valuation
resting on three loosely-matched comps is fine for a human who can look at the photos
and is not fine for a process that cannot.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from arb.sourcing.rank import ScoredCandidate

__all__ = [
    "MAX_ARM_HOURS",
    "MIN_ARM_HOURS",
    "Authorisation",
    "RailBreach",
    "RailContext",
    "SpendCaps",
    "authorise",
    "idempotency_key",
]


MAX_ARM_HOURS = 24
"""Longest AutoBuy may be armed in one go. A day is the point past which "I armed
it and forgot" stops being hypothetical -- the expiry only protects you if it is
shorter than your attention span."""

MIN_ARM_HOURS = 1


class RailBreach(StrEnum):
    """Why a purchase was refused. Every value is a distinct operator response."""

    FEES_UNMEASURED = "fees_unmeasured"
    NOT_ARMED = "not_armed"
    KILL_SWITCH = "kill_switch"
    RUN_CAP = "run_cap"
    DAILY_CAP = "daily_cap"
    OUTSTANDING_CAP = "outstanding_cap"
    DUPLICATE = "duplicate"
    LOW_CONFIDENCE = "low_confidence"
    NO_VELOCITY = "no_velocity"


class SpendCaps(NamedTuple):
    """Three caps, because they bound three different disasters.

    Defaults are deliberately small. The right way to raise them is deliberately,
    after the dry-run has been checked against real decisions -- not by discovering
    that the default was already generous.
    """

    per_run_pence: int = 5_000
    per_day_pence: int = 20_000
    outstanding_pence: int = 50_000


class RailContext(NamedTuple):
    """Every fact the authorisation needs, gathered at the edge so this stays pure."""

    fees_measured: bool
    """False while P1 is open. The one hard ordering rule in the roadmap."""

    armed_until: datetime | None
    kill_switch: bool
    spent_today_pence: int
    outstanding_pence: int
    already_attempted: frozenset[str]
    """Idempotency keys already claimed. Checked here so a batch cannot contain the
    same item twice; the database's unique index is the backstop, not the only guard."""

    caps: SpendCaps = SpendCaps()
    min_confidence: float = 0.6


class Authorisation(NamedTuple):
    approved: tuple[ScoredCandidate, ...]
    refused: tuple[tuple[ScoredCandidate, RailBreach], ...]
    halted: RailBreach | None
    """Set when a global rail stopped the whole batch rather than individual items.
    Distinguished because 'nothing qualified' and 'AutoBuy is disarmed' are different
    situations and look identical in an empty approved list."""

    approved_spend_pence: int

    @property
    def is_halted(self) -> bool:
        return self.halted is not None


def idempotency_key(venue: str, external_id: str) -> str:
    """Stable key for one purchasable listing.

    Derived rather than random so that a retry recomputes the *same* key and is
    refused. A random key per attempt would make every retry look like a new purchase,
    which is the failure this is here to prevent.
    """
    return f"{venue}:{external_id}"


def _cost_of(candidate: ScoredCandidate) -> int:
    """What this purchase actually costs: the price paid, not the estimated resale."""
    listing = candidate.listing
    return listing.total_pence or listing.price_pence


def _global_breach(ctx: RailContext, now: datetime) -> RailBreach | None:
    """Rails that stop the entire batch. Checked before any item is considered."""
    if ctx.kill_switch:
        return RailBreach.KILL_SWITCH
    if not ctx.fees_measured:
        return RailBreach.FEES_UNMEASURED
    if ctx.armed_until is None or ctx.armed_until <= now:
        return RailBreach.NOT_ARMED
    if ctx.spent_today_pence >= ctx.caps.per_day_pence:
        return RailBreach.DAILY_CAP
    if ctx.outstanding_pence >= ctx.caps.outstanding_pence:
        return RailBreach.OUTSTANDING_CAP
    return None


def _item_breach(candidate: ScoredCandidate, ctx: RailContext) -> RailBreach | None:
    """Rails that reject one item without stopping the batch."""
    key = idempotency_key(candidate.listing.venue.value, candidate.listing.external_id)
    if key in ctx.already_attempted:
        return RailBreach.DUPLICATE
    valuation = candidate.opportunity.valuation
    if min(valuation.est_confidence, valuation.match_confidence) < ctx.min_confidence:
        return RailBreach.LOW_CONFIDENCE
    if candidate.opportunity.capital_velocity is None:
        return RailBreach.NO_VELOCITY
    return None


def authorise(
    candidates: Sequence[ScoredCandidate], ctx: RailContext, *, now: datetime
) -> Authorisation:
    """Decide which candidates AutoBuy may buy. Pure: no I/O, no clock read.

    Candidates are considered in the order given, which the caller has already ranked
    by capital velocity. That ordering matters once a cap binds: the run cap should
    stop at the *worst* remaining opportunity, not at an arbitrary one.

    A candidate that would breach the run cap is refused rather than ending the loop,
    so a cheap good item behind an expensive one is still reachable. Refusing and
    continuing costs nothing; stopping early silently reorders the buy list.
    """
    halted = _global_breach(ctx, now)
    if halted is not None:
        return Authorisation(
            approved=(),
            refused=tuple((c, halted) for c in candidates),
            halted=halted,
            approved_spend_pence=0,
        )

    approved: list[ScoredCandidate] = []
    refused: list[tuple[ScoredCandidate, RailBreach]] = []
    spent_run = 0
    seen_in_batch: set[str] = set()

    for candidate in candidates:
        key = idempotency_key(candidate.listing.venue.value, candidate.listing.external_id)
        if key in seen_in_batch:
            refused.append((candidate, RailBreach.DUPLICATE))
            continue
        breach = _item_breach(candidate, ctx)
        if breach is not None:
            refused.append((candidate, breach))
            continue

        cost = _cost_of(candidate)
        if spent_run + cost > ctx.caps.per_run_pence:
            refused.append((candidate, RailBreach.RUN_CAP))
            continue
        if ctx.spent_today_pence + spent_run + cost > ctx.caps.per_day_pence:
            refused.append((candidate, RailBreach.DAILY_CAP))
            continue
        if ctx.outstanding_pence + spent_run + cost > ctx.caps.outstanding_pence:
            refused.append((candidate, RailBreach.OUTSTANDING_CAP))
            continue

        approved.append(candidate)
        seen_in_batch.add(key)
        spent_run += cost

    return Authorisation(
        approved=tuple(approved),
        refused=tuple(refused),
        halted=None,
        approved_spend_pence=spent_run,
    )
