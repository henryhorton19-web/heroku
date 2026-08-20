"""Scanner and ranking. Purity is tested explicitly because monitors, AutoBuy dry-runs
and backtests all wrap this function without changing it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from arb.comps.fees import load_fee_table
from arb.models import Attributes, Listing, Valuation, Venue
from arb.sourcing.contest import ContestPolicy
from arb.sourcing.rank import ScoreContext, VelocityPolicy, capital_velocity
from arb.sourcing.scanner import Candidate, scan

T0 = datetime(2026, 8, 1, tzinfo=UTC)
FEE_TABLE = load_fee_table(
    Path(__file__).resolve().parent.parent / "src/arb/data/fees/ebay_uk.yaml"
)


def _ctx(
    *,
    ship_in_pence: int = 0,
    ship_out_pence: int = 300,
    qty: int = 1,
) -> ScoreContext:
    return ScoreContext(
        fee_model=FEE_TABLE,
        now=T0,
        ship_in_pence=ship_in_pence,
        ship_out_pence=ship_out_pence,
        qty=qty,
    )


def _listing(external_id: str, price: int, title: str = "nike air max 90") -> Listing:
    return Listing(
        venue=Venue.VINTED,
        external_id=external_id,
        price_pence=price,
        attrs=Attributes(brand_norm="nike", title_norm=title, size_norm="M"),
        first_seen=T0,
        last_seen=T0,
    )


def _val(p25: int, days: float | None) -> Valuation:
    return Valuation(
        est_p25_pence=p25,
        est_p60_pence=p25 + 500,
        comp_n=10,
        est_confidence=0.6,
        match_confidence=0.9,
        days_to_sell_p50=days,
    )


# ------------------------------------------------------------------ capital velocity


def test_velocity_is_none_without_a_clearing_estimate() -> None:
    assert capital_velocity(1000, 2000, None) is None


def test_fast_low_margin_beats_slow_high_margin() -> None:
    """The whole reason ranking is on velocity: capital is the binding constraint."""
    fast = capital_velocity(400, 1000, 5)
    slow = capital_velocity(1200, 1000, 90)
    assert fast is not None
    assert slow is not None
    assert fast > slow


def test_days_are_floored_at_one() -> None:
    """Without the floor a single same-day comp dominates every ranking."""
    assert capital_velocity(100, 1000, 0.1) == capital_velocity(100, 1000, 1.0)


# ------------------------------------------------------------------ unknown velocity


def test_unknown_velocity_is_excluded_by_default() -> None:
    """Precision over recall. An item whose clearing speed is unknown is not ranked."""
    cand = [Candidate(_listing("a", 1000), _val(5000, None))]
    outcome = scan(cand, _ctx())
    assert outcome.ranked == ()
    assert outcome.result.suppressed_unknown_velocity == 1


def test_suppression_is_counted_so_silence_is_visible() -> None:
    """An empty list because the market is quiet and an empty list because nothing
    could be timed are different situations."""
    cand = [Candidate(_listing(str(i), 1000), _val(5000, None)) for i in range(3)]
    assert scan(cand, _ctx()).result.suppressed_unknown_velocity == 3


def test_assume_default_policy_ranks_unknowns() -> None:
    cand = [Candidate(_listing("a", 1000), _val(5000, None))]
    outcome = scan(cand, _ctx(), policy=VelocityPolicy.ASSUME_DEFAULT)
    assert len(outcome.ranked) == 1
    assert outcome.ranked[0].opportunity.capital_velocity is not None


def test_known_velocity_ranks_under_either_policy() -> None:
    cand = [Candidate(_listing("a", 1000), _val(5000, 7))]
    for policy in VelocityPolicy:
        assert len(scan(cand, _ctx(), policy=policy).ranked) == 1


# ------------------------------------------------------------------ scoring


def test_scored_at_the_fast_sale_price_not_the_optimal_one() -> None:
    """Buying against p60 is how a plausible margin becomes a loss."""
    outcome = scan([Candidate(_listing("a", 1000), _val(5000, 7))], _ctx())
    opportunity = outcome.ranked[0].opportunity
    expected_fees = FEE_TABLE.fees_pence(5000, 1)
    assert opportunity.net_pence == 5000 - 1000 - expected_fees - 300


def test_every_opportunity_carries_the_fee_table_version() -> None:
    """No score may be unattributable to the assumptions that produced it."""
    outcome = scan([Candidate(_listing("a", 1000), _val(5000, 7))], _ctx())
    assert outcome.ranked[0].opportunity.fee_table_version == FEE_TABLE.version


def test_unprofitable_candidates_are_dropped_and_counted() -> None:
    outcome = scan([Candidate(_listing("a", 9000), _val(5000, 7))], _ctx())
    assert outcome.ranked == ()
    assert outcome.result.suppressed_below_floor == 1


def test_total_price_is_preferred_over_headline_price() -> None:
    """Vinted's headline price excludes buyer protection; total is what you pay."""
    listing = _listing("a", 1000).model_copy(update={"total_pence": 1400})
    outcome = scan([Candidate(listing, _val(5000, 7))], _ctx())
    cheap = scan([Candidate(_listing("b", 1000), _val(5000, 7))], _ctx())
    assert outcome.ranked[0].opportunity.net_pence < cheap.ranked[0].opportunity.net_pence


# ------------------------------------------------------------------ filtering


def test_quality_rejections_carry_a_skip_reason() -> None:
    cand = [Candidate(_listing("a", 1000, "nike hoodie with a hole"), _val(5000, 7))]
    outcome = scan(cand, _ctx())
    assert outcome.ranked == ()
    assert outcome.rejected_quality[0].reason.startswith("quality:")


