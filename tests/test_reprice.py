"""Repricing and offer ladders.

The invariant under test is the first one in AGENTS.md: **there is one valuation
engine.** The call that decides a jumper is worth £42 on the buy side is the same
call that prices the listing and later reprices it. So `reprice` consumes a
`Valuation` and cannot construct one — there is no path through this module that
produces a price the valuation engine did not already imply.

The property that encodes it: **an ask never leaves the [p25, p60] band.** If a
repricing rule could produce a price outside the band the valuation engine produced,
the sell side has grown its own pricing logic and the design is broken.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from arb.comps.fees import load_fee_table
from arb.models import Valuation
from arb.selling.reprice import (
    DEFAULT_REPRICE_POLICY,
    RepriceContext,
    RepricePolicy,
    ask_price,
    break_even_pence,
    offer_ladder,
    reprice,
)

FEES = load_fee_table(Path(__file__).resolve().parent.parent / "src/arb/data/fees/ebay_uk.yaml")


def _valuation(p25: int = 3000, p60: int = 4000) -> Valuation:
    return Valuation(
        est_p25_pence=p25,
        est_p60_pence=p60,
        comp_n=8,
        est_confidence=0.8,
        match_confidence=0.8,
    )


# ---------------------------------------------------------------- the ladder


def test_a_fresh_listing_asks_the_optimistic_price() -> None:
    """Buy side scores at p25 so a plausible margin cannot become a loss. Sell side
    *lists* at p60 -- the two percentiles were always meant to be used from opposite
    ends."""
    assert ask_price(_valuation(), days_listed=0.0) == 4000


def test_a_stale_listing_decays_to_the_fast_sale_price() -> None:
    policy = DEFAULT_REPRICE_POLICY
    assert ask_price(_valuation(), days_listed=policy.decay_days) == 3000


def test_the_decay_does_not_continue_past_the_floor() -> None:
    """Capital sitting for a year is a problem, but the answer is a write-off
    decision, not an ask that drifts to zero."""
    assert ask_price(_valuation(), days_listed=3650.0) == 3000


def test_the_ladder_is_monotone_in_time() -> None:
    prices = [ask_price(_valuation(), days_listed=float(d)) for d in range(0, 40, 5)]
    assert prices == sorted(prices, reverse=True)


def test_a_slower_decay_holds_the_price_longer() -> None:
    slow = RepricePolicy(decay_days=90.0)
    assert ask_price(_valuation(), days_listed=30.0, policy=slow) > ask_price(
        _valuation(), days_listed=30.0
    )


# ---------------------------------------------------------------- the invariant


@given(
    p25=st.integers(min_value=1, max_value=500_000),
    spread=st.integers(min_value=0, max_value=500_000),
    days=st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False),
    decay=st.floats(min_value=0.1, max_value=365.0, allow_nan=False),
)
def test_an_ask_never_leaves_the_valuation_band(
    p25: int, spread: int, days: float, decay: float
) -> None:
    """The load-bearing property. A price outside [p25, p60] would be a price the
    valuation engine never produced, which is the sell side having its own opinion."""
    valuation = _valuation(p25=p25, p60=p25 + spread)
    ask = ask_price(valuation, days_listed=days, policy=RepricePolicy(decay_days=decay))
    assert p25 <= ask <= p25 + spread


@given(days=st.floats(min_value=0.0, max_value=1_000.0, allow_nan=False))
def test_a_degenerate_band_always_prices_at_the_point(days: float) -> None:
    """p25 == p60 happens when comps agree. There is nothing to decay through."""
    assert ask_price(_valuation(p25=2500, p60=2500), days_listed=days) == 2500


# ---------------------------------------------------------------- break-even


def test_break_even_covers_cost_fees_and_postage() -> None:
    """Solved against the fee model rather than derived algebraically, so it stays
    correct whatever components the table grows."""
    price = break_even_pence(FEES, cost_pence=1000, ship_out_pence=320)
    assert price is not None
    assert price - FEES.fees_pence(price) - 1000 - 320 >= 0


def test_break_even_is_the_smallest_such_price() -> None:
    price = break_even_pence(FEES, cost_pence=1000, ship_out_pence=320)
    assert price is not None
    below = price - 1
    assert below - FEES.fees_pence(below) - 1000 - 320 < 0


def test_break_even_rises_with_cost() -> None:
    cheap = break_even_pence(FEES, cost_pence=500, ship_out_pence=320)
    dear = break_even_pence(FEES, cost_pence=5000, ship_out_pence=320)
    assert cheap is not None
    assert dear is not None
    assert dear > cheap


# ---------------------------------------------------------------- decisions


def test_a_small_move_is_not_worth_making() -> None:
    """Churning the price by pennies resets search ranking and signals nothing."""
    decision = reprice(
        _valuation(), RepriceContext(FEES, cost_pence=1000), current_pence=3990, days_listed=1.0
    )
    assert not decision.changed
    assert "below_min_change" in decision.reason


def test_a_material_move_is_made() -> None:
    decision = reprice(
        _valuation(), RepriceContext(FEES, cost_pence=1000), current_pence=4000, days_listed=30.0
    )
    assert decision.changed
    assert decision.suggested_pence == 3000


def test_a_decision_reports_the_break_even_alongside_the_suggestion() -> None:
    """Knowing the ask is below break-even is the whole point at the stale end of the
    ladder. Suppressing the suggestion instead would hide it."""
    decision = reprice(
        _valuation(), RepriceContext(FEES, cost_pence=3000), current_pence=4000, days_listed=30.0
    )
    assert decision.below_break_even
    assert decision.break_even_pence > decision.suggested_pence


def test_a_profitable_ask_is_not_flagged() -> None:
    decision = reprice(
        _valuation(), RepriceContext(FEES, cost_pence=500), current_pence=4000, days_listed=0.0
    )
    assert not decision.below_break_even


def test_repricing_is_stable_once_applied() -> None:
    """Applying a suggestion and re-running must not suggest another move, or the
    scheduler oscillates forever."""
    first = reprice(
        _valuation(), RepriceContext(FEES, cost_pence=1000), current_pence=4000, days_listed=15.0
    )
    second = reprice(
        _valuation(),
        RepriceContext(FEES, cost_pence=1000),
        current_pence=first.suggested_pence,
        days_listed=15.0,
    )
    assert not second.changed


# ---------------------------------------------------------------- offer ladders


def test_the_offer_ladder_brackets_the_ask() -> None:
    ladder = offer_ladder(_valuation(), RepriceContext(FEES, cost_pence=1000), ask_pence=4000)
    assert ladder.auto_accept_pence is not None
    assert ladder.auto_decline_pence < ladder.auto_accept_pence <= 4000


def test_auto_decline_never_sits_below_break_even() -> None:
    """An auto-accept that loses money is the one setting you cannot supervise: it
    fires while you are asleep."""
    ladder = offer_ladder(_valuation(), RepriceContext(FEES, cost_pence=3000), ask_pence=4000)
    assert ladder.auto_accept_pence is not None
    assert ladder.auto_accept_pence >= ladder.break_even_pence


def test_an_unprofitable_item_declines_everything_automatically() -> None:
    """When break-even is above the ask there is no offer worth auto-accepting, so
    the ladder refuses rather than inventing a band."""
    ladder = offer_ladder(_valuation(), RepriceContext(FEES, cost_pence=5000), ask_pence=1200)
    assert ladder.auto_accept_pence is None or ladder.auto_accept_pence >= ladder.break_even_pence


def test_a_wider_concession_accepts_lower_offers() -> None:
    generous = RepricePolicy(concession=Decimal("0.20"))
    tight = offer_ladder(_valuation(), RepriceContext(FEES, cost_pence=500), ask_pence=4000)
    wide = offer_ladder(
        _valuation(), RepriceContext(FEES, cost_pence=500, policy=generous), ask_pence=4000
    )
    assert tight.auto_accept_pence is not None
    assert wide.auto_accept_pence is not None
    assert wide.auto_accept_pence < tight.auto_accept_pence


# ---------------------------------------------------------------- policy


def test_the_policy_is_declared_provisional() -> None:
    """The decay window is a guess about how fast this market clears, and it is the
    capital-velocity thesis in a single number. Registered as P10."""
    assert DEFAULT_REPRICE_POLICY.provisional is True
    assert DEFAULT_REPRICE_POLICY.version
