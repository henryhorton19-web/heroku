"""The placeholder register: which numbers here are still assumptions.

Almost every figure this tool produces rests on something nobody has measured. The
fees are invented, `days_to_sell` has no source, the quality lexicon is a first
guess, postage is a constant. That is a deliberate and defensible way to build --
you cannot measure fees before you have sold anything -- but it is only defensible
while the assumptions stay visible. The moment a placeholder is mistaken for a
measurement, the tool is confidently wrong, which is the one failure mode worth real
effort to avoid.

Placeholder discipline is four rules. Three already exist:

1. **Declared** -- `provisional: true` in the fee YAML, `provisional` on the contest
   policy.
2. **Versioned** -- content-hashed, so any edit produces a new identity.
3. **Stamped** -- `fee_table_version` on every opportunity it influenced.

This module is the fourth: **listed**. One command shows everything still running on
assumptions, resolved against what the database actually contains.

**The resolution defaults to open, always.** Only positive evidence closes a
placeholder, and "nothing to check" resolves to `UNKNOWN` rather than to closed --
an empty fee directory technically satisfies "no table is provisional" and must not
read as green. This is the same precision-over-recall posture as `value()` returning
`None` below the comp floor: refusing to answer beats answering wrongly.

The split between `gather` and `resolve` follows `pipeline.py` -- all I/O at the
edge, judgement in a pure function. `resolve` is where every threshold lives, so it
is exhaustively testable without a database.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

import yaml
from sqlalchemy import func, select

from arb.db import Decisions, Inventory, Opportunities, SoldObs
from arb.pipeline import DEFAULT_SCAN_SETTINGS
from arb.selling.reprice import DEFAULT_REPRICE_POLICY
from arb.sourcing.contest import DEFAULT_CONTEST_POLICY
from arb.sourcing.rank import VelocityPolicy

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.orm import Session

__all__ = [
    "REGISTER",
    "LiveState",
    "Placeholder",
    "PlaceholderState",
    "PlaceholderStatus",
    "gather",
    "resolve",
]


class PlaceholderStatus(StrEnum):
    OPEN = "open"
    """Positive evidence the assumption is still in force."""

    CLOSED = "closed"
    """Positive evidence it has been replaced by a measurement."""

    UNKNOWN = "unknown"
    """The check could not be performed. Treated as open, reported distinctly --
    'I could not tell' and 'I checked and it is fine' must never look the same."""


class Placeholder(NamedTuple):
    """One declared gap between what this tool asserts and what it has measured."""

    id: str
    gap: str
    standing_in: str
    """What is being used instead of a measurement."""
    closed_by: str
    """The real source. A placeholder with no route to one is a permanent guess."""
    blast_radius: str
    """What is wrong downstream if the assumption is wrong. This is the field that
    decides whether an open placeholder is tolerable or urgent."""


REGISTER: tuple[Placeholder, ...] = (
    Placeholder(
        id="P1",
        gap="eBay and Vinted selling fees",
        standing_in="invented rates in provisional YAML",
        closed_by="`arb reconcile` against Sell Fulfillment settlement data",
        blast_radius="every margin and every buy decision",
    ),
    Placeholder(
        id="P2",
        gap="days to sell",
        standing_in="assumed 30 days where the ranking needs a denominator",
        closed_by="`arb sweep`: corroborated active-to-sold transitions",
        blast_radius="ranking order only; NET and CONF are unaffected",
    ),
    Placeholder(
        id="P3",
        gap="valuation accuracy",
        standing_in="unvalidated; est_confidence is a shape, not a track record",
        closed_by="`arb backtest` over 100 items with known realised prices",
        blast_radius="how much any estimate can be trusted at all",
    ),
    Placeholder(
        id="P4",
        gap="quality lexicon",
        standing_in="v0 word list, expected to be wrong at the edges",
        closed_by="false-negative rate measured on realised trades",
        blast_radius="missed buys, and bad buys let through",
    ),
    Placeholder(
        id="P5",
        gap="postage in and out",
        standing_in="config constants, flat across every item",
        closed_by="measured per carrier and size band from real shipments",
        blast_radius="net margin, roughly three to four pounds per trade",
    ),
    Placeholder(
        id="P6",
        gap="condition discount",
        standing_in="none applied; a worn item is valued like a clean one",
        closed_by="fitted from realised price against condition band",
        blast_radius="over-values worn stock, systematically",
    ),
    Placeholder(
        id="P7",
        gap="ledger and dashboard figures",
        standing_in="no settled sales; anything shown is synthetic",
        closed_by="real completed sales with settlement data",
        blast_radius="display only, but every number on screen",
    ),
    Placeholder(
        id="P8",
        gap="AutoBuy evaluation set",
        standing_in="too few real decisions to score a dry-run against",
        closed_by="accumulated manual decisions in the decisions table",
        blast_radius="a dry-run that means nothing while it looks like it does",
    ),
    Placeholder(
        id="P9",
        gap="contest-density thresholds",
        standing_in="invented favourite cap and save rate",
        closed_by="realised win rate: which attempted buys were gone before checkout",
        blast_radius="silently skipped good stock, or lost races we entered anyway",
    ),
    Placeholder(
        id="P10",
        gap="repricing decay window",
        standing_in="assumed 30 days from optimal price to fast-sale price",
        closed_by="realised days-to-sell against the price the item actually cleared at",
        blast_radius="capital sits too long, or margin is given away too early",
    ),
)
"""Ten declared gaps. Adding a tenth means adding a resolver -- `resolve` asserts
coverage, so a placeholder cannot be registered and then quietly never checked."""

BACKTEST_ITEMS = 100
"""ROADMAP section 9: 100 labelled items closes P3."""

RETUNE_TRADES = 20
"""First 20 realised trades closes P4 and P6."""

MEASURED_SHIPMENTS = 10
"""First 10 shipments closes P5."""

AUTOBUY_DECISIONS = 50
"""First 50 real decisions closes P8."""


class LiveState(NamedTuple):
    """Everything the resolution needs, gathered once so `resolve` stays pure."""

    provisional_fee_tables: tuple[str, ...]
    verified_fee_tables: tuple[str, ...]
    sold_obs_total: int
    sold_obs_with_days: int
    real_decisions: int
    """Manual and AutoBuy decisions. Dry-runs are excluded: counting the thing being
    validated as evidence that validation is possible would be circular."""
    realised_sales: int
    settled_sales: int
    """Sales whose actual fees have come back. Only these can correct a fee table."""
    measured_shipments: int
    velocity_policy: VelocityPolicy
    ship_in_pence: int
    ship_out_pence: int
    contest_provisional: bool
    contest_version: str
    fee_versions_in_use: tuple[tuple[str, int], ...]
    """Fee table version to the number of opportunities scored under it. This is what
    stamping was for: after a correction, it tells you how much of the book needs
    re-scoring, and more than one entry means the book is not internally comparable."""

    reprice_provisional: bool = True
    reprice_version: str = "reprice-v0"


class PlaceholderState(NamedTuple):
    placeholder: Placeholder
    status: PlaceholderStatus
    evidence: str
    """Why the status is what it is, in numbers. 'open' alone is not actionable;
    '12 of 50 real decisions' tells you how far off you are."""


def _resolve_fees(state: LiveState) -> tuple[PlaceholderStatus, str]:
    provisional = state.provisional_fee_tables
    verified = state.verified_fee_tables
    total = len(provisional) + len(verified)
    if total == 0:
        return PlaceholderStatus.UNKNOWN, "no fee tables found -- nothing to check"
    if provisional:
        listed = ", ".join(sorted(provisional))
        return PlaceholderStatus.OPEN, f"{len(provisional)} of {total} tables provisional: {listed}"
    return PlaceholderStatus.CLOSED, f"all {total} tables verified against settlement data"


MIN_DURATIONS = 30
"""Corroborated durations before P2 is worth closing. Below this the median is one
slow listing away from moving, and the whole point of the sweep is that the number it
produces is trustworthy enough to rank on."""


def _resolve_velocity(state: LiveState) -> tuple[PlaceholderStatus, str]:
    policy = state.velocity_policy.value
    observed = state.sold_obs_with_days
    detail = f"{observed} of {state.sold_obs_total} sold observations carry days_to_sell"
    if 0 < observed < MIN_DURATIONS:
        return (
            PlaceholderStatus.OPEN,
            f"{detail}; {observed} of {MIN_DURATIONS} needed before the median is stable",
        )
    if observed == 0:
        return PlaceholderStatus.OPEN, f"{detail}; policy={policy}"
    if state.velocity_policy is VelocityPolicy.ASSUME_DEFAULT:
        return PlaceholderStatus.OPEN, f"{detail}, but policy={policy} still assumes a default"
    return PlaceholderStatus.CLOSED, f"{detail}; policy={policy}"


def _counted(have: int, need: int, noun: str) -> tuple[PlaceholderStatus, str]:
    """Resolve a placeholder that closes at a count threshold."""
    status = PlaceholderStatus.CLOSED if have >= need else PlaceholderStatus.OPEN
    return status, f"{have} of {need} {noun}"


def _resolve_accuracy(state: LiveState) -> tuple[PlaceholderStatus, str]:
    return _counted(state.realised_sales, BACKTEST_ITEMS, "realised sales available to label")


def _resolve_lexicon(state: LiveState) -> tuple[PlaceholderStatus, str]:
    return _counted(state.realised_sales, RETUNE_TRADES, "realised trades available to retune on")


def _resolve_postage(state: LiveState) -> tuple[PlaceholderStatus, str]:
    status, detail = _counted(state.measured_shipments, MEASURED_SHIPMENTS, "shipments measured")
    return status, f"{detail}; in={state.ship_in_pence}p out={state.ship_out_pence}p from config"


def _resolve_condition(state: LiveState) -> tuple[PlaceholderStatus, str]:
    status, detail = _counted(state.realised_sales, RETUNE_TRADES, "realised trades")
    return status, f"{detail}; no condition discount is applied"


def _resolve_ledger(state: LiveState) -> tuple[PlaceholderStatus, str]:
    if state.settled_sales == 0:
        return PlaceholderStatus.OPEN, "0 settled sales -- any ledger figure is synthetic"
    return PlaceholderStatus.CLOSED, f"{state.settled_sales} settled sales on the books"


def _resolve_autobuy(state: LiveState) -> tuple[PlaceholderStatus, str]:
    return _counted(state.real_decisions, AUTOBUY_DECISIONS, "real decisions recorded")


def _resolve_reprice(state: LiveState) -> tuple[PlaceholderStatus, str]:
    version = state.reprice_version
    if state.reprice_provisional:
        return PlaceholderStatus.OPEN, f"{version} decay window is an unmeasured guess"
    return PlaceholderStatus.CLOSED, f"{version} fitted to realised days-to-sell"


def _resolve_contest(state: LiveState) -> tuple[PlaceholderStatus, str]:
    version = state.contest_version
    if state.contest_provisional:
        return PlaceholderStatus.OPEN, f"{version} thresholds are unmeasured guesses"
    return PlaceholderStatus.CLOSED, f"{version} fitted to realised win rate"


_RESOLVERS: dict[str, Callable[[LiveState], tuple[PlaceholderStatus, str]]] = {
    "P1": _resolve_fees,
    "P2": _resolve_velocity,
    "P3": _resolve_accuracy,
    "P4": _resolve_lexicon,
    "P5": _resolve_postage,
    "P6": _resolve_condition,
    "P7": _resolve_ledger,
    "P8": _resolve_autobuy,
    "P9": _resolve_contest,
    "P10": _resolve_reprice,
}


def resolve(state: LiveState) -> tuple[PlaceholderState, ...]:
    """Resolve every registered placeholder against live state. Pure.

    Raises if a placeholder has no resolver, rather than skipping it. A registered
    gap that silently never gets checked is exactly the failure this module exists
    to prevent, so it fails loudly at the only moment anyone would notice.
    """
    resolved: list[PlaceholderState] = []
    for placeholder in REGISTER:
        resolver = _RESOLVERS.get(placeholder.id)
        if resolver is None:
            msg = f"{placeholder.id} is registered but has no resolver"
            raise RuntimeError(msg)
        status, evidence = resolver(state)
        resolved.append(PlaceholderState(placeholder, status, evidence))
    return tuple(resolved)


def _fee_table_flags(fee_dir: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split the fee tables on disk into provisional and verified.

    Reads the YAML directly rather than going through `load_fee_table`, because a
    table too malformed to validate is itself an unverified assumption and should be
    reported as provisional rather than crash the report.
    """
    provisional: list[str] = []
    verified: list[str] = []
    if not fee_dir.is_dir():
        return (), ()
    for path in sorted(fee_dir.glob("*.yaml")):
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        flag = parsed.get("provisional", True) if isinstance(parsed, dict) else True
        (provisional if flag else verified).append(path.stem)
    return tuple(provisional), tuple(verified)


