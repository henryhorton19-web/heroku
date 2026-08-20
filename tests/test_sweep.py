"""The active-listing sweep — the path that closes P2.

Two properties carry the whole design, and both are ways of refusing to produce a
number that would look fine and be wrong:

**A disappearance is not a sale.** Listings leave search when they sell, when they end
unsold, and when the seller delists. Counting every disappearance as a sale would
understate time-to-sell badly and confidently.

**A listing still live is censored, not a zero-day sale.** You know it has lasted at
least N days, not how long it will last, and it must not reach a fit.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from arb.money import CurrencyMismatchError
from arb.sourcing.sweep import (
    diff_actives,
    parse_active_listings,
    resolve_disappearances,
)

T0 = datetime(2026, 7, 1, tzinfo=UTC)
T30 = datetime(2026, 7, 31, tzinfo=UTC)


def _payload(*entries: dict[str, object]) -> dict[str, object]:
    return {
        "href": "/buy/browse/v1/item_summary/search",
        "total": len(entries),
        "itemSummaries": list(entries),
    }


def _item(
    legacy: str = "1234567890",
    created: str = "2026-07-01T09:00:00.000Z",
    value: str = "42.00",
    currency: str = "GBP",
) -> dict[str, object]:
    return {
        "itemId": f"v1|{legacy}|0",
        "legacyItemId": legacy,
        "title": "Nike Air Max 90 White",
        "itemCreationDate": created,
        "price": {"value": value, "currency": currency},
        "condition": "Pre-owned",
    }


# ---------------------------------------------------------------- parsing


def test_parse_reads_the_verified_browse_fields() -> None:
    listings = parse_active_listings(_payload(_item()))
    assert len(listings) == 1
    assert listings[0].created_at == datetime(2026, 7, 1, 9, 0, tzinfo=UTC)
    assert listings[0].price_pence == 4200


def test_the_legacy_id_is_kept_not_the_restful_one() -> None:
    """Sold data keys on the bare number. Matching `v1|1234|0` against it corroborates
    nothing, silently, and the sweep would report every disappearance as unconfirmed
    forever while appearing to work."""
    listings = parse_active_listings(_payload(_item(legacy="9988776655")))
    assert listings[0].external_id == "9988776655"
    assert "|" not in listings[0].external_id


def test_a_listing_without_a_creation_date_is_dropped() -> None:
    """It cannot be timed. Carrying it with a guessed start would put a fabricated
    duration into the one dataset that closes P2."""
    item = _item()
    del item["itemCreationDate"]
    assert parse_active_listings(_payload(item)) == ()


def test_a_naive_creation_date_is_dropped() -> None:
    assert parse_active_listings(_payload(_item(created="2026-07-01T09:00:00"))) == ()


def test_an_unparseable_creation_date_is_dropped() -> None:
    assert parse_active_listings(_payload(_item(created="last Tuesday"))) == ()


def test_a_non_gbp_listing_is_refused_not_converted() -> None:
    with pytest.raises(CurrencyMismatchError):
        parse_active_listings(_payload(_item(currency="USD")))


def test_a_malformed_payload_is_empty_not_an_exception() -> None:
    assert parse_active_listings({}) == ()
    assert parse_active_listings([]) == ()
    assert parse_active_listings({"itemSummaries": ["nonsense"]}) == ()


# ---------------------------------------------------------------- diffing


def test_the_diff_separates_new_surviving_and_gone() -> None:
    diff = diff_actives(known=["a", "b", "c"], observed=["b", "c", "d"])
    assert diff.appeared == ("d",)
    assert diff.still_active == ("b", "c")
    assert diff.disappeared == ("a",)


def test_the_diff_output_is_sorted_and_therefore_stable() -> None:
    """Set iteration order would make a scheduler's logs and a test's assertions both
    flap for no reason."""
    diff = diff_actives(known=[], observed=["z", "a", "m"])
    assert diff.appeared == ("a", "m", "z")


def test_a_first_sweep_is_all_appearances() -> None:
    diff = diff_actives(known=[], observed=["a", "b"])
    assert diff.appeared == ("a", "b")
    assert diff.disappeared == ()


def test_an_unchanged_market_produces_no_events() -> None:
    diff = diff_actives(known=["a"], observed=["a"])
    assert diff.appeared == ()
    assert diff.disappeared == ()
    assert diff.still_active == ("a",)


# ---------------------------------------------------------------- corroboration


def test_a_disappearance_with_a_matching_sale_is_measured() -> None:
    resolution = resolve_disappearances(["a"], {"a": T0}, {"a": T30})
    assert resolution.sold == (("a", 30),)
    assert resolution.unconfirmed == ()


def test_a_disappearance_without_a_sale_is_not_counted_as_one() -> None:
    """The load-bearing test. Fashion runs on 30-day cycles and ended-unsold is
    ordinary; treating every disappearance as a sale would understate time-to-sell
    badly and confidently."""
    resolution = resolve_disappearances(["a"], {"a": T0}, {})
    assert resolution.sold == ()
    assert resolution.unconfirmed == ("a",)


def test_a_sale_with_no_known_start_is_not_measured() -> None:
    """Both ends or no measurement. A substituted start point is a fabricated
    duration wearing a real sale's authority."""
    resolution = resolve_disappearances(["a"], {}, {"a": T30})
    assert resolution.sold == ()
    assert resolution.unconfirmed == ("a",)


def test_a_same_day_sale_measures_zero_not_one() -> None:
    """`capital_velocity` floors its divisor at one day, which is where a same-day
    sale is stopped from looking infinitely fast. Flooring again here would turn a
    real zero-day observation into a one-day one before it reached a fit."""
    resolution = resolve_disappearances(["a"], {"a": T0}, {"a": T0})
    assert resolution.sold == (("a", 0),)


def test_a_sale_dated_before_the_listing_never_goes_negative() -> None:
    resolution = resolve_disappearances(["a"], {"a": T30}, {"a": T0})
    assert resolution.sold == (("a", 0),)


def test_still_active_listings_never_reach_the_resolver() -> None:
    """Censored data. A live listing has lasted at least N days; that is not a
    duration and must not be fitted as one. The resolver only sees disappearances,
    so the exclusion is structural rather than a filter someone could forget."""
    diff = diff_actives(known=["live", "gone"], observed=["live"])
    resolution = resolve_disappearances(diff.disappeared, {"live": T0, "gone": T0}, {"gone": T30})
    assert [item for item, _ in resolution.sold] == ["gone"]


def test_the_confirmation_rate_is_reported() -> None:
    """A rate collapsing toward zero means either the id formats stopped matching or
    the market stopped clearing, and those need very different responses."""
    resolution = resolve_disappearances(["a", "b", "c", "d"], dict.fromkeys("abcd", T0), {"a": T30})
    assert resolution.confirmation_rate == 0.25


def test_no_disappearances_is_a_zero_rate_not_a_division_error() -> None:
    assert resolve_disappearances([], {}, {}).confirmation_rate == 0.0
