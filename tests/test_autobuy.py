"""AutoBuy rails.

Every test here is a way of checking the same property: **the rails fail closed.** A
missing fact, an expired token, an unreadable state — all refuse. The cost of a
wrongly-refused purchase is a missed item, which costs nothing. The cost of a
wrongly-allowed one is money gone at machine speed while nobody is watching.

The most important test in the file is `test_purchases_are_refused_while_fees_are_
unmeasured`. It is the roadmap's one hard ordering rule, enforced rather than
documented.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import given
from hypothesis import strategies as st

from arb.autobuy import (
    Authorisation,
    RailBreach,
    RailContext,
    SpendCaps,
    authorise,
    idempotency_key,
)
from arb.models import Attributes, Listing, Opportunity, Valuation, Venue
from arb.sourcing.rank import ScoredCandidate

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
ARMED = NOW + timedelta(hours=1)


def _candidate(
    external_id: str = "a",
    *,
    cost: int = 1_000,
    confidence: float = 0.9,
    velocity: float | None = 0.05,
) -> ScoredCandidate:
    listing = Listing(
        venue=Venue.VINTED,
        external_id=external_id,
        price_pence=cost,
        attrs=Attributes(brand_norm="nike", title_norm="nike air max 90"),
        first_seen=NOW,
        last_seen=NOW,
    )
    return ScoredCandidate(
        listing,
        Opportunity(
            listing_id=0,
            valuation=Valuation(
                est_p25_pence=4_000,
                est_p60_pence=5_000,
                comp_n=8,
                est_confidence=confidence,
                match_confidence=confidence,
            ),
            fees_pence=600,
            ship_in_pence=0,
            ship_out_pence=320,
            net_pence=2_080,
            roi=2.0,
            capital_velocity=velocity,
            fee_table_version="ebay_uk@test",
            scored_at=NOW,
        ),
    )


ARMED_CTX = RailContext(
    fees_measured=True,
    armed_until=ARMED,
    kill_switch=False,
    spent_today_pence=0,
    outstanding_pence=0,
    already_attempted=frozenset(),
)
"""A context in which every rail is satisfied. Tests break one at a time via
`._replace`, which mypy checks field-by-field -- unlike a `**overrides` helper, which
needs a suppression the guard rightly bans."""


# ---------------------------------------------------------------- the hard rule


def test_purchases_are_refused_while_fees_are_unmeasured() -> None:
    """The roadmap's one hard ordering rule, enforced rather than documented.
    Automated spending against invented fee rates repeats a mistake at machine speed
    and is one `arb reconcile-fees` run away from being fixed."""
    result = authorise([_candidate()], ARMED_CTX._replace(fees_measured=False), now=NOW)
    assert result.approved == ()
    assert result.halted is RailBreach.FEES_UNMEASURED


def test_a_halt_is_distinguishable_from_nothing_qualifying() -> None:
    """An empty approved list means two very different things, and an operator needs
    to know which."""
    disarmed = authorise([_candidate()], ARMED_CTX._replace(armed_until=None), now=NOW)
    quiet = authorise([], ARMED_CTX, now=NOW)
    assert disarmed.is_halted
    assert not quiet.is_halted


# ---------------------------------------------------------------- the dead man


def test_an_expired_arm_refuses() -> None:
    """`armed_until` is an expiry, not a flag. Walking away from the machine stops
    AutoBuy rather than leaving it spending unattended."""
    result = authorise(
        [_candidate()], ARMED_CTX._replace(armed_until=NOW - timedelta(seconds=1)), now=NOW
    )
    assert result.halted is RailBreach.NOT_ARMED


def test_never_armed_refuses() -> None:
    assert (
        authorise([_candidate()], ARMED_CTX._replace(armed_until=None), now=NOW).halted
        is RailBreach.NOT_ARMED
    )


def test_the_kill_switch_beats_everything() -> None:
    result = authorise([_candidate()], ARMED_CTX._replace(kill_switch=True), now=NOW)
    assert result.halted is RailBreach.KILL_SWITCH


def test_an_armed_run_with_measured_fees_proceeds() -> None:
    result = authorise([_candidate()], ARMED_CTX, now=NOW)
    assert len(result.approved) == 1
    assert not result.is_halted


# ---------------------------------------------------------------- spend caps


def test_the_run_cap_bounds_one_batch() -> None:
    caps = SpendCaps(per_run_pence=2_500)
    candidates = [_candidate(str(i), cost=1_000) for i in range(5)]
    result = authorise(candidates, ARMED_CTX._replace(caps=caps), now=NOW)
    assert result.approved_spend_pence <= 2_500
    assert len(result.approved) == 2


def test_a_cheap_item_behind_an_expensive_one_is_still_reachable() -> None:
    """Refusing and continuing costs nothing; stopping at the first breach would
    silently reorder the buy list."""
    caps = SpendCaps(per_run_pence=2_000)
    result = authorise(
        [_candidate("big", cost=5_000), _candidate("small", cost=500)],
        ARMED_CTX._replace(caps=caps),
        now=NOW,
    )
    assert [c.listing.external_id for c in result.approved] == ["small"]


def test_the_daily_cap_counts_what_was_already_spent() -> None:
    """A per-run cap alone permits twenty runs an hour."""
    caps = SpendCaps(per_run_pence=10_000, per_day_pence=5_000)
    result = authorise(
        [_candidate(cost=2_000)], ARMED_CTX._replace(caps=caps, spent_today_pence=4_500), now=NOW
    )
    assert result.approved == ()


def test_an_exhausted_daily_cap_halts_rather_than_refusing_item_by_item() -> None:
    caps = SpendCaps(per_day_pence=5_000)
    result = authorise(
        [_candidate()], ARMED_CTX._replace(caps=caps, spent_today_pence=5_000), now=NOW
    )
    assert result.halted is RailBreach.DAILY_CAP


def test_the_outstanding_cap_bounds_capital_tied_up() -> None:
    caps = SpendCaps(outstanding_pence=10_000)
    result = authorise(
        [_candidate(cost=2_000)], ARMED_CTX._replace(caps=caps, outstanding_pence=9_000), now=NOW
    )
    assert result.approved == ()


def test_the_default_caps_are_small() -> None:
    """The right way to raise a cap is deliberately, after the dry-run has been
    checked -- not by discovering the default was already generous."""
    caps = SpendCaps()
    assert caps.per_run_pence <= 10_000
    assert caps.per_run_pence <= caps.per_day_pence <= caps.outstanding_pence


# ---------------------------------------------------------------- idempotency


def test_the_key_is_derived_not_random() -> None:
    """A random key per attempt makes every retry look like a new purchase, which is
    the failure this exists to prevent."""
    assert idempotency_key("vinted", "123") == idempotency_key("vinted", "123")


def test_venues_do_not_collide() -> None:
    assert idempotency_key("vinted", "123") != idempotency_key("ebay", "123")


def test_an_already_attempted_listing_is_refused() -> None:
    key = idempotency_key("vinted", "a")
    result = authorise(
        [_candidate("a")], ARMED_CTX._replace(already_attempted=frozenset({key})), now=NOW
    )
    assert result.approved == ()
    assert result.refused[0][1] is RailBreach.DUPLICATE


def test_the_same_listing_twice_in_one_batch_is_bought_once() -> None:
    """The database's unique index is the backstop, not the only guard -- a batch
    should not be relying on an IntegrityError to notice its own duplicate."""
    result = authorise([_candidate("a"), _candidate("a")], ARMED_CTX, now=NOW)
    assert len(result.approved) == 1
    assert result.refused[0][1] is RailBreach.DUPLICATE


# ---------------------------------------------------------------- quality of buy


def test_a_low_confidence_valuation_is_not_bought_automatically() -> None:
    """Fine for a human who can look at the photos. Not fine for a process that
    cannot."""
    result = authorise([_candidate(confidence=0.2)], ARMED_CTX, now=NOW)
    assert result.refused[0][1] is RailBreach.LOW_CONFIDENCE


def test_an_unknown_velocity_is_not_bought_automatically() -> None:
    """Ranking already excludes these; buying one would mean spending on an item
    whose clearing speed nobody has measured."""
    result = authorise([_candidate(velocity=None)], ARMED_CTX, now=NOW)
    assert result.refused[0][1] is RailBreach.NO_VELOCITY


# ---------------------------------------------------------------- properties


@given(
    costs=st.lists(st.integers(min_value=1, max_value=20_000), min_size=0, max_size=20),
    run_cap=st.integers(min_value=0, max_value=20_000),
    spent=st.integers(min_value=0, max_value=20_000),
)
def test_approved_spend_never_exceeds_any_cap(costs: list[int], run_cap: int, spent: int) -> None:
    """The load-bearing property. No ordering of candidates, no combination of prior
    spend, may authorise more than the caps allow."""
    caps = SpendCaps(per_run_pence=run_cap, per_day_pence=20_000, outstanding_pence=50_000)
    candidates = [_candidate(str(i), cost=c) for i, c in enumerate(costs)]
    result = authorise(candidates, ARMED_CTX._replace(caps=caps, spent_today_pence=spent), now=NOW)
    assert result.approved_spend_pence <= caps.per_run_pence
    assert spent + result.approved_spend_pence <= caps.per_day_pence


@given(armed_minutes=st.integers(min_value=-1000, max_value=1000))
def test_nothing_is_ever_approved_while_disarmed(armed_minutes: int) -> None:
    ctx = ARMED_CTX._replace(armed_until=NOW + timedelta(minutes=armed_minutes))
    result = authorise([_candidate()], ctx, now=NOW)
    if armed_minutes <= 0:
        assert result.approved == ()


def test_every_candidate_is_accounted_for() -> None:
    """Nothing may be silently dropped: an item that is neither approved nor refused
    is one nobody can explain afterwards."""
    candidates = [
        _candidate("a"),
        _candidate("b", confidence=0.1),
        _candidate("c", velocity=None),
    ]
    result: Authorisation = authorise(candidates, ARMED_CTX, now=NOW)
    assert len(result.approved) + len(result.refused) == len(candidates)
