"""Money. Integer pence in, integer pence out; a float never touches a price.

The comps API returns prices as decimal *strings* (`"899.99"`). Parsing those via
`float` introduces representation error that compounds across percentile maths and
fee arithmetic, and the resulting drift is invisible until a ledger disagrees with a
bank statement. Everything here goes through `Decimal`.

Currency is checked, never converted. A USD comp in a GBP valuation is a wrong
answer wearing the right units, and we would rather return nothing.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

__all__ = [
    "GBP",
    "CurrencyMismatchError",
    "parse_pence",
    "pence_to_decimal",
    "percentage_of_pence",
]

GBP = "GBP"

_STRIP = re.compile(r"[£$€\s,]")
_PENCE_PER_UNIT = Decimal(100)
_ONE_PENNY = Decimal("0.01")


class CurrencyMismatchError(ValueError):
    """Raised when an observation's currency is not the one being valued in."""


def parse_pence(raw: str | int | Decimal | None, *, currency: str | None = None) -> int | None:
    """Parse a money value into integer pence. Returns None for missing values.

    Accepts the decimal strings the comps API emits, plus ints already in pence-free
    major units is *not* supported -- an `int` here is read as major units, matching
    how `Decimal(5)` reads, so callers never guess.

    Rounds half-up at the penny. Banker's rounding is the Python default and would
    round `0.125` to `0.12`, which is not how invoices work.
    """
    if raw is None:
        return None
    if currency is not None and currency.upper() != GBP:
        msg = f"expected {GBP}, got {currency!r}"
        raise CurrencyMismatchError(msg)

    if isinstance(raw, str):
        cleaned = _STRIP.sub("", raw)
        if not cleaned:
            return None
        try:
            amount = Decimal(cleaned)
        except InvalidOperation:
            msg = f"not a decimal money value: {raw!r}"
            raise ValueError(msg) from None
    else:
        amount = Decimal(raw)

    if not amount.is_finite():
        msg = f"non-finite money value: {raw!r}"
        raise ValueError(msg)
    return int(amount.quantize(_ONE_PENNY, rounding=ROUND_HALF_UP) * _PENCE_PER_UNIT)


def pence_to_decimal(pence: int) -> Decimal:
    """Pence to major units, for display and for YAML round-tripping."""
    return (Decimal(pence) / _PENCE_PER_UNIT).quantize(_ONE_PENNY)


def percentage_of_pence(pence: int, rate: Decimal) -> int:
    """Apply a rate to a pence amount, rounding half-up to the nearest penny.

    Rounding happens per component rather than once on the total, because that is
    what marketplaces actually do when they itemise a fee -- and the difference is
    real money once it is multiplied across a few hundred trades.
    """
    return int((Decimal(pence) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))
