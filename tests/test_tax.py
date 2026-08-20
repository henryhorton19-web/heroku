"""UK tax output.

The tax year boundary is 6 April, not 1 January and not 1 April, and getting it
wrong moves income into the wrong year silently. The trading allowance applies to
*gross* income before expenses, which is the part most often got wrong. Both are
pinned here because the consequence of an error is a compliance one rather than a
lost trade.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from arb.books.tax import (
    TRADING_ALLOWANCE_PENCE,
    TaxYear,
    summarise_tax_year,
    tax_year_of,
)
from arb.comps.fees import load_fee_table
from arb.db import Inventory

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

FEES = load_fee_table(Path(__file__).resolve().parent.parent / "src/arb/data/fees/ebay_uk.yaml")
Y2026 = TaxYear(2026)


def _add(session: Session, row: Inventory) -> Inventory:
    session.add(row)
    session.flush()
    return row


def _trade(
    *,
    cost: int,
    gross: int | None,
    acquired: datetime,
    sold: datetime | None,
    fees: int | None = None,
) -> Inventory:
    return Inventory(
        cost_pence=cost,
        qty=1,
        state="sold" if sold else "listed",
        acquired_at=acquired,
        sold_at=sold,
        gross_pence=gross,
        actual_fees_pence=fees,
    )


# ---------------------------------------------------------------- the boundary


def test_the_tax_year_starts_on_6_april() -> None:
    assert tax_year_of(datetime(2026, 4, 6, tzinfo=UTC)) == TaxYear(2026)
    assert tax_year_of(datetime(2026, 4, 5, tzinfo=UTC)) == TaxYear(2025)


def test_january_belongs_to_the_previous_starting_year() -> None:
    """The trap: a January sale is in the tax year that began the previous April."""
    assert tax_year_of(datetime(2027, 1, 15, tzinfo=UTC)) == TaxYear(2026)


def test_the_label_reads_the_way_hmrc_writes_it() -> None:
    assert TaxYear(2026).label == "2026/27"


def test_the_registration_deadline_is_the_october_after_year_end() -> None:
    """Exceed the threshold in 2026/27 (ends 5 Apr 2027), register by 5 Oct 2027."""
    assert TaxYear(2026).register_by == "5 October 2027"


def test_a_naive_datetime_is_refused() -> None:
    """An hour either side of midnight on 6 April lands in a different tax year
    depending on the timezone assumed, and nobody notices until it is filed."""
    naive = datetime(2026, 4, 6, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError, match="naive"):
        tax_year_of(naive)


def test_a_non_utc_timestamp_is_converted_not_assumed() -> None:
    late = datetime(2026, 4, 6, 0, 30, tzinfo=timezone(timedelta(hours=2)))
    assert tax_year_of(late) == TaxYear(2025)


# ---------------------------------------------------------------- the allowance


def test_the_allowance_is_tested_against_gross_not_profit(session: Session) -> None:
    """The most commonly got-wrong rule. Gross £1,500 with £1,400 of costs is over
    the threshold, even though profit is only £100."""
    _add(
        session,
        _trade(
            cost=140_000,
            gross=150_000,
            acquired=datetime(2026, 5, 1, tzinfo=UTC),
            sold=datetime(2026, 6, 1, tzinfo=UTC),
            fees=0,
        ),
    )
    summary = summarise_tax_year(session, Y2026, FEES)
    assert summary.gross_income_pence > TRADING_ALLOWANCE_PENCE
    assert not summary.below_threshold


def test_gross_at_the_threshold_is_still_below(session: Session) -> None:
    _add(
        session,
        _trade(
            cost=10_000,
            gross=TRADING_ALLOWANCE_PENCE,
            acquired=datetime(2026, 5, 1, tzinfo=UTC),
            sold=datetime(2026, 6, 1, tzinfo=UTC),
            fees=0,
        ),
    )
    assert summarise_tax_year(session, Y2026, FEES).below_threshold


def test_the_allowance_cannot_create_a_loss(session: Session) -> None:
    """Deducting actual expenses can produce a loss; claiming the allowance cannot.
    That asymmetry is one reason the choice is not purely arithmetic."""
    _add(
        session,
        _trade(
            cost=50_000,
            gross=20_000,
            acquired=datetime(2026, 5, 1, tzinfo=UTC),
            sold=datetime(2026, 6, 1, tzinfo=UTC),
            fees=0,
        ),
    )
    summary = summarise_tax_year(session, Y2026, FEES)
    assert summary.profit_trading_allowance_pence == 0
    assert summary.profit_actual_expenses_pence < 0


def test_the_cheaper_method_is_identified(session: Session) -> None:
    """High gross, tiny costs: the allowance beats deducting expenses."""
    _add(
        session,
        _trade(
            cost=5_000,
            gross=300_000,
            acquired=datetime(2026, 5, 1, tzinfo=UTC),
            sold=datetime(2026, 6, 1, tzinfo=UTC),
            fees=0,
        ),
    )
    assert summarise_tax_year(session, Y2026, FEES).lower_method == "trading_allowance"


def test_heavy_expenses_beat_the_allowance(session: Session) -> None:
    _add(
        session,
        _trade(
            cost=200_000,
            gross=300_000,
            acquired=datetime(2026, 5, 1, tzinfo=UTC),
            sold=datetime(2026, 6, 1, tzinfo=UTC),
            fees=0,
        ),
    )
    assert summarise_tax_year(session, Y2026, FEES).lower_method == "actual_expenses"


# ---------------------------------------------------------------- cash basis


def test_income_falls_in_the_year_it_was_received(session: Session) -> None:
    _add(
        session,
        _trade(
            cost=1_000,
            gross=50_000,
            acquired=datetime(2026, 3, 1, tzinfo=UTC),
            sold=datetime(2026, 5, 1, tzinfo=UTC),
            fees=0,
        ),
    )
    assert summarise_tax_year(session, TaxYear(2025), FEES).gross_income_pence == 0
    assert summarise_tax_year(session, Y2026, FEES).gross_income_pence == 50_000


def test_a_straddling_trade_is_counted(session: Session) -> None:
    """Bought in March, sold in May: cost in one tax year, income in the next. Under
    cash basis that is correct; under accruals they would be matched."""
    _add(
        session,
        _trade(
            cost=1_000,
            gross=50_000,
            acquired=datetime(2026, 3, 1, tzinfo=UTC),
            sold=datetime(2026, 5, 1, tzinfo=UTC),
            fees=0,
        ),
    )
    assert summarise_tax_year(session, Y2026, FEES).straddling_count == 1


def test_unsold_stock_still_costs_in_the_year_it_was_paid_for(session: Session) -> None:
    """Cash basis deducts the cost when it leaves your account, not when the item
    leaves. Unsold stock is still a cost this year."""
    _add(
        session,
        _trade(cost=30_000, gross=None, acquired=datetime(2026, 6, 1, tzinfo=UTC), sold=None),
    )
    summary = summarise_tax_year(session, Y2026, FEES)
    assert summary.allowable_costs_pence == 30_000
    assert summary.gross_income_pence == 0


# ---------------------------------------------------------------- provenance


def test_estimated_fees_are_flagged_in_the_tax_figures(session: Session) -> None:
    """A tax figure resting on an invented fee rate is not a tax figure."""
    _add(
        session,
        _trade(
            cost=1_000,
            gross=50_000,
            acquired=datetime(2026, 5, 1, tzinfo=UTC),
            sold=datetime(2026, 6, 1, tzinfo=UTC),
        ),
    )
    summary = summarise_tax_year(session, Y2026, FEES)
    assert summary.estimated_fees_count == 1
    assert summary.figures_are_provisional


def test_settled_fees_are_not_flagged(session: Session) -> None:
    _add(
        session,
        _trade(
            cost=1_000,
            gross=50_000,
            acquired=datetime(2026, 5, 1, tzinfo=UTC),
            sold=datetime(2026, 6, 1, tzinfo=UTC),
            fees=6_000,
        ),
    )
    assert not summarise_tax_year(session, Y2026, FEES).figures_are_provisional


def test_an_empty_year_is_zeroes_not_an_error(session: Session) -> None:
    summary = summarise_tax_year(session, Y2026, FEES)
    assert summary.gross_income_pence == 0
    assert summary.below_threshold
