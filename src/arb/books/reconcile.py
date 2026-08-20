"""`arb reconcile`: replace invented fee rates with measured ones.

This is the mechanism that closes **P1**. The fee tables ship `provisional: true` and
every rate in them is a guess; this module reads what eBay actually charged and fits
the components to it.

**It corrects values, it does not infer structure.** The existing table declares that
`final_value_fee` is a percentage and `fixed_order_fee` is a flat amount; reconcile
re-measures those numbers and leaves the shape alone. Inferring the shape from data
is possible and a bad idea — with a handful of settlements you can fit almost any
structure, and the failure mode is a table that matches history perfectly and
predicts nothing.

**An unmapped fee type is the important output, not a nuisance.** eBay charges things
our table does not model at all — `AD_FEE` for Promoted Listings is the common one.
Silently ignoring a fee we are being charged means every margin is overstated by
exactly that amount, forever, and nothing downstream can detect it. Unmapped types
are reported loudly and counted in the realised total.

**It refuses below a floor.** `MIN_SETTLEMENTS` observations are required before any
correction is offered. Rewriting a fee table from two sales replaces a guess with a
different guess that now carries the authority of having been "measured". Same
posture as `value()` returning `None` below the comp floor: refusing beats answering
wrongly.

Fitting uses the **median**, not the mean. One promoted or discounted order should
not move the rate, and with a small sample the mean is exactly what it would move.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from statistics import median
from typing import TYPE_CHECKING, NamedTuple

from arb.comps.fees import FeeKind
from arb.money import pence_to_decimal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arb.comps.fees import FeeTable
    from arb.selling.finances import Settlement

__all__ = [
    "EBAY_FEE_TYPE_TO_COMPONENT",
    "MIN_SETTLEMENTS",
    "ComponentFit",
    "Reconciliation",
    "reconcile",
]

MIN_SETTLEMENTS = 10
"""Observations required before a correction is offered. Ten is not a statistical
threshold -- it is the point past which one odd order stops dominating the median.
ROADMAP section 9 triggers P1 on the first completed sale; that is when to *start*
collecting, not when to trust the fit."""

EBAY_FEE_TYPE_TO_COMPONENT: dict[str, str] = {
    "FINAL_VALUE_FEE": "final_value_fee",
    "REGULATORY_OPERATING_FEE": "regulatory_operating_fee",
    "FINAL_VALUE_FEE_FIXED_PER_ORDER": "fixed_order_fee",
}
"""eBay `feeType` to our component name. Deliberately not a fallback-to-slugify:
an unrecognised type must surface as unmodelled rather than quietly inventing a
component nobody has costed."""

_RATE_PLACES = Decimal("0.0001")


class ComponentFit(NamedTuple):
    """One fee component, as assumed and as measured."""

    name: str
    kind: FeeKind
    assumed: Decimal
    """Rate for a percentage component, pence for a fixed one."""
    measured: Decimal
    observations: int

    @property
    def drift(self) -> Decimal:
        return self.measured - self.assumed

    @property
    def materially_different(self) -> bool:
        """Whether the difference is worth acting on.

        A percentage component moving by a basis point is noise from penny rounding.
        A fixed component moving at all is a real change to a flat charge.
        """
        if self.kind is FeeKind.PERCENTAGE:
            return abs(self.drift) >= Decimal("0.0005")
        return abs(self.drift) >= Decimal(1)


class Reconciliation(NamedTuple):
    fits: tuple[ComponentFit, ...]
    unmodelled: tuple[tuple[str, int], ...]
    """eBay fee types we do not model, and how many settlements carried them. Every
    one of these means realised margin is lower than predicted by that amount."""

    settlements_used: int
    refunds_excluded: int
    predicted_total_pence: int
    realised_total_pence: int

    @property
    def total_drift_pence(self) -> int:
        """Positive means eBay charged more than the table predicted."""
        return self.realised_total_pence - self.predicted_total_pence

    @property
    def needs_rewrite(self) -> bool:
        return any(fit.materially_different for fit in self.fits) or bool(self.unmodelled)


def _measure(
    component_name: str, kind: FeeKind, settlements: Sequence[Settlement]
) -> Decimal | None:
    """Median realised value for one component, or None if never observed."""
    samples: list[Decimal] = []
    for settlement in settlements:
        for fee in settlement.fees:
            if EBAY_FEE_TYPE_TO_COMPONENT.get(fee.fee_type) != component_name:
                continue
            if kind is FeeKind.FIXED:
                samples.append(Decimal(fee.amount_pence))
            elif settlement.fee_basis_pence > 0:
                samples.append(Decimal(fee.amount_pence) / Decimal(settlement.fee_basis_pence))
    if not samples:
        return None
    value = median(samples)
    if kind is FeeKind.FIXED:
        return value.quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return value.quantize(_RATE_PLACES, rounding=ROUND_HALF_UP)


def _assumed(table: FeeTable, name: str) -> tuple[FeeKind, Decimal] | None:
    for component in table.components:
        if component.name != name:
            continue
        if component.kind is FeeKind.PERCENTAGE and component.rate is not None:
            return FeeKind.PERCENTAGE, component.rate
        if component.kind is FeeKind.FIXED and component.amount_pence is not None:
            return FeeKind.FIXED, Decimal(component.amount_pence)
    return None


def _unmodelled(settlements: Sequence[Settlement]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for settlement in settlements:
        for fee in settlement.fees:
            if fee.fee_type not in EBAY_FEE_TYPE_TO_COMPONENT:
                counts[fee.fee_type] = counts.get(fee.fee_type, 0) + 1
    return tuple(sorted(counts.items()))


def reconcile(
    settlements: Sequence[Settlement],
    table: FeeTable,
    *,
    min_settlements: int = MIN_SETTLEMENTS,
) -> Reconciliation | None:
    """Fit the table's components to settlement data. `None` below the floor.

    Refusing is a valid and expected output. Early on there will not be ten clean
    sales, and a correction offered from three of them would look like a measurement
    while being a guess with better manners.
    """
    sales = [s for s in settlements if s.is_sale]
    refunds = len(settlements) - len(sales)
    if len(sales) < min_settlements:
        return None

    fits: list[ComponentFit] = []
    for component in table.components:
        assumed = _assumed(table, component.name)
        if assumed is None:
            continue
        kind, assumed_value = assumed
        measured = _measure(component.name, kind, sales)
        if measured is None:
            continue
        observations = sum(
            1
            for s in sales
            for f in s.fees
            if EBAY_FEE_TYPE_TO_COMPONENT.get(f.fee_type) == component.name
        )
        fits.append(
            ComponentFit(
                name=component.name,
                kind=kind,
                assumed=assumed_value,
                measured=measured,
                observations=observations,
            )
        )

    predicted = sum(table.fees_pence(s.fee_basis_pence) for s in sales)
    realised = sum(s.total_fees_pence for s in sales)
    return Reconciliation(
        fits=tuple(fits),
        unmodelled=_unmodelled(sales),
        settlements_used=len(sales),
        refunds_excluded=refunds,
        predicted_total_pence=predicted,
        realised_total_pence=realised,
    )


def corrected_yaml(table: FeeTable, result: Reconciliation, *, verified_at: str) -> str:
    """Render a corrected fee table.

    Emitted as text rather than written, so the caller decides whether to overwrite.
    Rewriting the file changes its content hash, which changes `fee_table_version`,
    which is how every opportunity scored under the old assumption stays findable --
    so this is a change that must be deliberate.

    `provisional` becomes false: the numbers now come from settlement data. That flag
    is what `arb provenance` reads to close P1.
    """
    measured = {fit.name: fit for fit in result.fits}
    lines = [
        f"# {table.venue} selling fees, measured from settlement data.",
        "#",
        f"# Written by `arb reconcile` from {result.settlements_used} settled sales.",
        "# Editing this file changes content_hash, which changes fee_table_version.",
        "# Re-score any opportunity carrying an older version before comparing margins.",
        f"venue: {table.venue}",
        f"currency: {table.currency}",
        "provisional: false",
        # Quoted. An unquoted ISO date is parsed by YAML as a `date`, not a `str`,
        # and FeeTable.verified_at is typed `str | None` -- so an unquoted value
        # emits a table that cannot be read back. Caught by the round-trip test.
        f'verified_at: "{verified_at}"',
        f'source: "measured from {result.settlements_used} eBay settlement transactions"',
        "components:",
    ]
    for component in table.components:
        fit = measured.get(component.name)
        lines.append(f"  - name: {component.name}")
        lines.append(f"    kind: {component.kind.value}")
        lines.append(f"    scope: {component.scope.value}")
        if component.kind is FeeKind.PERCENTAGE:
            rate = fit.measured if fit else component.rate
            lines.append(f'    rate: "{rate}"')
        else:
            amount = int(fit.measured) if fit else component.amount_pence
            lines.append(f"    amount_pence: {amount}")
    for fee_type, count in result.unmodelled:
        lines.append(f"  # UNMODELLED: {fee_type} seen on {count} settlements -- not costed")
    return "\n".join(lines) + "\n"


def format_pence(pence: int) -> str:
    return f"{pence_to_decimal(pence)}"