def test_unpriceable_candidates_are_separated_from_quality_rejections() -> None:
    """Different problems: one needs more comps, the other is a bad listing."""
    cand = [
        Candidate(_listing("a", 1000, "nike hoodie ripped"), _val(5000, 7)),
        Candidate(_listing("b", 1000), None),
    ]
    outcome = scan(cand, _ctx())
    assert len(outcome.rejected_quality) == 1
    assert len(outcome.unpriceable) == 1


# ------------------------------------------------------------------ ordering & purity


def test_ranked_descending_by_velocity() -> None:
    cand = [
        Candidate(_listing("slow", 1000), _val(5000, 90)),
        Candidate(_listing("fast", 1000), _val(3000, 3)),
    ]
    ranked = scan(cand, _ctx()).ranked
    assert [c.listing.external_id for c in ranked] == ["fast", "slow"]
    velocities = [c.opportunity.capital_velocity or 0.0 for c in ranked]
    assert velocities == sorted(velocities, reverse=True)


def test_scan_is_pure() -> None:
    """No I/O, no clock read, no hidden state. This is what lets monitors, AutoBuy
    dry-runs and backtests wrap it rather than fork it."""
    cand = [Candidate(_listing("a", 1000), _val(5000, 7))]
    first = scan(cand, _ctx())
    second = scan(cand, _ctx())
    assert first == second


def test_scan_does_not_mutate_its_input() -> None:
    cand = [Candidate(_listing("a", 1000), _val(5000, 7))]
    snapshot = list(cand)
    scan(cand, _ctx())
    assert cand == snapshot


def test_empty_input_is_an_empty_result_not_an_error() -> None:
    outcome = scan([], _ctx())
    assert outcome.ranked == ()
    assert outcome.result.suppressed_unknown_velocity == 0


def test_zero_cost_listings_are_counted_as_anomalies_not_bargains() -> None:
    """A free item is a data error or a scam, not an infinite-return opportunity.
    Counted separately from unknown velocity so the diagnostics stay honest about
    why a buy list is empty."""
    outcome = scan([Candidate(_listing("a", 0), _val(5000, 7))], _ctx())
    assert outcome.ranked == ()
    assert outcome.result.suppressed_anomalous_cost == 1
    assert outcome.result.suppressed_unknown_velocity == 0


# ------------------------------------------------- contest gate (added with contest.py)


def _contested(external_id: str, price: int, favourites: int, views: int | None) -> Listing:
    return Listing(
        venue=Venue.VINTED,
        external_id=external_id,
        price_pence=price,
        attrs=Attributes(brand_norm="nike", title_norm="nike air max 90", size_norm="M"),
        favourites=favourites,
        views=views,
        first_seen=T0,
        last_seen=T0,
    )


def test_contested_listings_are_rejected_with_a_reason() -> None:
    """A heavily-saved listing is a race you usually lose, whatever the margin."""
    contested = _contested("a", 1000, favourites=400, views=500)
    outcome = scan([Candidate(contested, _val(5000, 7))], _ctx())
    assert outcome.ranked == ()
    assert len(outcome.rejected_contest) == 1
    assert outcome.rejected_contest[0].reason.startswith("contest:")


def test_uncontested_listings_still_rank() -> None:
    quiet = _contested("a", 1000, favourites=1, views=900)
    outcome = scan([Candidate(quiet, _val(5000, 7))], _ctx())
    assert len(outcome.ranked) == 1
    assert outcome.rejected_contest == ()


def test_listings_without_counters_are_not_rejected_as_contested() -> None:
    """Absent favourites is missing data, not evidence of demand. Rejecting here
    would drop the freshest listings, which are the least contested ones."""
    outcome = scan([Candidate(_listing("a", 1000), _val(5000, 7))], _ctx())
    assert len(outcome.ranked) == 1
    assert outcome.rejected_contest == ()


def test_quality_is_reported_ahead_of_contest() -> None:
    """A damaged, heavily-saved listing reports the damage. Both are true, but the
    quality reason is the one that tells you not to bother looking again."""
    listing = Listing(
        venue=Venue.VINTED,
        external_id="a",
        price_pence=1000,
        attrs=Attributes(brand_norm="nike", title_norm="nike air max 90 ripped", size_norm="M"),
        favourites=400,
        views=500,
        first_seen=T0,
        last_seen=T0,
    )
    outcome = scan([Candidate(listing, _val(5000, 7))], _ctx())
    assert len(outcome.rejected_quality) == 1
    assert outcome.rejected_contest == ()


def test_contest_gate_runs_before_the_valuation_check() -> None:
    """An unpriceable *and* contested listing is contested: it costs a comps request
    to discover it is unpriceable, and the point of the gate is not spending one."""
    contested = _contested("a", 1000, favourites=400, views=500)
    outcome = scan([Candidate(contested, None)], _ctx())
    assert outcome.unpriceable == ()
    assert len(outcome.rejected_contest) == 1


def test_contest_policy_is_overridable_per_scan() -> None:
    """Monitors and backtests hold their own policy rather than mutating a global."""
    candidates = [Candidate(_contested("a", 1000, favourites=10, views=5000), _val(5000, 7))]
    assert len(scan(candidates, _ctx()).ranked) == 1
    strict_ctx = _ctx()._replace(contest_policy=ContestPolicy(max_favourites=5))
    strict = scan(candidates, strict_ctx)
    assert strict.ranked == ()
    assert len(strict.rejected_contest) == 1
