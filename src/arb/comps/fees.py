"""Fee tables: versioned YAML, content-hashed, provisional until settlement data lands.

Two things make this worth its own module rather than a constant.

**The version is a content hash of the file.** It is stamped onto every opportunity
as `fee_table_version`, so when a rate turns out to be wrong you can find exactly
which historical scores it poisoned instead of guessing.

**`provisional` is a first-class field.** Every table ships marked provisional and
stays that way until `arb reconcile` rewrites it from Sell Fulfillment settlement
data. A number nobody has checked against a bank statement should not look the same
as one that has.

Rounding is per component, half-up, then summed -- matching how marketplaces itemise
fees. Rounding once on the total drifts by a penny or two per trade, which is
invisible per trade and not invisible across a few hundred.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from arb.money import percentage_of_pence

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["FeeComponent", "FeeKind", "FeeScope", "FeeTable", "load_fee_table"]


class FeeKind(StrEnum):
    PERCENTAGE = "percentage"
    FIXED = "fixed"


class FeeScope(StrEnum):
    """Whether a component is charged once per order or once per item.

    This is the whole bundle seam. Wholesale economics is `qty=N` against a table
    whose per-order components do not multiply, and nothing else needs to change.
    """

    ORDER = "order"
    ITEM = "item"


class FeeComponent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    kind: FeeKind
    scope: FeeScope = FeeScope.ORDER
    rate: Decimal | None = None
    amount_pence: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _kind_matches_payload(self) -> Self:
        if self.kind is FeeKind.PERCENTAGE:
            if self.rate is None:
                msg = f"{self.name}: percentage component needs a rate"
                raise ValueError(msg)
            if not (Decimal(0) <= self.rate <= Decimal(1)):
                msg = f"{self.name}: rate {self.rate} outside 0..1 -- express 12.5% as 0.125"
                raise ValueError(msg)
        elif self.amount_pence is None:
            msg = f"{self.name}: fixed component needs amount_pence"
            raise ValueError(msg)
        return self

    def charge_pence(self, price_pence: int, qty: int) -> int:
        """Charge for one order line. Narrowing is re-done here rather than asserted:
        the validator already guarantees it, but a raise carries the component name
        into the traceback and an assert would vanish under -O."""
        multiplier = qty if self.scope is FeeScope.ITEM else 1
        if self.kind is FeeKind.PERCENTAGE:
            rate = self.rate
            if rate is None:
                msg = f"{self.name}: percentage component has no rate"
                raise ValueError(msg)
            return percentage_of_pence(price_pence, rate) * multiplier
        amount = self.amount_pence
        if amount is None:
            msg = f"{self.name}: fixed component has no amount_pence"
            raise ValueError(msg)
        return amount * multiplier


class FeeTable(BaseModel):
    """A venue's cost of selling. Satisfies the `FeeModel` protocol structurally."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    venue: str = Field(min_length=1)
    currency: str = "GBP"
    provisional: bool = True
    source: str = ""
    verified_at: str | None = None
    components: tuple[FeeComponent, ...] = Field(min_length=1)
    content_hash: str = Field(min_length=8)

    @property
    def version(self) -> str:
        """`venue@hash`. Stamped onto every opportunity; never reconstructable later."""
        return f"{self.venue}@{self.content_hash[:12]}"

    def fees_pence(self, price_pence: int, qty: int = 1) -> int:
        """Total selling cost. `price_pence` is the per-item price.

        Percentage components are charged on the per-item price and multiplied by
        `qty` when item-scoped, rather than charged on a combined total, because
        that is how a multi-quantity listing is actually billed.
        """
        if price_pence < 0:
            msg = "price_pence cannot be negative"
            raise ValueError(msg)
        if qty < 1:
            msg = "qty must be at least 1"
            raise ValueError(msg)
        return sum(c.charge_pence(price_pence, qty) for c in self.components)


def load_fee_table(path: Path) -> FeeTable:
    """Load and content-hash a fee table.

    The hash covers the raw bytes, not the parsed model, so any edit at all -- a
    changed rate, a reordered component, an added comment -- produces a new version.
    Over-sensitivity is the right failure direction: a spurious version bump costs
    nothing, a missed one means two different fee assumptions share an audit trail.
    """
    raw = path.read_bytes()
    content_hash = hashlib.sha256(raw).hexdigest()
    parsed = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        msg = f"{path}: fee table must be a YAML mapping"
        raise TypeError(msg)
    parsed["content_hash"] = content_hash
    return FeeTable.model_validate(parsed)
