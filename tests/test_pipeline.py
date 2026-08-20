"""Comps service and pipeline. The quota behaviour is the important part: on a
100-request month, a scan that fetches per listing spends the budget in one run."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from arb.comps.cache import payloads_for
from arb.comps.fees import load_fee_table
from arb.comps.service import CompsService
from arb.comps.soldcomps import QuotaExceededError, parse_response
from arb.models import Attributes, CompQuery, Listing, ListingFilter, Venue
from arb.pipeline import ScanDeps, ScanSettings, query_for, run_scan
from arb.sourcing.rank import VelocityPolicy
from tests.conftest import FIXTURES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from arb.models import SoldObservation
    from arb.sourcing.scanner import ScanOutcome

T0 = datetime(2026, 8, 1, tzinfo=UTC)
FEES = load_fee_table(Path(__file__).resolve().parent.parent / "src/arb/data/fees/ebay_uk.yaml")
PAYLOAD = json.loads((FIXTURES / "soldcomps" / "scrape_uk.json").read_text(encoding="utf-8"))
QUERY = CompQuery(brand_norm="nike", title_norm="nike air max 90 white", size_norm="9")


class _StubSource:
    """Counts calls, so cache behaviour is observable rather than assumed."""

    def __init__(self, *, quota_gone: bool = False) -> None:
        self.calls = 0
        self._quota_gone = quota_gone

    @property
    def name(self) -> str:
        return "stub"

    def raw_search(self, keyword: str, *, page: int = 1) -> object:
        del keyword, page
        self.calls += 1
        if self._quota_gone:
            reset_at = "2026-09-01"
            raise QuotaExceededError(reset_at)
        return PAYLOAD

    def parse(self, payload: object, query: CompQuery) -> Sequence[SoldObservation]:
        return parse_response(payload, brand_norm=query.brand_norm, size_norm=query.size_norm)


def _service(session: Session, source: _StubSource, days: int = 7) -> CompsService:
    return CompsService(source, session, freshness=timedelta(days=days))


# ------------------------------------------------------------------ caching


def test_first_lookup_fetches(session: Session) -> None:
    source = _StubSource()
    assert _service(session, source).comps_for(QUERY)
    assert source.calls == 1


def test_repeat_lookup_is_served_from_cache(session: Session) -> None:
    """This is what makes a 100-request month workable at all."""
    source = _StubSource()
    service = _service(session, source)
    service.comps_for(QUERY)
    service.comps_for(QUERY)
    assert source.calls == 1
    assert service.stats.cache_hits == 1
    assert service.stats.fetches == 1


def test_stale_cache_triggers_a_refetch(session: Session) -> None:
    source = _StubSource()
    _service(session, source).comps_for(QUERY)
    rows = payloads_for(session, QUERY)
    rows[0].fetched_at = T0 - timedelta(days=400)
    session.flush()
    _service(session, source, days=1).comps_for(QUERY)
    assert source.calls == 2


def test_the_raw_payload_is_cached_before_parsing(session: Session) -> None:
    """If the parser is wrong the bytes are still there to re-parse."""
    _service(session, _StubSource()).comps_for(QUERY)
    rows = payloads_for(session, QUERY)
    assert len(rows) == 1
    assert json.loads(rows[0].payload)["items"]


def test_different_queries_each_cost_a_fetch(session: Session) -> None:
    source = _StubSource()
    service = _service(session, source)
    service.comps_for(QUERY)
    service.comps_for(CompQuery(brand_norm="adidas", title_norm="samba og"))
    assert source.calls == 2


# ------------------------------------------------------------------ quota


def test_quota_exhaustion_returns_empty_rather_than_raising(session: Session) -> None:
    service = _service(session, _StubSource(quota_gone=True))
    assert service.comps_for(QUERY) == []
    assert service.stats.quota_exhausted


def test_quota_exhaustion_stops_further_fetches(session: Session) -> None:
    """Retrying a quota_exceeded is pointless -- Retry-After can be days."""
    source = _StubSource(quota_gone=True)
    service = _service(session, source)
    for _ in range(5):
        service.comps_for(CompQuery(brand_norm="nike", title_norm=f"item {_}"))
    assert source.calls == 1


def test_cached_comps_still_serve_after_the_quota_is_gone(session: Session) -> None:
    """A partially-priced buy list beats none, as long as you are told it is partial."""
    _service(session, _StubSource()).comps_for(QUERY)
    exhausted = _service(session, _StubSource(quota_gone=True))
    assert exhausted.comps_for(QUERY)


# ------------------------------------------------------------------ pipeline


def _listing(external_id: str, price: int, title: str = "nike air max 90 white") -> Listing:
    return Listing(
        venue=Venue.VINTED,
        external_id=external_id,
        price_pence=price,
        attrs=Attributes(brand_norm="nike", title_norm=title, size_norm="9"),
        first_seen=T0,
        last_seen=T0,
    )


class _StubVenue:
    def __init__(self, listings: list[Listing]) -> None:
        self._listings = listings

    @property
    def name(self) -> str:
        return "stub-venue"

    def search(self, listing_filter: ListingFilter) -> Sequence[Listing]:
        del listing_filter
        return self._listings


def _run(session: Session, listings: list[Listing]) -> ScanOutcome:
    deps = ScanDeps(
        buy_venue=_StubVenue(listings),
        comps=_service(session, _StubSource()),
        fee_model=FEES,
    )
    return run_scan(
        deps,
        ListingFilter(query="nike"),
        T0,
        ScanSettings(policy=VelocityPolicy.ASSUME_DEFAULT),
    )


def test_comp_query_omits_condition() -> None:
    """Narrowing comps to an exact condition band usually empties the set, and the
    valuation floor then refuses everything. Condition belongs in the discount."""
    listing = _listing("1", 1200)
    assert query_for(listing).condition_band is None


def test_pipeline_prices_and_ranks(session: Session) -> None:
    outcome = _run(session, [_listing("1", 1200)])
    assert len(outcome.ranked) == 1


def test_pipeline_marks_unpriceable_listings_rather_than_dropping_them(
    session: Session,
) -> None:
    """A thin comp database must not look like a quiet market."""
    outcome = _run(session, [_listing("1", 1200, "obscure brand nobody sells")])
    assert outcome.ranked == ()
    assert len(outcome.unpriceable) == 1


def test_pipeline_reuses_comps_across_identical_listings(session: Session) -> None:
    """Two of the same item in one search is one comp fetch, not two."""
    service = _service(session, _StubSource())
    deps = ScanDeps(
        buy_venue=_StubVenue([_listing("1", 1200), _listing("2", 1300)]),
        comps=service,
        fee_model=FEES,
    )
    run_scan(deps, ListingFilter(query="nike"), T0, ScanSettings())
    assert service.stats.fetches == 1
    assert service.stats.cache_hits == 1


def test_quality_rejects_never_cost_a_comps_request(session: Session) -> None:
    """The efficiency bug this guards against is expensive and invisible: on a
    100-request month, fetching comps for listings the filter was always going to
    reject burns the budget on items you had already decided against."""
    source = _StubSource()
    deps = ScanDeps(
        buy_venue=_StubVenue(
            [
                _listing("good", 1200),
                _listing("bad", 900, "nike air max 90 white stained sole"),
                _listing("worse", 800, "nike air max 90 ripped upper"),
            ]
        ),
        comps=_service(session, source),
        fee_model=FEES,
    )
    outcome = run_scan(deps, ListingFilter(query="nike"), T0, ScanSettings())
    assert source.calls == 1
    assert len(outcome.rejected_quality) == 2


def test_quality_rejects_are_still_classified_as_quality_not_unpriceable(
    session: Session,
) -> None:
    """Pre-filtering must not change what the scan reports. `scan` stays the
    authoritative classifier; the pipeline only avoids the spend."""
    deps = ScanDeps(
        buy_venue=_StubVenue([_listing("bad", 900, "nike air max 90 white stained sole")]),
        comps=_service(session, _StubSource()),
        fee_model=FEES,
    )
    outcome = run_scan(deps, ListingFilter(query="nike"), T0, ScanSettings())
    assert len(outcome.rejected_quality) == 1
    assert outcome.unpriceable == ()
    assert outcome.rejected_quality[0].reason.startswith("quality:")


def test_contested_listings_never_cost_a_comps_request(session: Session) -> None:
    """Same waste as a quality reject, in a different disguise. Pricing an item we
    expect to lose the race for spends a request from a 100-per-month budget on a
    trade that was never going to happen."""
    contested = Listing(
        venue=Venue.VINTED,
        external_id="1",
        price_pence=1200,
        attrs=Attributes(brand_norm="nike", title_norm="nike air max 90 white", size_norm="9"),
        favourites=400,
        views=500,
        first_seen=T0,
        last_seen=T0,
    )
    source = _StubSource()
    deps = ScanDeps(
        buy_venue=_StubVenue([contested]),
        comps=_service(session, source),
        fee_model=FEES,
    )
    outcome = run_scan(deps, ListingFilter(query="nike"), T0, ScanSettings())
    assert source.calls == 0
    assert len(outcome.rejected_contest) == 1


def test_the_pre_filter_does_not_change_the_classification(session: Session) -> None:
    """`run_scan` pre-filters to save quota, but `scan` stays the authoritative
    classifier. A contested listing must land in `rejected_contest` either way --
    the pre-filter changes only the spend, never the verdict."""
    contested = Listing(
        venue=Venue.VINTED,
        external_id="1",
        price_pence=1200,
        attrs=Attributes(brand_norm="nike", title_norm="nike air max 90 white", size_norm="9"),
        favourites=400,
        views=500,
        first_seen=T0,
        last_seen=T0,
    )
    outcome = _run(session, [contested])
    assert outcome.ranked == ()
    assert outcome.unpriceable == ()
    assert outcome.rejected_contest[0].reason.startswith("contest:")
