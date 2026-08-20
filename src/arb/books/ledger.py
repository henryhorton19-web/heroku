"""The books: what is owned, what it cost, what came back, and what is stuck.

Two numbers decide whether this tool is working, and neither is margin. **Capital
deployed** is money currently sitting in stock rather than available to trade with,
and **ageing** is how long it has been sitting. A 40% margin realised in a week beats
a 120% margin realised in three months, which is the same thesis `capital_velocity`
encodes on the buy side, measured on the way out instead of predicted on the way in.

**Realised margin uses actual fees when they exist and predicted fees otherwise, and
says which.** This is the important design point. Mixing a settled sale and an
estimated one into a single "profit" figure produces a number that is neither, and
the error is invisible because both are plausible. `RealisedTrade.settled` carries
the distinction all the way to the report, and the totals are reported separately.

**Lifecycle is a column, not an inference.** The timestamps imply it, but an implied
state cannot be aged or counted. Stockly's states, adopted as-is: an item is
`scouted`, `in_transit`, `listed`, or `sold`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import func, select

from arb.db import Inventory

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.orm import Session

    from arb.protocols import FeeModel

__all__ = [
    "AGEING_DAYS",
    "CapitalPosition",
    "LifecycleState",
    "RealisedTrade",
    "capital_position",
    "ledger",
    "realised_trade",
]

AGEING_DAYS = 60
"""Past this, capital is stale. From ROADMAP W3: 'ageing over 60 days'. It is a
reporting threshold rather than an action -- nothing is written off automatically,
because a slow item and a dead item look identical to a query and different to a
person."""


class LifecycleState(StrEnum):
    """Stockly's states, adopted rather than invented.

    Deliberately linear and deliberately coarse. A richer state machine is easy to
    write and hard to keep honest, because every state nobody updates becomes a lie
    that queries then trust.
    """

    SCOUTED = "scouted"
    SNIPED = "sniped"
    IN_TRANSIT = "in_transit"
    ENHANCED = "enhanced"
    LISTED = "listed"
    SOLD = "sold"

    @property
    def order(self) -> int:
        return _STATE_ORDER[self]


_STATE_ORDER: dict[LifecycleState, int] = {
    LifecycleState.SCOUTED: 0,
    LifecycleState.SNIPED: 1,
    LifecycleState.IN_TRANSIT: 2,
    LifecycleState.ENHANCED: 3,
    LifecycleState.LISTED: 4,
    LifecycleState.SOLD: 5,
}
"""Explicit order. Enum definition order is load-bearing nowhere else in this
codebase, and a pipeline that silently reorders when someone adds a state would be
worse than one that fails to compile."""

FUNDS_CLEARED = "funds_cleared"
"""The seventh pipeline stage, and the only one that is **derived rather than
stored**. An item is sold when the buyer pays and cleared when settlement data
arrives, which is a fact about `actual_fees_pence`, not a state anyone sets. Storing
it would create a state that must be kept in sync with a column that already answers
the question."""


class RealisedTrade(NamedTuple):
    """One completed sale, as the books see it."""

    inventory_id: int
    cost_pence: int
    gross_pence: int
    fees_pence: int
    ship_pence: int
    net_pence: int
    settled: bool
    """True when `fees_pence` came from settlement data, False when it is the fee
    table's prediction. A settled trade and an estimated one must never be added
    into one 'profit' figure without this travelling alongside."""

    days_held: int | None

    @property
    def roi(self) -> float:
        deployed = self.cost_pence + self.ship_pence
        return self.net_pence / deployed if deployed > 0 else 0.0


class CapitalPosition(NamedTuple):
    """Where the money is right now."""

    deployed_pence: int
    """Cost basis of everything not yet sold. Money you cannot trade with."""

    recycled_pence: int
    """Gross returned by sold stock. Money that came back and can be redeployed."""

    aged_pence: int
    aged_count: int
    """Unsold stock held longer than `AGEING_DAYS`. The number that says whether the
    buy side's velocity estimates are any good."""

    by_state: tuple[tuple[LifecycleState, int, int], ...]
    """State, row count, cost basis. Turns 'outstanding tasks' into a query."""


def realised_trade(row: Inventory, fee_model: FeeModel) -> RealisedTrade | None:
    """Compute one trade's realised economics. `None` if it has not sold.

    Falls back to predicted fees when settlement has not arrived, and records that it
    did. Refusing outright would leave a sold item invisible in the books, which is
    worse -- but so is quietly presenting an estimate as a measurement.
    """
    if row.sold_at is None or row.gross_pence is None:
        return None

    settled = row.actual_fees_pence is not None
    fees = (
        row.actual_fees_pence
        if row.actual_fees_pence is not None
        else fee_model.fees_pence(row.gross_pence, row.qty)
    )
    ship = row.actual_ship_pence if row.actual_ship_pence is not None else 0
    # acquired_at is non-nullable, so no guard is needed and mypy rejects one as
    # unreachable. days_held is Optional only because a future source may lack it.
    days = (row.sold_at - row.acquired_at).days
    return RealisedTrade(
        inventory_id=row.id,
        cost_pence=row.cost_pence,
        gross_pence=row.gross_pence,
        fees_pence=fees,
        ship_pence=ship,
        net_pence=row.gross_pence - row.cost_pence - fees - ship,
        settled=settled,
        days_held=max(days, 0),
    )


def ledger(session: Session, fee_model: FeeModel) -> tuple[RealisedTrade, ...]:
    """Every completed trade, oldest first."""
    rows = session.scalars(
        select(Inventory).where(Inventory.sold_at.is_not(None)).order_by(Inventory.sold_at)
    ).all()
    trades = (realised_trade(row, fee_model) for row in rows)
    return tuple(trade for trade in trades if trade is not None)


def capital_position(session: Session, *, now: datetime) -> CapitalPosition:
    """Where the money is. `now` is passed in rather than read, so the report is
    reproducible and testable at an arbitrary date."""
    by_state: list[tuple[LifecycleState, int, int]] = []
    for state in LifecycleState:
        count, cost = session.execute(
            select(func.count(), func.coalesce(func.sum(Inventory.cost_pence), 0)).where(
                Inventory.state == state.value
            )
        ).one()
        by_state.append((state, int(count), int(cost)))

    deployed = (
        session.scalar(
            select(func.coalesce(func.sum(Inventory.cost_pence), 0)).where(
                Inventory.sold_at.is_(None)
            )
        )
        or 0
    )
    recycled = (
        session.scalar(
            select(func.coalesce(func.sum(Inventory.gross_pence), 0)).where(
                Inventory.sold_at.is_not(None)
            )
        )
        or 0
    )

    unsold = session.scalars(select(Inventory).where(Inventory.sold_at.is_(None))).all()
    aged = [row for row in unsold if (now - row.acquired_at).days > AGEING_DAYS]
    return CapitalPosition(
        deployed_pence=int(deployed),
        recycled_pence=int(recycled),
        aged_pence=sum(row.cost_pence for row in aged),
        aged_count=len(aged),
        by_state=tuple(by_state),
    )


def totals(trades: Sequence[RealisedTrade]) -> tuple[int, int, int]:
    """Net across settled trades, net across estimated trades, and the count settled.

    Split rather than summed, because a settled figure and an estimated one added
    together produce a number that is neither and looks like both.
    """
    settled = sum(t.net_pence for t in trades if t.settled)
    estimated = sum(t.net_pence for t in trades if not t.settled)
    return settled, estimated, sum(1 for t in trades if t.settled)
