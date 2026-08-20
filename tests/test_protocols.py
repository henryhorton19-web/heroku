"""Protocol conformance.

Each fake is assigned to a protocol-typed variable, so `mypy --strict` checks the
structural match at type-check time; the runtime isinstance assertions catch the
same thing if the fakes are ever changed without running mypy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from arb.protocols import BuyVenue, CompSource, FeeModel, SellVenue

if TYPE_CHECKING:
    from collections.abc import Sequence

    from arb.models import CompQuery, Listing, ListingDraft, ListingFilter, SoldObservation


class FakeFeeModel:
    @property
    def version(self) -> str:
        return "test-0000"

    def fees_pence(self, price_pence: int, qty: int = 1) -> int:
        return price_pence * qty // 10


class FakeCompSource:
    @property
    def name(self) -> str:
        return "fake-comps"

    def sold_comps(self, query: CompQuery) -> Sequence[SoldObservation]:
        del query
        return []


class FakeBuyVenue:
    @property
    def name(self) -> str:
        return "fake-buy"

    def search(self, listing_filter: ListingFilter) -> Sequence[Listing]:
        del listing_filter
        return []


class FakeSellVenue:
    @property
    def name(self) -> str:
        return "fake-sell"

    def fee_model(self) -> FeeModel:
        return FakeFeeModel()

    def create_listing(self, draft: ListingDraft) -> str:
        del draft
        return "listing-1"

    def reprice(self, external_id: str, price_pence: int) -> None:
        del external_id, price_pence


def test_fee_model_conforms() -> None:
    model: FeeModel = FakeFeeModel()
    assert isinstance(model, FeeModel)
    assert model.version


def test_comp_source_conforms() -> None:
    source: CompSource = FakeCompSource()
    assert isinstance(source, CompSource)


def test_buy_venue_conforms() -> None:
    venue: BuyVenue = FakeBuyVenue()
    assert isinstance(venue, BuyVenue)


def test_sell_venue_conforms() -> None:
    venue: SellVenue = FakeSellVenue()
    assert isinstance(venue, SellVenue)
    assert isinstance(venue.fee_model(), FeeModel)


def test_fee_model_carries_qty_from_day_one() -> None:
    """The bundle seam. Wholesale economics is qty=N, not a refactor."""
    model = FakeFeeModel()
    assert model.fees_pence(1000) == 100
    assert model.fees_pence(1000, qty=3) == 300


def test_a_venue_can_hold_several_roles() -> None:
    """eBay is a CompSource and a SellVenue; Vinted is a BuyVenue and a SellVenue.
    The protocols are split so that is expressible without inheritance."""

    class DualRole(FakeCompSource, FakeSellVenue):
        pass

    dual = DualRole()
    assert isinstance(dual, CompSource)
    assert isinstance(dual, SellVenue)
