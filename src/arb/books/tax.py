"""UK tax output. A preparation aid, deliberately not a filing.

**This module does not do your tax return and is written so it cannot pretend to.**
It totals what the ledger already knows, applies two rules that are stable and
legislated, and refuses to go further. Everything it produces is a figure to hand to
an accountant or to check against GOV.UK, not a number to copy into a form.

Three things it does *not* do, each on purpose:

*No SA103 box numbers.* Box numbering changes between tax years and between the short
and full forms. A wrong box number produces a return that is confidently incorrect,
which is the exact failure mode this codebase spends its effort avoiding — and here
the consequence is a compliance one rather than a lost trade. The `sa103_category`
column exists on `inventory` for when a mapping has been confirmed against a specific
year's form; nothing here fills it in.

*No tax owed.* That needs your other income, your personal allowance, whether you are
a Scottish taxpayer, and your National Insurance position. None of that lives here.

*No advice on which method to use.* It computes both and reports which yields the
lower taxable profit, because that is arithmetic. Whether to claim it is not.

**Verified 20 August 2026 for the 2026/27 tax year:** the trading allowance is
£1,000, unchanged since 2017/18; it applies to *gross* income before expenses; and
the two methods are mutually exclusive — you claim the allowance or you deduct actual
expenses, never both. Re-verify against GOV.UK before relying on it: from 2027/28 a
simplified service is expected to change *reporting* obligations between £1,000 and
£3,000, which does not change the allowance but does change who must file.

**Cash basis is assumed, and it matters more here than it looks.** Cash basis is the
default for sole traders: income counts when received and costs when paid. For a
reseller that means **a single trade can straddle two tax years** — a jumper bought in
March and sold in May puts its cost in one year and its income in the next. Straddling
trades are counted and reported rather than silently netted, because under the
alternative (traditional accruals) they would be matched instead, and the difference
is real money in the wrong year.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple

from sqlalchemy import select

from arb.db import Inventory

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from arb.protocols import FeeModel

__all__ = [
    "TRADING_ALLOWANCE_PENCE",
    "TaxYear",
    "TaxYearSummary",
    "summarise_tax_year",
    "tax_year_of",
]

TRADING_ALLOWANCE_PENCE = 100_000
"""£1,000. Verified 20 Aug 2026 for 2026/27; unchanged since 2017/18. Applies to
GROSS trading income before any expenses, which is the part most often got wrong."""

TAX_YEAR_START_MONTH = 4
TAX_YEAR_START_DAY = 6
"""The UK tax year runs 6 April to 5 April. Not the calendar year, and not the
1 April used for corporation tax."""


class TaxYear(NamedTuple):
    """A UK tax year, identified by its starting calendar year."""

    start_year: int

    @property
    def label(self) -> str:
        return f"{self.start_year}/{str(self.start_year + 1)[-2:]}"

    @property
    def starts(self) -> datetime:
        return datetime(self.start_year, TAX_YEAR_START_MONTH, TAX_YEAR_START_DAY, tzinfo=UTC)

    @property
    def ends(self) -> datetime:
        return TaxYear(self.start_year + 1).starts

    @property
    def register_by(self) -> str:
        """Deadline to register for Self Assessment if the threshold was crossed:
        5 October following the *end* of the tax year.

        The tax year labelled 2026/27 ends 5 April 2027, so the deadline is October
        2027 -- `start_year + 1`, not `+ 2`. Verified against HMRC's own worked
        example: exceed in 2025/26 and register by 5 October 2026.
        """
        return f"5 October {self.start_year + 1}"

    def contains(self, moment: datetime) -> bool:
        return self.starts <= moment < self.ends


def tax_year_of(moment: datetime) -> TaxYear:
    """Which UK tax year a timestamp falls in.

    Rejects naive datetimes for the same reason `UtcDateTime` does: a timestamp an
    hour either side of midnight on 6 April lands in a different tax year depending
    on the timezone assumed, and nobody would notice until the figures were filed.
    """
    if moment.tzinfo is None:
        msg = "naive datetime rejected; pass an aware UTC datetime"
        raise ValueError(msg)
    moment = moment.astimezone(UTC)
    boundary = datetime(moment.year, TAX_YEAR_START_MONTH, TAX_YEAR_START_DAY, tzinfo=UTC)
    return TaxYear(moment.year if moment >= boundary else moment.year - 1)


class TaxYearSummary(NamedTuple):
    """Figures for one tax year. Every one of these is a starting point, not an answer."""

    tax_year: TaxYear
    gross_income_pence: int
    """Total received from sales in this year. The trading allowance is tested
    against this figure, before any costs."""

    allowable_costs_pence: int
    """Stock paid for, fees, and postage falling in this year under cash basis."""

    sales_count: int
    straddling_count: int
    """Trades whose cost fell in a different tax year from their income. Under cash
    basis that is correct and expected; under traditional accruals they would be
    matched instead. Reported because the difference is real money in the wrong year."""

    estimated_fees_count: int
    """Sales still costed from the provisional fee table rather than settlement.
    A tax figure resting on an invented fee rate is not a tax figure."""

    @property
    def profit_actual_expenses_pence(self) -> int:
        """Taxable profit if actual expenses are deducted."""
        return self.gross_income_pence - self.allowable_costs_pence

    @property
    def profit_trading_allowance_pence(self) -> int:
        """Taxable profit if the £1,000 allowance is claimed instead of expenses.

        Never negative: the allowance cannot create a loss. Deducting actual expenses
        can, which is one reason the choice is not purely arithmetic.
        """
        return max(self.gross_income_pence - TRADING_ALLOWANCE_PENCE, 0)

    @property
    def below_threshold(self) -> bool:
        """Gross income at or under the allowance. Full relief normally applies and
        registration is not usually required *for this income alone* -- other income
        or circumstances can still require a return."""
        return self.gross_income_pence <= TRADING_ALLOWANCE_PENCE

    @property
    def lower_method(self) -> str:
        """Which method yields the lower taxable profit. Arithmetic, not advice."""
        if self.profit_trading_allowance_pence < self.profit_actual_expenses_pence:
            return "trading_allowance"
        if self.profit_actual_expenses_pence < self.profit_trading_allowance_pence:
            return "actual_expenses"
        return "either"

    @property
    def figures_are_provisional(self) -> bool:
        """True while any sale in the year is costed from an unmeasured fee table."""
        return self.estimated_fees_count > 0


def summarise_tax_year(session: Session, tax_year: TaxYear, fee_model: FeeModel) -> TaxYearSummary:
    """Total one tax year on a cash basis. Income by `sold_at`, costs by when paid.

    Stock cost is attributed to `acquired_at` and fees and postage to `sold_at`,
    which is what cash basis does: you paid for the stock when you bought it, and the
    marketplace took its fee when the sale settled.
    """
    sold = session.scalars(select(Inventory).where(Inventory.sold_at.is_not(None))).all()

    gross = 0
    costs = 0
    sales = 0
    straddling = 0
    estimated = 0

    for row in sold:
        if row.sold_at is not None and tax_year.contains(row.sold_at):
            sales += 1
            gross += row.gross_pence or 0
            if row.actual_fees_pence is not None:
                costs += row.actual_fees_pence
            elif row.gross_pence is not None:
                costs += fee_model.fees_pence(row.gross_pence, row.qty)
                estimated += 1
            costs += row.actual_ship_pence or 0
            if not tax_year.contains(row.acquired_at):
                straddling += 1

    # Stock paid for in this year, whether or not it has sold yet. Cash basis
    # deducts the cost when it leaves your account, not when the item leaves.
    for row in session.scalars(select(Inventory)).all():
        if tax_year.contains(row.acquired_at):
            costs += row.cost_pence

    return TaxYearSummary(
        tax_year=tax_year,
        gross_income_pence=gross,
        allowable_costs_pence=costs,
        sales_count=sales,
        straddling_count=straddling,
        estimated_fees_count=estimated,
    )
