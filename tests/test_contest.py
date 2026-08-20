"""Contest density: how many other buyers are competing for the same listing.

The asymmetry these tests pin down is the opposite of the quality filter's. Quality
rejects on ambiguity, because an ambiguous description usually means a flawed item.
Contest *accepts* on ambiguity, because an absent favourite count means nothing at
all about demand -- and rejecting on missing data would silently drop the freshest
stock, which is exactly the stock worth having.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import given
from hypothesis import strategies as st

from arb.models import Attributes, Listing, Venue
from arb.sourcing.contest import (
    DEFAULT_CONTEST_POLICY,
    ContestPolicy,
    ContestReason,
    assess_contest,
)

SEEN = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _listing(favourites: int | None = None, views: int | None = None) -> Listing:
    return Listing(
        venue=Venue.VINTED,
        external_id="1",
        price_pence=1200,
        attrs=Attributes(brand_norm="nike", title_norm="nike hoodie"),
        favourites=favourites,
        views=views,
        first_seen=SEEN,
        last_seen=SEEN,
    )


# ---------------------------------------------------------------- missing data


def test_absent_favourites_is_not_a_rejection() -> None:
    """Unknown contest is not evidence of contest.

    Vinted omits these counters on some responses. Treating absence as a reject
    would drop stock for a reason that has nothing to do with the stock.
    """
    verdict = assess_contest(_listing(favourites=None, views=None))
    assert verdict.accepted
    assert verdict.reasons == ()
    assert verdict.skip_reason is None


def test_absent_views_still_allows_the_absolute_rule() -> None:
    """The save rate needs both counters; the absolute cap needs only one."""
    verdict = assess_contest(_listing(favourites=200, views=None))
    assert not verdict.accepted
    assert ContestReason.HIGH_FAVOURITES in verdict.reasons
    assert verdict.save_rate is None


def test_zero_views_does_not_divide_by_zero() -> None:
    """Ten favourites against zero views is inconsistent data, not infinite demand."""
    verdict = assess_contest(_listing(favourites=10, views=0))
    assert verdict.save_rate is None


# ---------------------------------------------------------------- absolute rule


def test_high_absolute_favourites_is_contested() -> None:
    policy = DEFAULT_CONTEST_POLICY
    verdict = assess_contest(_listing(favourites=policy.max_favourites, views=100_000))
    assert not verdict.accepted
    assert verdict.reasons == (ContestReason.HIGH_FAVOURITES,)


def test_just_below_the_absolute_cap_passes() -> None:
    policy = DEFAULT_CONTEST_POLICY
    verdict = assess_contest(_listing(favourites=policy.max_favourites - 1, views=100_000))
    assert verdict.accepted


# ---------------------------------------------------------------- save rate


def test_high_save_rate_is_contested_even_with_few_favourites() -> None:
    """Eight saves from twenty views is a hot item, whatever the absolute count.

    This is the rule that makes the filter age-invariant. Both counters accumulate
    over the same unknown window, so their ratio needs no listing-creation date --
    which is fortunate, because Vinted's search response does not carry one.
    """
    verdict = assess_contest(_listing(favourites=8, views=20))
    assert not verdict.accepted
    assert ContestReason.HIGH_SAVE_RATE in verdict.reasons
    assert verdict.save_rate is not None


def test_save_rate_needs_a_volume_floor() -> None:
    """One save from one view is 100% and means nothing."""
    verdict = assess_contest(_listing(favourites=1, views=1))
    assert verdict.accepted
    assert verdict.save_rate is None


def test_many_views_few_saves_passes() -> None:
    """People looked and did not want it. That is the opposite of contest."""
    verdict = assess_contest(_listing(favourites=6, views=2_000))
    assert verdict.accepted


def test_both_rules_can_fire_together() -> None:
    verdict = assess_contest(_listing(favourites=400, views=500))
    assert not verdict.accepted
    assert set(verdict.reasons) == {ContestReason.HIGH_FAVOURITES, ContestReason.HIGH_SAVE_RATE}


# ---------------------------------------------------------------- skip reasons


def test_skip_reason_is_stable_and_sorted() -> None:
    """AutoBuy's dry-run diffs against these strings, so ordering cannot be
    incidental to dict iteration."""
    verdict = assess_contest(_listing(favourites=400, views=500))
    assert verdict.skip_reason == "contest:high_favourites,high_save_rate"


def test_accepted_verdict_has_no_skip_reason() -> None:
    assert assess_contest(_listing(favourites=0, views=10)).skip_reason is None


# ---------------------------------------------------------------- policy


def test_policy_is_declared_provisional() -> None:
    """The thresholds are guesses. If this ever fails, someone has claimed they were
    measured -- check that they actually were, against realised win rates."""
    assert DEFAULT_CONTEST_POLICY.provisional is True
    assert DEFAULT_CONTEST_POLICY.version


def test_a_stricter_policy_rejects_more() -> None:
    listing = _listing(favourites=10, views=1_000)
    assert assess_contest(listing).accepted
    strict = ContestPolicy(max_favourites=5)
    assert not assess_contest(listing, strict).accepted


# ---------------------------------------------------------------- properties


@given(
    favourites=st.integers(min_value=0, max_value=10_000),
    views=st.integers(min_value=0, max_value=10_000),
)
def test_a_verdict_is_never_both_accepted_and_reasoned(favourites: int, views: int) -> None:
    verdict = assess_contest(_listing(favourites=favourites, views=views))
    assert verdict.accepted == (verdict.reasons == ())


@given(favourites=st.integers(min_value=0, max_value=10_000))
def test_raising_the_cap_never_rejects_more(favourites: int) -> None:
    """Monotonicity: a looser policy cannot reject something a tighter one accepted.

    Worth a property test rather than an example because the two rules interact --
    it would be easy to write a save-rate floor that fires only under a high cap.
    """
    listing = _listing(favourites=favourites, views=100)
    loose = assess_contest(listing, ContestPolicy(max_favourites=10_001))
    tight = assess_contest(listing, ContestPolicy(max_favourites=1))
    assert loose.accepted or not tight.accepted
