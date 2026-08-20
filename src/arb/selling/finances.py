"""Settlement data: what eBay actually charged, as opposed to what we assumed.

This is the module that closes **P1**, the placeholder with the widest blast radius
in the register. Every margin, every buy decision and every ranking is computed from
`data/fees/*.yaml`, and every one of those numbers was invented. Until real fees
arrive from settlement, the whole book is downstream of a guess.

**A correction to the build plan: the fees are in `sell_finances`, not
`sell_fulfillment`.** The roadmap says "Sell Fulfillment client — settlement data",
and Fulfillment's `Order` does carry `totalMarketplaceFee` — but only as a lump sum.
Our fee table is componentised (final value fee, regulatory operating fee, fixed per
order), so a single total cannot correct it: any split of that total across three
components fits equally well. `sell_finances` `getTransactions` exposes
`orderLineItems[].marketplaceFees[]` with a `feeType` per fee, which is the level the
table is actually written at. Verified against `ebay_rest.api.sell_finances.models`.

Two judgements about what counts as an observation:

*Refunds are excluded.* A refunded order has fees credited back, sometimes partially
and sometimes on a later transaction, so its fee lines are not a clean reading of the
fee schedule. Including them would drag every fitted rate downward. They are counted
and reported rather than silently dropped, because a high refund rate is worth
knowing about for other reasons.

*Non-GBP is refused, not converted.* `parse_pence` raises on a currency mismatch, and
that is deliberate — a EUR settlement folded into a GBP fee fit is a wrong answer
wearing the right units.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from arb.money import parse_pence

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "SALE",
    "Settlement",
    "SettlementFee",
    "parse_transactions",
]

SALE = "SALE"
"""The only `transactionType` that is a clean observation of the fee schedule."""


class SettlementFee(NamedTuple):
    fee_type: str
    """eBay's own `feeType`, kept verbatim. Mapping it to one of our component names
    happens in `books/reconcile.py`, so an unfamiliar type survives parsing and can
    be reported rather than being dropped here where nobody would see it."""

    amount_pence: int


class Settlement(NamedTuple):
    """One line item's realised economics, as eBay settled it."""

    order_id: str
    line_item_id: str
    transaction_type: str
    fee_basis_pence: int
    """What eBay charged the percentage fees *on*. Not the item price: it includes
    postage and, on some transactions, tax. Fitting a rate against the item price
    instead would understate every percentage component."""

    fees: tuple[SettlementFee, ...]
    transaction_date: str | None = None

    @property
    def total_fees_pence(self) -> int:
        return sum(fee.amount_pence for fee in self.fees)

    @property
    def is_sale(self) -> bool:
        return self.transaction_type == SALE


def _amount_pence(raw: object) -> int | None:
    """Pull pence out of an eBay `Amount`. Raises on a non-GBP currency."""
    if not isinstance(raw, dict):
        return None
    value = raw.get("value")
    currency = raw.get("currency")
    if not isinstance(value, str):
        return None
    return parse_pence(value, currency=currency if isinstance(currency, str) else None)


def _fees(raw: object) -> tuple[SettlementFee, ...]:
    if not isinstance(raw, list):
        return ()
    parsed: list[SettlementFee] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        fee_type = entry.get("feeType")
        amount = _amount_pence(entry.get("amount"))
        if isinstance(fee_type, str) and fee_type.strip() and amount is not None:
            parsed.append(SettlementFee(fee_type=fee_type.strip(), amount_pence=amount))
    return tuple(parsed)


def _settlements_from(transaction: dict[str, object]) -> list[Settlement]:
    order_id = transaction.get("orderId")
    transaction_type = transaction.get("transactionType")
    date = transaction.get("transactionDate")
    line_items = transaction.get("orderLineItems")
    if not isinstance(order_id, str) or not isinstance(line_items, list):
        return []

    parsed: list[Settlement] = []
    for line in line_items:
        if not isinstance(line, dict):
            continue
        basis = _amount_pence(line.get("feeBasisAmount"))
        line_id = line.get("lineItemId")
        if basis is None or not isinstance(line_id, str):
            continue
        parsed.append(
            Settlement(
                order_id=order_id,
                line_item_id=line_id,
                transaction_type=transaction_type if isinstance(transaction_type, str) else "",
                fee_basis_pence=basis,
                fees=_fees(line.get("marketplaceFees")),
                transaction_date=date if isinstance(date, str) else None,
            )
        )
    return parsed


def parse_transactions(payload: object) -> tuple[Settlement, ...]:
    """Parse a `getTransactions` response. Pure.

    Every line item is returned, refunds included, and filtering is left to the
    caller. `reconcile` wants sales only, but the refund count is worth reporting and
    dropping them here would hide it.
    """
    if not isinstance(payload, dict):
        return ()
    transactions = payload.get("transactions")
    if not isinstance(transactions, list):
        return ()
    parsed: list[Settlement] = []
    for transaction in transactions:
        if isinstance(transaction, dict):
            parsed.extend(_settlements_from(transaction))
    return tuple(parsed)


def sales_only(settlements: Sequence[Settlement]) -> tuple[Settlement, ...]:
    """The clean observations. Refunds have fees credited back, sometimes on a later
    transaction, so they are not a reading of the fee schedule."""
    return tuple(s for s in settlements if s.is_sale)
