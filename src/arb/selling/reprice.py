"""Repricing and offer ladders. One valuation engine, used from the other end.

`comps.valuation.value()` prices the buy side, the sell side and repricing. Nothing
here computes a price: every function takes a `Valuation` that `value()` produced,
and the ask is always a point *inside* the band it returned. That is not a style
preference — a sell side with its own pricing logic is a second opinion that will
drift from the first, and then two parts of the tool disagree about what an item is
worth while both look authoritative.

**The two percentiles are used from opposite ends, deliberately.** The buy side
scores at `est_p25`, the fast-sale price, so a plausible margin cannot quietly become
a loss. The sell side *lists* at `est_p60`, the optimal price, and decays toward
`est_p25` as the item ages. Both numbers come out of the same call; buying against
the optimistic one and selling against the pessimistic one is how you lose money at
both ends.

**The floor is `est_p25`, not break-even.** Two different questions get confused
here. `est_p25` is what the market will pay quickly — the ladder stops there because
below it you are not clearing faster, you are donating. Break-even is what *you* need
to not lose money, which depends on what you paid and has nothing to do with what the
item is worth. Break-even is computed and reported so a decision to sell at a loss is
visible and deliberate, but it never moves the ladder: an item bought badly is not
worth more because of it.

**Break-even is solved, not derived.** With one percentage and one fixed component
the algebra is trivial, but the fee table is a list of arbitrary components and will
grow more. A binary search over `fee_model.fees_pence` stays correct whatever the
table becomes, and reuses the fee logic instead of restating it.

The decay window is unmeasured — **P10** in the register.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from arb.models import Valuation
    from arb.protocols import FeeModel

__all__ = [
    "DEFAULT_REPRICE_POLICY",
    "OfferLadder",
    "RepriceContext",
    "RepriceDecision",
    "RepricePolicy",
    "ask_price",
    "break_even_pence",
    "offer_ladder",
    "reprice",
]


class RepricePolicy(NamedTuple):
    decay_days: float = 30.0
    """Days from listing to reach the fast-sale price. This single number *is* the
    capital-velocity thesis: too slow and capital sits, too fast and margin is given
    away. Unmeasured -- closed by realised days-to-sell against realised price."""

    min_change_pence: int = 100
    """Below this, leave the price alone. Churning by pennies resets search ranking
    and signals nothing to a watcher."""

    concession: Decimal = Decimal("0.05")
    """How far below the ask an offer is auto-accepted."""

    version: str = "reprice-v0"
    provisional: bool = True


DEFAULT_REPRICE_POLICY = RepricePolicy()


class RepriceContext(NamedTuple):
    """What repricing needs beyond the valuation and the elapsed time.

    Grouped rather than passed as four more arguments, for the same reason
    `ScoreContext` is: adding a cost component later should be a field, not a
    signature change at every call site.
    """

    fee_model: FeeModel
    cost_pence: int
    ship_out_pence: int = 320
    policy: RepricePolicy = DEFAULT_REPRICE_POLICY


class RepriceDecision(NamedTuple):
    current_pence: int
    suggested_pence: int
    floor_pence: int
    break_even_pence: int
    days_listed: float
    changed: bool
    reason: str

    @property
    def below_break_even(self) -> bool:
        """Whether the suggested ask loses money. Reported, never suppressed: at the
        stale end of the ladder this is the number the decision turns on."""
        return self.suggested_pence < self.break_even_pence


class OfferLadder(NamedTuple):
    ask_pence: int
    auto_accept_pence: int | None
    """`None` when no offer above break-even is worth taking -- the item cannot be
    sold profitably at this ask, so there is nothing to accept unsupervised."""

    auto_decline_pence: int
    break_even_pence: int


def ask_price(
    valuation: Valuation,
    *,
    days_listed: float,
    policy: RepricePolicy = DEFAULT_REPRICE_POLICY,
) -> int:
    """The ask for an item that has been listed `days_listed`. Pure.

    Linear decay from `est_p60` to `est_p25`. Linear rather than curved because a
    curve is a claim about how demand decays over time and nobody here has measured
    that; a straight line is the honest shape for an unmeasured relationship.
    """
    top = valuation.est_p60_pence
    floor = valuation.est_p25_pence
    if top <= floor:
        return floor
    elapsed = max(days_listed, 0.0)
    fraction = 1.0 if elapsed >= policy.decay_days else elapsed / policy.decay_days
    return max(floor, top - round((top - floor) * fraction))


def break_even_pence(fee_model: FeeModel, *, cost_pence: int, ship_out_pence: int) -> int | None:
    """Smallest sale price that covers cost, postage and the fees on itself.

    Binary searched over the fee model rather than solved algebraically, so it holds
    for any component structure the table grows. `None` when no price clears -- a fee
    schedule at or above 100% has no break-even, and returning a large number would
    look like an answer.
    """
    outlay = cost_pence + ship_out_pence
    low, high = 0, max(outlay * 4, 1000)
    for _ in range(64):
        if high - fee_model.fees_pence(high) - outlay >= 0:
            break
        high *= 2
    else:
        return None
    if high - fee_model.fees_pence(high) - outlay < 0:
        return None
    while low < high:
        mid = (low + high) // 2
        if mid - fee_model.fees_pence(mid) - outlay >= 0:
            high = mid
        else:
            low = mid + 1
    return low


def reprice(
    valuation: Valuation,
    ctx: RepriceContext,
    *,
    current_pence: int,
    days_listed: float,
) -> RepriceDecision:
    """Decide whether and how to move an ask. Pure; no clock read, no I/O.

    Always returns a decision, including when the answer is to do nothing. A caller
    scheduling this over a hundred listings needs to distinguish "held at £40 because
    it is already right" from "not considered".
    """
    target = ask_price(valuation, days_listed=days_listed, policy=ctx.policy)
    break_even = break_even_pence(
        ctx.fee_model, cost_pence=ctx.cost_pence, ship_out_pence=ctx.ship_out_pence
    )
    delta = abs(target - current_pence)
    changed = delta >= ctx.policy.min_change_pence
    reason = (
        f"decay:{days_listed:.0f}d"
        if changed
        else f"below_min_change:{delta}p<{ctx.policy.min_change_pence}p"
    )
    return RepriceDecision(
        current_pence=current_pence,
        suggested_pence=target if changed else current_pence,
        floor_pence=valuation.est_p25_pence,
        break_even_pence=break_even if break_even is not None else 0,
        days_listed=days_listed,
        changed=changed,
        reason=reason,
    )


def offer_ladder(valuation: Valuation, ctx: RepriceContext, *, ask_pence: int) -> OfferLadder:
    """Auto-accept and auto-decline thresholds for Best Offer.

    **Auto-accept never goes below break-even.** It is the one setting that cannot be
    supervised — it fires while you are asleep — so it is clamped rather than merely
    warned about. If break-even is above the ask, `auto_accept_pence` is `None`:
    there is no offer worth taking unattended on an item that cannot be sold at a
    profit, and inventing a band would be worse than refusing one.

    Auto-decline sits at the valuation floor. Below `est_p25` an offer is not a fast
    sale, it is someone trying their luck.

    Note the feedback loop: a Best Offer sale reports the *listed* price to eBay's
    completed listings, so our own accepted offers re-enter the comp set marked
    `price_is_upper_bound`. `value()` excludes those by default, which is what keeps
    this from inflating our own future valuations.
    """
    break_even = break_even_pence(
        ctx.fee_model, cost_pence=ctx.cost_pence, ship_out_pence=ctx.ship_out_pence
    )
    floor = break_even if break_even is not None else ask_pence
    concession = int(Decimal(ask_pence) * ctx.policy.concession)
    accept: int | None = max(ask_pence - concession, floor)
    if accept is not None and accept > ask_pence:
        accept = None
    return OfferLadder(
        ask_pence=ask_pence,
        auto_accept_pence=accept,
        auto_decline_pence=min(valuation.est_p25_pence, ask_pence) - 1,
        break_even_pence=floor,
    )
