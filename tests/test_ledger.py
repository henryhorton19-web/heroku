"""The books.

The property these tests exist to protect: **a settled figure and an estimated one
are never silently added together.** Realised margin computed from settlement data
and realised margin computed from the provisional fee table are both plausible
numbers, and summing them produces a total that is neither — with no way to tell
afterwards which half was real.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from arb.books.ledger import (
    AGEING_DAYS,
    LifecycleState,
    capital_position,
    ledger,
    realised_trade,
    totals,
)
from arb.comps.fees import load_fee_table
from arb.db import Inventory

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FEES = load_fee_table(Path(__file__).resolve().parent.parent / "src/arb/data/fees/ebay_uk.yaml")
NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _add(session: Session, row: Inventory) -> Inventory:
    """Persist and flush. Inventory is constructed inline in each test: a shared
    builder wide enough for every case needs eight parameters, and at that point it
    obscures more than it saves."""
    session.add(row)
    session.flush()
    return row


def _stock(
    cost: int = 1200, *, days_ago: int = 5, state: LifecycleState = LifecycleState.LISTED
) -> Inventory:
    return Inventory(
        cost_pence=cost, qty=1, state=state.value, acquired_at=NOW - timedelta(days=days_ago)
    )


def _sold(row: Inventory, *, gross: int, days_ago: int = 1, fees: int | None = None) -> Inventory:
    row.gross_pence = gross
    row.actual_fees_pence = fees
    row.sold_at = NOW - timedelta(days=days_ago)
    row.state = LifecycleState.SOLD.value
    return row


# ---------------------------------------------------------------- lifecycle


def test_a_new_row_defaults_to_scouted(session: Session) -> None:
    row = Inventory(cost_pence=1000, acquired_at=NOW)
    session.add(row)
    session.flush()
    assert row.state == LifecycleState.SCOUTED.value


def test_states_are_countable_and_ageable(session: Session) -> None:
    """The whole reason state is a column. An implied state cannot be grouped."""
    _add(session, _stock(1000, state=LifecycleState.IN_TRANSIT))
    _add(session, _stock(2000, state=LifecycleState.IN_TRANSIT))
    _add(session, _stock(500, state=LifecycleState.LISTED))
    position = capital_position(session, now=NOW)
    by_state = {state: (count, cost) for state, count, cost in position.by_state}
    assert by_state[LifecycleState.IN_TRANSIT] == (2, 3000)
    assert by_state[LifecycleState.LISTED] == (1, 500)


# ---------------------------------------------------------------- realised margin


def test_settlement_fees_are_used_when_present(session: Session) -> None:
    row = _stock(1200)
    row.actual_ship_pence = 320
    _add(session, _sold(row, gross=4000, fees=500))
    trade = realised_trade(row, FEES)
    assert trade is not None
    assert trade.settled
    assert trade.fees_pence == 500
    assert trade.net_pence == 4000 - 1200 - 500 - 320


def test_predicted_fees_are_used_when_settlement_has_not_arrived(session: Session) -> None:
    """Refusing outright would leave a sold item invisible in the books. Presenting
    the estimate as a measurement would be worse, so the flag travels with it."""
    row = _add(session, _sold(_stock(1200), gross=4000))
    trade = realised_trade(row, FEES)
    assert trade is not None
    assert not trade.settled
    assert trade.fees_pence == FEES.fees_pence(4000)


def test_an_unsold_item_has_no_realised_trade(session: Session) -> None:
    assert realised_trade(_add(session, _stock()), FEES) is None


def test_a_sold_item_with_no_gross_has_no_realised_trade(session: Session) -> None:
    """Sold but unpriced is a half-written row, not a zero-revenue trade."""
    row = _stock()
    row.sold_at = NOW - timedelta(days=1)
    assert realised_trade(_add(session, row), FEES) is None


def test_days_held_is_never_negative(session: Session) -> None:
    row = _add(session, _sold(_stock(days_ago=1), gross=4000, days_ago=5))
    trade = realised_trade(row, FEES)
    assert trade is not None
    assert trade.days_held == 0


def test_a_loss_is_representable(session: Session) -> None:
    """Money is signed. A trade that lost money must not clamp to zero."""
    row = _add(session, _sold(_stock(4000), gross=1000, fees=200))
    trade = realised_trade(row, FEES)
    assert trade is not None
    assert trade.net_pence < 0
    assert trade.roi < 0


# ---------------------------------------------------------------- the split


def test_settled_and_estimated_totals_are_reported_separately(session: Session) -> None:
    """The load-bearing test. Adding a measured margin to an estimated one gives a
    number that is neither and looks like both."""
    _add(session, _sold(_stock(1000), gross=3000, fees=400))
    _add(session, _sold(_stock(1000), gross=3000, days_ago=2))
    settled, estimated, settled_count = totals(ledger(session, FEES))
    assert settled_count == 1
    assert settled != 0
    assert estimated != 0
    assert settled != estimated


def test_the_ledger_returns_only_completed_trades(session: Session) -> None:
    _add(session, _sold(_stock(), gross=3000))
    _add(session, _stock())
    assert len(ledger(session, FEES)) == 1


# ---------------------------------------------------------------- capital


def test_deployed_capital_is_what_is_not_yet_sold(session: Session) -> None:
    _add(session, _stock(1500))
    _add(session, _stock(2500))
    _add(session, _sold(_stock(1000), gross=3000))
    position = capital_position(session, now=NOW)
    assert position.deployed_pence == 4000


def test_recycled_capital_is_gross_returned(session: Session) -> None:
    _add(session, _sold(_stock(1000), gross=3000))
    assert capital_position(session, now=NOW).recycled_pence == 3000


def test_ageing_counts_only_unsold_stock(session: Session) -> None:
    """Old stock that sold is history. Old stock that has not is the problem."""
    _add(session, _stock(1000, days_ago=AGEING_DAYS + 10))
    _add(session, _sold(_stock(9000, days_ago=AGEING_DAYS + 10), gross=9000))
    position = capital_position(session, now=NOW)
    assert position.aged_count == 1
    assert position.aged_pence == 1000


def test_stock_inside_the_window_is_not_aged(session: Session) -> None:
    _add(session, _stock(1000, days_ago=AGEING_DAYS - 1))
    assert capital_position(session, now=NOW).aged_count == 0


def test_an_empty_book_reports_zeroes_not_an_error(session: Session) -> None:
    position = capital_position(session, now=NOW)
    assert position.deployed_pence == 0
    assert position.recycled_pence == 0
    assert ledger(session, FEES) == ()
