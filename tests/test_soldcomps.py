"""SoldComps adapter, exercised against the documented response shape via respx.
No test in this file reaches the network."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from arb.comps.soldcomps import (
    BASE_URL,
    MAX_COUNT,
    QuotaExceededError,
    RateLimitedError,
    SoldCompsClient,
    SoldCompsError,
    parse_response,
)
from arb.comps.valuation import value
from arb.models import CompQuery, SoldObservation
from tests.conftest import FIXTURES

FIXTURE_DIR = FIXTURES / "soldcomps"


def _payload() -> object:
    return json.loads((FIXTURE_DIR / "scrape_uk.json").read_text(encoding="utf-8"))


def _obs() -> list[SoldObservation]:
    return parse_response(_payload(), brand_norm="Nike", size_norm="9")


# ------------------------------------------------------------------ parsing


def test_parses_the_documented_shape() -> None:
    assert len(_obs()) == 4  # five rows in, one dropped for being USD


def test_non_gbp_rows_are_dropped_not_converted() -> None:
    """A USD comp in a GBP valuation is a wrong answer wearing the right units."""
    titles = [o.title_norm for o in _obs()]
    assert not any("us 10" in t for t in titles)


def test_decimal_strings_become_integer_pence() -> None:
    prices = {o.price_pence for o in _obs()}
    assert 4499 in prices
    assert all(isinstance(p, int) for p in prices)


def test_best_offer_rows_are_flagged_as_upper_bounds() -> None:
    """The row that sold at 'best offer accepted' carries the LISTED price."""
    flagged = [o for o in _obs() if o.price_is_upper_bound]
    assert len(flagged) == 1
    assert flagged[0].price_pence == 8900


def test_a_lone_upper_bound_row_is_also_caught_by_outlier_trimming() -> None:
    """Belt and braces, and worth knowing the limit of the braces.

    Here the 89.00 Best Offer row is extreme enough that Tukey trimming would drop it
    even if the flag did not. That is luck, not design -- it only holds while such
    sales are rare. See test_valuation for the case where they are not."""
    honest = value(_obs(), min_comp_n=3, match_confidence=0.9)
    naive = value(_obs(), min_comp_n=3, match_confidence=0.9, include_upper_bound_prices=True)
    assert honest is not None
    assert naive is not None
    assert naive.est_p60_pence == honest.est_p60_pence


def test_missing_shipping_is_none_not_zero() -> None:
    """'Free' is 0.00 and 'unknown' is null. Collapsing them would understate cost."""
    by_price = {o.price_pence: o for o in _obs()}
    assert by_price[4750].ship_pence is None
    assert by_price[4499].ship_pence == 395


def test_ended_at_is_timezone_aware() -> None:
    sold = [o.sold_at for o in _obs() if o.sold_at]
    assert sold
    assert all(d.tzinfo is not None for d in sold)


def test_listed_at_is_always_none() -> None:
    """A property of the source, not an omission: the eBay sold endpoint has no
    listing-start date, so days_to_sell cannot be derived from a comp alone."""
    assert all(o.listed_at is None for o in _obs())
    assert all(o.days_to_sell is None for o in _obs())


def test_rows_without_a_title_or_price_are_dropped() -> None:
    payload = {"items": [{"title": None, "soldPrice": "10.00"}, {"title": "x", "soldPrice": None}]}
    assert parse_response(payload, brand_norm="nike") == []


def test_empty_response_parses_to_nothing() -> None:
    assert parse_response({"items": []}, brand_norm="nike") == []


# ------------------------------------------------------------------ transport


@respx.mock
def test_search_sends_key_and_uk_site() -> None:
    route = respx.get(f"{BASE_URL}/v1/scrape").mock(
        return_value=httpx.Response(200, json=_payload())
    )
    client = SoldCompsClient("sc_test", client=httpx.Client(base_url=BASE_URL))
    client.raw_search("nike air max 90")
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sc_test"
    assert "ebaySite=ebay.co.uk" in str(request.url)
    assert "exactMatch=true" in str(request.url)


@respx.mock
def test_sold_comps_returns_observations() -> None:
    respx.get(f"{BASE_URL}/v1/scrape").mock(return_value=httpx.Response(200, json=_payload()))
    client = SoldCompsClient("sc_test", client=httpx.Client(base_url=BASE_URL))
    query = CompQuery(brand_norm="nike", title_norm="air max 90", size_norm="9")
    assert len(client.sold_comps(query)) == 4


@respx.mock
def test_quota_exceeded_is_its_own_error() -> None:
    """Must never be retried: Retry-After on a quota response can be days."""
    body = json.loads((FIXTURE_DIR / "429_quota.json").read_text(encoding="utf-8"))
    respx.get(f"{BASE_URL}/v1/scrape").mock(
        return_value=httpx.Response(429, json=body, headers={"Retry-After": "86400"})
    )
    client = SoldCompsClient("sc_test", client=httpx.Client(base_url=BASE_URL))
    with pytest.raises(QuotaExceededError) as excinfo:
        client.raw_search("nike")
    assert excinfo.value.reset_at is not None
    assert not isinstance(excinfo.value, RateLimitedError)


@respx.mock
def test_rate_limited_carries_a_retry_delay() -> None:
    body = json.loads((FIXTURE_DIR / "429_rate.json").read_text(encoding="utf-8"))
    respx.get(f"{BASE_URL}/v1/scrape").mock(return_value=httpx.Response(429, json=body))
    client = SoldCompsClient("sc_test", client=httpx.Client(base_url=BASE_URL))
    with pytest.raises(RateLimitedError) as excinfo:
        client.raw_search("nike")
    assert excinfo.value.retry_after == 3


@respx.mock
def test_rate_limit_falls_back_to_the_header() -> None:
    respx.get(f"{BASE_URL}/v1/scrape").mock(
        return_value=httpx.Response(
            429, json={"code": "rate_limited"}, headers={"Retry-After": "7"}
        )
    )
    client = SoldCompsClient("sc_test", client=httpx.Client(base_url=BASE_URL))
    with pytest.raises(RateLimitedError) as excinfo:
        client.raw_search("nike")
    assert excinfo.value.retry_after == 7


@respx.mock
def test_upstream_failures_raise() -> None:
    respx.get(f"{BASE_URL}/v1/scrape").mock(return_value=httpx.Response(502, text="blocked"))
    client = SoldCompsClient("sc_test", client=httpx.Client(base_url=BASE_URL))
    with pytest.raises(SoldCompsError, match="502"):
        client.raw_search("nike")


def test_count_above_the_documented_ceiling_is_refused() -> None:
    """The API caps at 200. The build plan said 240; the plan was wrong."""
    with pytest.raises(ValueError, match=r"1\.\.200"):
        SoldCompsClient("sc_test", count=MAX_COUNT + 1)
