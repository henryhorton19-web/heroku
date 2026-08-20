"""Model invariants. These are the guardrails that stop bad data reaching the ledger."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from arb.models import (
    VINTED_STATUS_TO_BAND,
    Attributes,
    CompQuery,
    ConditionBand,
    Decision,
    DecisionMode,
    DecisionOutcome,
    Listing,
    ListingDraft,
    ListingFilter,
    SoldObservation,
    Valuation,
    Venue,
    utcnow,
)

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _attrs() -> Attributes:
    return Attributes(brand_norm="nike", title_norm="nike air max 90", size_norm="M")


# ------------------------------------------------------------------ decisions


def test_skip_requires_a_reason() -> None:
    """The load-bearing invariant. Without skip_reason there is no way to later
    measure whether an automated buyer would have beaten the manual one."""
    with pytest.raises(ValidationError, match="skip_reason is required"):
        Decision(
            opportunity_id=1,
            mode=DecisionMode.MANUAL,
            outcome=DecisionOutcome.SKIPPED,
            decided_at=T0,
        )


@pytest.mark.parametrize("reason", ["", "   "])
def test_blank_skip_reason_is_not_a_reason(reason: str) -> None:
    with pytest.raises(ValidationError, match="skip_reason is required"):
        Decision(
            opportunity_id=1,
            mode=DecisionMode.MANUAL,
            outcome=DecisionOutcome.SKIPPED,
            skip_reason=reason,
            decided_at=T0,
        )


def test_skip_cannot_have_spend() -> None:
    with pytest.raises(ValidationError, match="cannot have spend"):
        Decision(
            opportunity_id=1,
            mode=DecisionMode.MANUAL,
            outcome=DecisionOutcome.SKIPPED,
            skip_reason="damaged in photos",
            decided_at=T0,
            spend_pence=1200,
        )


def test_buy_cannot_carry_a_skip_reason() -> None:
    with pytest.raises(ValidationError, match="must be empty"):
        Decision(
            opportunity_id=1,
            mode=DecisionMode.MANUAL,
            outcome=DecisionOutcome.BOUGHT,
            skip_reason="changed my mind",
            decided_at=T0,
            spend_pence=1200,
        )


def test_valid_skip_and_buy() -> None:
    skip = Decision(
        opportunity_id=1,
        mode=DecisionMode.MANUAL,
        outcome=DecisionOutcome.SKIPPED,
        skip_reason="bundle-ambiguous listing",
        decided_at=T0,
    )
    buy = Decision(
        opportunity_id=2,
        mode=DecisionMode.MANUAL,
        outcome=DecisionOutcome.BOUGHT,
        decided_at=T0,
        spend_pence=1450,
    )
    assert skip.skip_reason
    assert buy.spend_pence == 1450


# ------------------------------------------------------------------ money


def _listing_payload(**overrides: object) -> dict[str, object]:
    """Build a Listing payload as a dict so tests can supply values the constructor
    is correctly typed to forbid. `model_validate` is the runtime boundary, and these
    tests are about what happens at that boundary."""
    payload: dict[str, object] = {
        "venue": Venue.VINTED,
        "external_id": "1",
        "price_pence": 100,
        "attrs": _attrs(),
        "first_seen": T0,
        "last_seen": T0,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("field", ["price_pence", "total_pence"])
def test_prices_cannot_be_negative(field: str) -> None:
    with pytest.raises(ValidationError):
        Listing.model_validate(_listing_payload(**{field: -1}))


def test_float_prices_are_rejected() -> None:
    """Strict integer pence. A float price is how rounding error enters a ledger."""
    with pytest.raises(ValidationError):
        Listing.model_validate(_listing_payload(price_pence=12.50))


# ------------------------------------------------------------------ ordering


def test_last_seen_cannot_precede_first_seen() -> None:
    with pytest.raises(ValidationError, match="precedes"):
        Listing(
            venue=Venue.VINTED,
            external_id="1",
            price_pence=100,
            attrs=_attrs(),
            first_seen=T0,
            last_seen=T0 - timedelta(days=1),
        )


def test_inverted_percentiles_are_rejected() -> None:
    with pytest.raises(ValidationError, match="percentiles inverted"):
        Valuation(
            est_p25_pence=5000,
            est_p60_pence=4000,
            comp_n=10,
            est_confidence=0.8,
            match_confidence=0.9,
        )


def test_inverted_price_filter_is_rejected() -> None:
    with pytest.raises(ValidationError, match="below min_price_pence"):
        ListingFilter(min_price_pence=5000, max_price_pence=1000)


@pytest.mark.parametrize("bad", [-0.1, 1.1])
def test_confidence_is_bounded(bad: float) -> None:
    with pytest.raises(ValidationError):
        Valuation(
            est_p25_pence=1000,
            est_p60_pence=2000,
            comp_n=5,
            est_confidence=bad,
            match_confidence=0.5,
        )


# ------------------------------------------------------------------ comp cache key


def test_query_hash_is_stable_across_construction() -> None:
    """The hash is the append-only cache key. If it drifts between releases the
    accumulated trend series fragments, and that series cannot be rebuilt."""
    a = CompQuery(brand_norm="nike", title_norm="air max 90", size_norm="M")
    b = CompQuery(title_norm="air max 90", size_norm="M", brand_norm="nike")
    assert a.query_hash == b.query_hash
    assert len(a.query_hash) == 64


def test_query_hash_is_pinned() -> None:
    """Pinned to a literal. Changing the canonical form is a cache-invalidating
    event and must be a conscious decision, not an accident."""
    q = CompQuery(brand_norm="nike", title_norm="air max 90", size_norm="M")
    assert q.query_hash == "11bb9fa57af4a3db5c7e7192eb0e24afacc2dd576f718e0591ca51a01b286f2b"


def test_query_hash_distinguishes_fields() -> None:
    base = CompQuery(brand_norm="nike", title_norm="air max 90")
    assert base.query_hash != CompQuery(brand_norm="nike", title_norm="air max 91").query_hash
    assert (
        base.query_hash
        != CompQuery(brand_norm="nike", title_norm="air max 90", size_norm="M").query_hash
    )
    assert (
        base.query_hash
        != CompQuery(
            brand_norm="nike", title_norm="air max 90", condition_band=ConditionBand.GOOD
        ).query_hash
    )


# ------------------------------------------------------------------ misc


@given(
    st.integers(min_value=0, max_value=400),
    st.integers(min_value=0, max_value=400),
)
def test_days_to_sell_is_never_negative(listed_offset: int, sold_offset: int) -> None:
    obs = SoldObservation(
        brand_norm="nike",
        title_norm="air max",
        size_norm="M",
        price_pence=4500,
        listed_at=T0 + timedelta(days=listed_offset),
        sold_at=T0 + timedelta(days=sold_offset),
    )
    days = obs.days_to_sell
    assert days is not None
    assert days >= 0


def test_days_to_sell_is_none_without_both_timestamps() -> None:
    obs = SoldObservation(
        brand_norm="nike", title_norm="air max", size_norm="M", price_pence=4500, sold_at=T0
    )
    assert obs.days_to_sell is None


def test_vinted_status_map_covers_every_band() -> None:
    """Vinted exposes exactly five statuses. If upstream adds one, the assertion on
    count fails loudly rather than mapping it to None somewhere downstream."""
    assert set(VINTED_STATUS_TO_BAND.values()) == set(ConditionBand)
    assert sorted(VINTED_STATUS_TO_BAND) == [1, 2, 3, 4, 6]


def test_models_are_frozen() -> None:
    attrs = _attrs()
    with pytest.raises(ValidationError):
        attrs.brand_norm = "adidas"


def test_extra_fields_are_rejected() -> None:
    """A misspelled field name should fail loudly rather than be silently dropped."""
    with pytest.raises(ValidationError):
        Attributes.model_validate(
            {"brand_norm": "nike", "title_norm": "t", "size_norm": "M", "colour": "red"}
        )


def test_listing_draft_enforces_title_limit() -> None:
    """eBay truncates at 80 characters; better to fail locally than publish a
    silently clipped title."""
    with pytest.raises(ValidationError):
        ListingDraft(
            title="x" * 81,
            description="d",
            category_id="57988",
            price_pence=1000,
            size="M",
            condition_band=ConditionBand.GOOD,
            brand="Nike",
        )


def test_utcnow_is_aware() -> None:
    assert utcnow().tzinfo is not None