def gather(session: Session, fee_dir: Path) -> LiveState:
    """Collect the live facts. The only function here that touches I/O."""
    provisional, verified = _fee_table_flags(fee_dir)

    sold_total = session.scalar(select(func.count()).select_from(SoldObs)) or 0
    sold_with_days = (
        session.scalar(
            select(func.count()).select_from(SoldObs).where(SoldObs.days_to_sell.is_not(None))
        )
        or 0
    )
    real_decisions = (
        session.scalar(
            select(func.count()).select_from(Decisions).where(Decisions.mode != "dryrun")
        )
        or 0
    )
    realised = (
        session.scalar(
            select(func.count()).select_from(Inventory).where(Inventory.sold_at.is_not(None))
        )
        or 0
    )
    settled = (
        session.scalar(
            select(func.count())
            .select_from(Inventory)
            .where(Inventory.actual_fees_pence.is_not(None))
        )
        or 0
    )
    shipments = (
        session.scalar(
            select(func.count())
            .select_from(Inventory)
            .where(Inventory.actual_ship_pence.is_not(None))
        )
        or 0
    )
    versions = session.execute(
        select(Opportunities.fee_table_version, func.count())
        .group_by(Opportunities.fee_table_version)
        .order_by(Opportunities.fee_table_version)
    ).all()

    return LiveState(
        provisional_fee_tables=provisional,
        verified_fee_tables=verified,
        sold_obs_total=sold_total,
        sold_obs_with_days=sold_with_days,
        real_decisions=real_decisions,
        realised_sales=realised,
        settled_sales=settled,
        measured_shipments=shipments,
        velocity_policy=DEFAULT_SCAN_SETTINGS.policy,
        ship_in_pence=DEFAULT_SCAN_SETTINGS.ship_in_pence,
        ship_out_pence=DEFAULT_SCAN_SETTINGS.ship_out_pence,
        contest_provisional=DEFAULT_CONTEST_POLICY.provisional,
        contest_version=DEFAULT_CONTEST_POLICY.version,
        reprice_provisional=DEFAULT_REPRICE_POLICY.provisional,
        reprice_version=DEFAULT_REPRICE_POLICY.version,
        fee_versions_in_use=tuple((str(version), int(count)) for version, count in versions),
    )
